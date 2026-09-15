"""Real static-image MP4 encode, independent probe and complete stream decode."""

from fractions import Fraction
from hashlib import file_digest
import json
from pathlib import Path

from app.domain.render_result import RenderedVideo, render_request
from app.runtime.media_process import MediaProcess, RenderCanceled
from app.storage.paths import contained_path


def checksum(path):
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


class FFmpegRenderer:
    def __init__(self, ffmpeg, ffprobe, *, process=None):
        # Executables are trusted composition, never taken from job JSON.
        self.ffmpeg, self.ffprobe = Path(ffmpeg).resolve(strict=True), Path(ffprobe).resolve(strict=True)
        self.process = process if process is not None else MediaProcess()

    def identity(self):
        return {"provider": "ffmpeg", "adapter": "static-mp4-v1",
                "ffmpeg_sha256": checksum(self.ffmpeg), "ffprobe_sha256": checksum(self.ffprobe)}

    async def render(self, timeline, root, *, canceled, progress):
        render_request(timeline, self.identity())
        root = Path(root)
        command = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-n"]
        filters, videos, audios = [], [], []
        for i, clip in enumerate(timeline.clips):
            # Paths have generated names and stay under the bound private work root.
            image = contained_path(root, f"image-{i}.png")
            audio = contained_path(root, f"audio-{i}.wav")
            command.extend(("-loop", "1", "-framerate", "25", "-i", image.name, "-i", audio.name))
            fit = ("scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:black"
                   if timeline.fit_policy == "fit" else
                   "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720")
            filters.append(f"[{2*i}:v]{fit},setsar=1,format=yuv420p,trim=end_frame={clip.duration_frames},setpts=PTS-STARTPTS[v{i}]")
            span = clip.media.audio
            filters.append(f"[{2*i+1}:a]atrim=start_sample={span.start_sample}:end_sample={span.end_sample},"
                           f"asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono[a{i}]")
            videos.append(f"[v{i}]")
            audios.append(f"[a{i}]")
        filters.append("".join(videos) + f"concat=n={len(videos)}:v=1:a=0[v]")
        filters.append("".join(audios) + f"concat=n={len(audios)}:v=0:a=1[a]")
        script = contained_path(root, "filters.txt")
        script.write_text(";\n".join(filters), encoding="utf-8")
        output = contained_path(root, "render.mp4")
        command.extend(("-filter_complex_script", script.name, "-filter_complex_threads", "1",
                        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-threads", "2", "-pix_fmt", "yuv420p", "-r", "25", "-c:a", "aac", "-b:a", "128k",
                        "-ar", "48000", "-ac", "1", "-movflags", "+faststart", "-progress", "pipe:1", output.name))
        progress("encoding", 0, timeline.total_frames)

        def line(value):
            if value.startswith("out_time_us="):
                raw = value.partition("=")[2]
                if raw.lstrip("-").isdigit():
                    progress("encoding", max(0, min(timeline.total_frames, int(raw) * 25 // 1000000)), timeline.total_frames)

        await self.process.run(command, cwd=root, canceled=canceled, on_line=line)
        return await self.validate(timeline, root, canceled=canceled, progress=progress)

    async def validate(self, timeline, root, *, canceled, progress):
        path = contained_path(root, "render.mp4")
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("Renderer did not produce MP4 bytes.")
        before, size = checksum(path), path.stat().st_size
        progress("probing", 0, 1)
        raw = await self.process.run([self.ffprobe, "-v", "error", "-count_frames", "-show_streams", "-show_format",
                                      "-of", "json", path.name], cwd=root, canceled=canceled)
        data = json.loads(raw)
        streams = data["streams"]
        if len(streams) != 2 or "mp4" not in data["format"]["format_name"].split(","):
            raise ValueError("Expected exactly one video and one audio stream in MP4.")
        video = next(s for s in streams if s["codec_type"] == "video")
        audio = next(s for s in streams if s["codec_type"] == "audio")
        vd, ad = Fraction(video["duration"]), Fraction(audio["duration"])
        tolerance = Fraction(1024, 48000) + Fraction(len(timeline.clips), 48000)
        if ((video["codec_name"], video["width"], video["height"], video["pix_fmt"]) != ("h264", 1280, 720, "yuv420p")
                or Fraction(video["avg_frame_rate"]) != 25 or int(video["nb_read_frames"]) != timeline.total_frames
                or abs(vd - timeline.video_duration) > Fraction(1, 1000000)
                or (audio["codec_name"], int(audio["sample_rate"]), audio["channels"]) != ("aac", 48000, 1)
                or int(audio["nb_read_frames"]) <= 0 or abs(ad - timeline.duration) > tolerance):
            raise ValueError("MP4 profile, decoded frame count or duration differs from timeline.")
        progress("decoding", 0, 1)
        await self.process.run([self.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
                                "-i", path.name, "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
                               cwd=root, canceled=canceled)
        if canceled():
            raise RenderCanceled("Render canceled after validation.")
        if checksum(path) != before or path.stat().st_size != size:
            raise ValueError("MP4 changed while validating.")
        progress("validated", 1, 1)
        return RenderedVideo(timeline.id, before, size, timeline.total_frames, vd, ad)

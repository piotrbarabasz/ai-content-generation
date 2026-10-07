"""Real static-image MP4 encode, independent probe and complete stream decode."""

from fractions import Fraction
from hashlib import file_digest
import json
from pathlib import Path

from app.domain.render_result import (RenderedVideo, render_request, delivery_profile,
                                      resolve_motion, MOTION_POLICY_VERSION, LEGACY_PROFILE)
from app.domain.timeline import OutputTimebase, TimelineRevision
from app.runtime.media_process import MediaProcess, RenderCanceled
from app.storage.paths import contained_path


def checksum(path):
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


class FFmpegRenderer:
    def __init__(self, ffmpeg, ffprobe, *, process=None, proxy=False, legacy=False):
        # Executables are trusted composition, never taken from job JSON.
        self.ffmpeg, self.ffprobe = Path(ffmpeg).resolve(strict=True), Path(ffprobe).resolve(strict=True)
        self.process = process if process is not None else MediaProcess()
        self.proxy = proxy
        self.legacy = legacy

    def for_request(self, algorithm_version):
        if algorithm_version not in ("1", "2") or self.proxy:
            raise ValueError("Unsupported final render request version.")
        if self.legacy == (algorithm_version == "1"):
            return self
        return FFmpegRenderer(self.ffmpeg, self.ffprobe, process=self.process,
                              legacy=algorithm_version == "1")

    def dimensions(self, timeline):
        if self.legacy:
            return LEGACY_PROFILE, 1280, 720
        profile, width, height = delivery_profile(timeline)
        if not self.proxy:
            return profile, width, height
        proxy_width, proxy_height = (640, 360) if width >= height else (360, 640)
        return "proxy", proxy_width, proxy_height

    def identity(self):
        if self.legacy:
            return {"provider": "ffmpeg", "adapter": "static-mp4-v1",
                    "ffmpeg_sha256": checksum(self.ffmpeg), "ffprobe_sha256": checksum(self.ffprobe)}
        return {"provider": "ffmpeg", "adapter": "proxy-motion-mp4-v1" if self.proxy else "motion-mp4-v1",
                "motion_policy": MOTION_POLICY_VERSION,
                "ffmpeg_sha256": checksum(self.ffmpeg), "ffprobe_sha256": checksum(self.ffprobe)}

    async def render(self, timeline, root, *, captions=None, canceled, progress):
        profile, width, height = self.dimensions(timeline)
        if self.proxy:
            if not isinstance(timeline, TimelineRevision) or timeline.timebase != OutputTimebase(1, 25):
                raise ValueError("MP4 proxy requires a D018 timeline at 25 FPS.")
        else:
            render_request(timeline, self.identity(), captions,
                           algorithm_version="1" if self.legacy else "2")
        root = Path(root)
        command = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-n"]
        filters, videos, audios = [], [], []
        for i, clip in enumerate(timeline.clips):
            # Paths have generated names and stay under the bound private work root.
            image = contained_path(root, f"image-{i}.png")
            audio = contained_path(root, f"audio-{i}.wav")
            command.extend(("-loop", "1", "-framerate", "25", "-i", image.name, "-i", audio.name))
            if not self.legacy and clip.media.image.provenance == "motion_master":
                mode = resolve_motion(clip.media.scene_id)
                frames = clip.duration_frames
                progress_expr = f"on/{max(1, frames - 1)}"
                zoom = (f"1+0.10*{progress_expr}" if mode == "zoom_in" else
                        f"1.10-0.10*{progress_expr}" if mode == "zoom_out" else "1.10")
                max_x, max_y = f"(iw-iw/zoom)", f"(ih-ih/zoom)"
                x = (f"{max_x}*{progress_expr}" if mode == "pan_right" else
                     f"{max_x}*(1-{progress_expr})" if mode == "pan_left" else f"{max_x}/2")
                y = (f"{max_y}*{progress_expr}" if mode == "pan_down" else
                     f"{max_y}*(1-{progress_expr})" if mode == "pan_up" else f"{max_y}/2")
                motion = (f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps=25")
                filters.append(f"[{2*i}:v]{motion},setsar=1,format=yuv420p,trim=end_frame={frames},setpts=PTS-STARTPTS[v{i}]")
            else:
                fit = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                       f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
                       if timeline.fit_policy == "fit" else
                       f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                       f"crop={width}:{height}")
                filters.append(f"[{2*i}:v]{fit},setsar=1,format=yuv420p,trim=end_frame={clip.duration_frames},setpts=PTS-STARTPTS[v{i}]")
            span = clip.media.audio
            filters.append(f"[{2*i+1}:a]atrim=start_sample={span.start_sample}:end_sample={span.end_sample},"
                           f"asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono[a{i}]")
            videos.append(f"[v{i}]")
            audios.append(f"[a{i}]")
        filters.append("".join(videos) + f"concat=n={len(videos)}:v=1:a=0[v]")
        filters.append("".join(audios) + f"concat=n={len(audios)}:v=0:a=1[a]")
        video_output = "[v]"
        if captions is not None:
            caption_path = contained_path(root, "captions.ass")
            if not caption_path.is_file() or checksum(caption_path) != captions.ass_checksum:
                raise ValueError("Staged ASS captions differ from the selected caption artifact.")
            filters.append("[v]subtitles=filename='captions.ass'[vc]")
            video_output = "[vc]"
        script = contained_path(root, "filters.txt")
        script.write_text(";\n".join(filters), encoding="utf-8")
        output = contained_path(root, "render.mp4")
        command.extend(("-filter_complex_script", script.name, "-filter_complex_threads", "1",
                        "-map", video_output, "-map", "[a]", "-c:v", "libx264",
                        "-preset", "ultrafast" if self.proxy else "veryfast", "-crf", "28" if self.proxy else "20",
                        "-threads", "2", "-pix_fmt", "yuv420p", "-r", "25", "-c:a", "aac", "-b:a", "128k",
                        "-ar", "48000", "-ac", "1", "-movflags", "+faststart", "-progress", "pipe:1", output.name))
        progress("encoding", 0, timeline.total_frames)

        def line(value):
            if value.startswith("out_time_us="):
                raw = value.partition("=")[2]
                if raw.lstrip("-").isdigit():
                    progress("encoding", max(0, min(timeline.total_frames, int(raw) * 25 // 1000000)), timeline.total_frames)

        await self.process.run(command, cwd=root, canceled=canceled, on_line=line)
        return await self.validate(timeline, root, canceled=canceled, progress=progress,
                                   profile=profile, width=width, height=height)

    async def validate(self, timeline, root, *, canceled, progress, profile=None, width=None, height=None):
        if profile is None:
            profile, width, height = self.dimensions(timeline)
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
        if ((video["codec_name"], video["width"], video["height"], video["pix_fmt"])
                != ("h264", width, height, "yuv420p")
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
        return RenderedVideo(timeline.id, before, size, timeline.total_frames, vd, ad,
                             profile=profile, width=width, height=height)

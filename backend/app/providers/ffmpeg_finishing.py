"""Deterministic local cards and frame/sample-accurate post-render composition."""

from fractions import Fraction
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, __version__ as pillow_version

from app.providers.ffmpeg_render import checksum
from app.runtime.media_process import MediaProcess, RenderCanceled
from app.storage.paths import contained_path


class FFmpegFinisher:
    def __init__(self, ffmpeg, ffprobe, *, process=None):
        self.ffmpeg = Path(ffmpeg).resolve(strict=True)
        self.ffprobe = Path(ffprobe).resolve(strict=True)
        self.process = process or MediaProcess(timeout=1800)
        self.font_path = None
        for candidate in ("C:/Windows/Fonts/segoeui.ttf", "DejaVuSans.ttf"):
            try:
                font = ImageFont.truetype(candidate, 20)
                self.font_path = Path(font.path).resolve(strict=True)
                break
            except OSError:
                continue

    def identity(self):
        return {"adapter": "branding-finishing-v1", "ffmpeg_sha256": checksum(self.ffmpeg),
                "ffprobe_sha256": checksum(self.ffprobe), "pillow": pillow_version,
                "font": checksum(self.font_path) if self.font_path else "pillow-default"}

    def card(self, config, logo_path, width, height, *, outro=False):
        canvas = Image.new("RGBA", (width, height), config.background)
        draw = ImageDraw.Draw(canvas)
        scale = min(width / 1920, height / 1080)
        def font(size):
            pixels = max(10, int(size * scale))
            return ImageFont.truetype(str(self.font_path), pixels) if self.font_path else ImageFont.load_default(size=pixels)
        def text(value, y, size, color="white"):
            if not value:
                return
            face = font(size)
            while draw.textbbox((0, 0), value, font=face)[2] > width * .86 and size > 14:
                size -= 2
                face = font(size)
            if draw.textbbox((0, 0), value, font=face)[2] > width * .9:
                raise ValueError("Branding text cannot fit safely at this delivery size.")
            draw.text((width / 2, y), value, font=face, fill=color, anchor="mt")
        with Image.open(logo_path) as original:
            logo = original.convert("RGBA")
            logo.thumbnail((max(1, int(width * .22)), max(1, int(height * (.20 if outro else .30)))), Image.Resampling.LANCZOS)
            y = int(height * (.10 if outro else .24))
            canvas.alpha_composite(logo, ((width - logo.width) // 2, y))
        text(config.channel_name, height * (.34 if outro else .59), 54)
        text(config.cta if outro else config.tagline, height * (.43 if outro else .68), 32, config.accent)
        if outro and config.placeholders:
            box_width, box_height = int(width * .32), int(height * .26)
            for x in (int(width * .13), int(width * .55)):
                y = int(height * .61)
                draw.rounded_rectangle((x, y, x + box_width, y + box_height), radius=max(2, int(12 * scale)),
                                       outline=config.accent, width=max(1, int(2 * scale)))
        return canvas.convert("RGB")

    async def probe(self, root, name, canceled):
        raw = await self.process.run([self.ffprobe, "-v", "error", "-count_frames", "-show_streams", "-show_format",
                                      "-of", "json", name], cwd=root, canceled=canceled)
        data = json.loads(raw)
        if len(data["streams"]) != 2 or "mp4" not in data["format"]["format_name"].split(","):
            raise ValueError("Finishing requires MP4 with one video and one audio stream.")
        video = next(s for s in data["streams"] if s["codec_type"] == "video")
        audio = next(s for s in data["streams"] if s["codec_type"] == "audio")
        if ((video["codec_name"], video["pix_fmt"], audio["codec_name"], int(audio["sample_rate"]))
                != ("h264", "yuv420p", "aac", 48000) or audio["channels"] not in (1, 2)):
            raise ValueError("Finishing requires H.264/yuv420p and mono/stereo 48 kHz AAC.")
        return {"width": video["width"], "height": video["height"], "fps": Fraction(video["avg_frame_rate"]),
                "frames": int(video["nb_read_frames"]), "video_duration": Fraction(video["duration"]),
                "audio_duration": Fraction(audio["duration"]), "channels": audio["channels"],
                "audio_frames": int(audio["nb_read_frames"])}

    async def finish(self, root, evidence, config, boundary, *, canceled, progress):
        root = Path(root)
        source = await self.probe(root, "source.mp4", canceled)
        width, height, fps = source["width"], source["height"], source["fps"]
        if (not evidence.get("fully_decoded") or (width, height, source["frames"])
                != (evidence["width"], evidence["height"], evidence["frame_count"])
                or fps != evidence.get("fps", 25)
                or abs(source["video_duration"] - Fraction(*evidence["video_duration"])) > Fraction(1, 1000000)
                or abs(source["audio_duration"] - Fraction(*evidence["audio_duration"])) > Fraction(1, 1000000)):
            raise ValueError("Source MP4 streams differ from verified render evidence.")
        command = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-n", "-i", "source.mp4"]
        filters = []
        layout = "mono" if source["channels"] == 1 else "stereo"
        main_count = 2 if config.intro and boundary else 1
        if main_count == 2:
            filters += ["[0:v]split=2[mv0][mv1]", "[0:a]asplit=2[ma0][ma1]"]
        for i in range(main_count):
            v, a = (f"mv{i}", f"ma{i}") if main_count == 2 else ("0:v", "0:a")
            vf = (f"trim=end_frame={boundary['video_frame']}" if i == 0 else
                  f"trim=start_frame={boundary['video_frame']}:end_frame={source['frames']}") if boundary else f"trim=end_frame={source['frames']}"
            af = (f"atrim=end_sample={boundary['audio_sample']}" if i == 0 else
                  f"atrim=start_sample={boundary['audio_sample']}:end_sample={round(source['audio_duration'] * 48000)}") if boundary else f"atrim=end_sample={round(source['audio_duration'] * 48000)}"
            filters += [f"[{v}]{vf},setpts=PTS-STARTPTS,setsar=1[vmain{i}]",
                        f"[{a}]{af},asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:channel_layouts={layout}[amain{i}]"]
        card_inputs, extra_frames = 1, 0
        for kind, enabled, seconds in (("intro", config.intro, config.intro_seconds), ("outro", config.outro, config.outro_seconds)):
            if not enabled:
                continue
            self.card(config, root / "logo.png", width, height, outro=kind == "outro").save(root / f"{kind}.png")
            frames = round(seconds * fps)
            extra_frames += frames
            duration = float(Fraction(frames, fps))
            command += ["-loop", "1", "-framerate", str(fps), "-i", f"{kind}.png",
                        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl={layout}"]
            fade = min(.45, duration / 4)
            filters += [f"[{card_inputs}:v]trim=end_frame={frames},setpts=PTS-STARTPTS,format=yuv420p,setsar=1,"
                        f"fade=t=in:st=0:d={fade},fade=t=out:st={duration-fade}:d={fade}[v{kind}]",
                        f"[{card_inputs+1}:a]atrim=duration={seconds},asetpts=PTS-STARTPTS[a{kind}]"]
            card_inputs += 2
        order = (["main0", "intro", "main1"] if main_count == 2 else
                 (["intro"] if config.intro else []) + ["main0"]) + (["outro"] if config.outro else [])
        filters += ["".join(f"[v{x}]" for x in order) + f"concat=n={len(order)}:v=1:a=0[v]",
                    "".join(f"[a{x}]" for x in order) + f"concat=n={len(order)}:v=0:a=1[a]"]
        contained_path(root, "filters.txt").write_text(";\n".join(filters), encoding="utf-8")
        command += ["-filter_complex_script", "filters.txt", "-filter_complex_threads", "1", "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "2", "-pix_fmt", "yuv420p",
                    "-r", str(fps), "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", str(source["channels"]),
                    "-movflags", "+faststart", "branded.mp4"]
        progress("encoding", 0, 1)
        await self.process.run(command, cwd=root, canceled=canceled)
        progress("validating", 0, 1)
        path = contained_path(root, "branded.mp4")
        before, size = checksum(path), path.stat().st_size
        result = await self.probe(root, path.name, canceled)
        tolerance = Fraction(1, fps) + Fraction(1024, 48000)
        if ((result["width"], result["height"], result["fps"], result["frames"], result["channels"])
                != (width, height, fps, source["frames"] + extra_frames, source["channels"])
                or abs(result["video_duration"] - (source["video_duration"] + config.extra_seconds)) > Fraction(1, fps)
                or abs(result["audio_duration"] - (source["audio_duration"] + config.extra_seconds)) > tolerance
                or result["audio_frames"] <= 0 or size <= 0):
            raise ValueError("Branded MP4 dimensions, frames or durations differ from finishing inputs.")
        await self.process.run([self.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
            "-i", path.name, "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], cwd=root, canceled=canceled)
        if canceled():
            raise RenderCanceled("Finishing canceled after validation.")
        if checksum(path) != before or path.stat().st_size != size:
            raise ValueError("Branded output changed during validation.")
        progress("validated", 1, 1)
        return {"version": 1, "fully_decoded": True, "checksum": before, "size_bytes": size,
                "width": width, "height": height, "fps": str(fps), "frame_count": result["frames"],
                "video_codec": "h264", "audio_codec": "aac", "sample_rate": 48000, "channels": source["channels"],
                "video_duration": [result["video_duration"].numerator, result["video_duration"].denominator],
                "audio_duration": [result["audio_duration"].numerator, result["audio_duration"].denominator]}

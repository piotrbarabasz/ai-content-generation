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

    def reusable_identity(self):
        return {"adapter":"reusable-clip-finishing-v2","ffmpeg_sha256":checksum(self.ffmpeg),
                "ffprobe_sha256":checksum(self.ffprobe)}

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

    async def finish(self, root, evidence, config, boundary, *, canceled, progress, clips=None):
        root = Path(root)
        if clips is not None and config.intro and config.placement=="after_cold_open" and not boundary:
            raise ValueError("Reusable intro after cold open requires a verified frame/sample boundary; choose Before documentary explicitly if unavailable.")
        source = await self.probe(root, "source.mp4", canceled)
        width, height, fps = source["width"], source["height"], source["fps"]
        if (not evidence.get("fully_decoded") or (width, height, source["frames"])
                != (evidence["width"], evidence["height"], evidence["frame_count"])
                or fps != Fraction(str(evidence.get("fps", 25)))
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
        allocated = {}
        for kind, enabled, seconds in (("intro", config.intro, config.intro_seconds), ("outro", config.outro, config.outro_seconds)):
            if not enabled:
                continue
            if clips is not None:
                item = clips[kind]
                media = item["media"]
                if config.fit == "reject" and Fraction(media["width"], media["height"]) != Fraction(width, height):
                    raise ValueError("Clip aspect ratio differs from the source. Choose Contain (letterbox) or import a matching clip.")
                # Both frames and PCM samples must represent the same inserted duration.
                from app.domain.branding_timing import clip_allocation
                frames,samples,duration=clip_allocation(Fraction(*media["video_duration"]),fps)
                allocated[kind] = {"frames": frames, "samples": samples, "duration": [duration.numerator, duration.denominator],
                    "has_audio": media["has_audio"], "silent": not media["has_audio"] or media.get("audio_levels",{}).get("rms_db") is None, "version_id": item["version_id"], "checksum": item["checksum"]}
                extra_frames += frames
                command += ["-protocol_whitelist", "file,pipe", "-i", str(item["path"])]
                filters += [f"[{card_inputs}:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p,"
                    f"tpad=stop_mode=clone:stop_duration=1,trim=end_frame={frames},setpts=PTS-STARTPTS[v{kind}]"]
                if media["has_audio"]:
                    filters += [f"[{card_inputs}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts={layout},"
                        f"apad,atrim=end_sample={samples},asetpts=PTS-STARTPTS[a{kind}]"]
                    card_inputs += 1
                else:
                    command += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl={layout}"]
                    filters += [f"[{card_inputs+1}:a]atrim=end_sample={samples},asetpts=PTS-STARTPTS[a{kind}]"]
                    card_inputs += 2
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
        extra_duration = Fraction(extra_frames, fps) if clips is not None else config.extra_seconds
        tolerance = Fraction(1, fps) + Fraction(1024, 48000)
        if ((result["width"], result["height"], result["fps"], result["frames"], result["channels"])
                != (width, height, fps, source["frames"] + extra_frames, source["channels"])
                or abs(result["video_duration"] - (source["video_duration"] + extra_duration)) > Fraction(1, fps)
                or abs(result["audio_duration"] - (source["audio_duration"] + extra_duration)) > tolerance
                or result["audio_frames"] <= 0 or size <= 0):
            raise ValueError("Branded MP4 dimensions, frames or durations differ from finishing inputs.")
        await self.process.run([self.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
            "-i", path.name, "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], cwd=root, canceled=canceled)
        if canceled():
            raise RenderCanceled("Finishing canceled after validation.")
        if checksum(path) != before or path.stat().st_size != size:
            raise ValueError("Branded output changed during validation.")
        progress("validated", 1, 1)
        result_evidence = {"version": 1, "fully_decoded": True, "checksum": before, "size_bytes": size,
                "width": width, "height": height, "fps": str(fps), "frame_count": result["frames"],
                "video_codec": "h264", "audio_codec": "aac", "sample_rate": 48000, "channels": source["channels"],
                "video_duration": [result["video_duration"].numerator, result["video_duration"].denominator],
                "audio_duration": [result["audio_duration"].numerator, result["audio_duration"].denominator]}

        if clips is not None:
            from app.domain.branding_timing import make_timing_map
            result_evidence["timing_map"] = make_timing_map(source, allocated, boundary, before)
            source_levels = await self.audio_levels(root, "source.mp4", "source-levels.txt", canceled)
            warnings = []
            for kind, value in clips.items():
                levels = value["media"].get("audio_levels", {})
                if levels.get("rms_db") is not None and source_levels.get("rms_db") is not None and levels["rms_db"] > source_levels["rms_db"] + 6:
                    warnings.append(f"{kind.title()} audio is over 6 dB louder in measured RMS than source narration. Review the boundary or use D069 audio mastering.")
            output_levels=await self.audio_levels(root,"branded.mp4","output-levels.txt",canceled)
            if output_levels["peak_db"] is not None and output_levels["peak_db"]>=-.1:
                warnings.append(f"Decoded output peak is {output_levels['peak_db']:.2f} dBFS, near full scale. Review clip levels or use D069 mastering before delivery.")
            result_evidence["output_audio_levels"]=output_levels
            result_evidence["audio_warnings"] = warnings
            result_evidence["source_audio_levels"] = source_levels
        return result_evidence

    async def audio_levels(self, root, name, output, canceled):
        import math
        await self.process.run([self.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", name,
            "-map", "0:a:0", "-af", f"astats=metadata=1:reset=0,ametadata=print:file={output}",
            "-f", "null", "-"], cwd=root, canceled=canceled)
        result = {"peak_db": None, "rms_db": None}
        for line in contained_path(Path(root), output).read_text(encoding="utf-8").splitlines():
            for key, label in (("peak_db", "lavfi.astats.Overall.Peak_level="), ("rms_db", "lavfi.astats.Overall.RMS_level=")):
                if line.startswith(label):
                    value = float(line[len(label):])
                    result[key] = value if math.isfinite(value) else None
        return result

    async def inspect_asset(self, path, kind, *, canceled=lambda: False):
        from app.storage.paths import import_path
        path = import_path(path)
        raw = await self.process.run([self.ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe",
            "-format_whitelist", "mov,wav,mp3,flac,ogg,aac", "-count_frames", "-show_streams", "-show_format", "-of", "json", path.name], cwd=path.parent, canceled=canceled)
        data = json.loads(raw)
        videos = [s for s in data["streams"] if s["codec_type"] == "video"]
        audios = [s for s in data["streams"] if s["codec_type"] == "audio"]
        if len(audios) > 1 or len(videos) != (0 if kind == "sound" else 1) or len(data["streams"]) != len(videos) + len(audios):
            raise ValueError("Import one video with zero/one audio stream, or one audio-only sound asset.")
        if kind != "sound" and "mp4" not in data["format"]["format_name"].split(","):
            raise ValueError("Intro/outro must contain actual MP4 media.")
        if kind != "sound" and data["format"].get("tags",{}).get("major_brand","").strip().lower() in {"qt","mjp2"}:
            raise ValueError("Re-export QuickTime/Motion JPEG containers as MP4 before importing.")
        duration = Fraction(data["format"]["duration"])
        if not 0 < duration <= 60: raise ValueError("Branding media must have a measured duration between 0 and 60 seconds.")
        if any(abs(Fraction(stream.get("start_time","0"))) > Fraction(1,1000) for stream in data["streams"]):
            raise ValueError("Align clip video/audio start timestamps to zero before importing; delayed streams are not silently shifted.")
        if audios and (int(audios[0]["channels"]) not in (1,2) or int(audios[0]["sample_rate"]) > 192000):
            raise ValueError("Import mono/stereo audio at no more than 192 kHz.")
        media = {"fully_decoded": True, "has_audio": bool(audios), "audio_channels": int(audios[0]["channels"]) if audios else 0,
            "audio_sample_rate": int(audios[0]["sample_rate"]) if audios else None, "audio_codec":audios[0].get("codec_name") if audios else None,
            "duration": [duration.numerator,duration.denominator]}
        if videos:
            v = videos[0]
            fps = Fraction(v["avg_frame_rate"])
            if not 1 <= fps <= 120 or not 2 <= v["width"] <= 4096 or not 2 <= v["height"] <= 4096 or v["width"]*v["height"] > 9_000_000:
                raise ValueError("Clip dimensions/FPS exceed the supported 4K/120 FPS intake bounds.")
            if v.get("sample_aspect_ratio", "1:1") not in {"1:1", "0:1", "N/A"} or int(v.get("tags",{}).get("rotate",0)) or any(int(item.get("rotation",0)) for item in v.get("side_data_list",[])):
                raise ValueError("Export the clip with square pixels and no rotation metadata before importing.")
            vd = Fraction(v["duration"])
            if not 0<vd<=60 or int(v["nb_read_frames"])<1:raise ValueError("Clip video duration/frame count exceeds supported intake bounds.")
            media.update(width=v["width"], height=v["height"], fps=str(fps), frame_count=int(v["nb_read_frames"]), video_codec=v["codec_name"],pixel_format=v.get("pix_fmt"),
                         video_duration=[vd.numerator,vd.denominator])
        elif not audios:
            raise ValueError("Sound asset has no audio stream.")
        before = checksum(path)
        await self.process.run([self.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
            "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,wav,mp3,flac,ogg,aac", "-i", path.name, "-f", "null", "-"], cwd=path.parent, canceled=canceled)
        if checksum(path) != before: raise ValueError("Imported media changed during validation.")
        media["checksum"] = before
        if audios:
            # Measurements are actual decoded audio; no assumed loudness or automatic mastering.
            import tempfile
            with tempfile.TemporaryDirectory(dir=path.parent) as directory:
                work=Path(directory)
                media["audio_levels"] = await self.audio_levels(work, str(path), "levels.txt", canceled)
        return media

    async def create_clip(self, root, config, *, kind, duration, width=1280, height=720, fps=25, sound=None, canceled=lambda: False):
        root=Path(root)
        card=self.card(config, root/"logo.png", width,height,outro=kind=="outro")
        if kind=="outro" and config.tagline:
            draw=ImageDraw.Draw(card)
            size=max(10,round(height/1080*28))
            def face(n):return ImageFont.truetype(str(self.font_path),n) if self.font_path else ImageFont.load_default(size=n)
            font=face(size)
            while draw.textbbox((0,0),config.tagline,font=font)[2]>width*.86 and size>10:
                size-=1;font=face(size)
            if draw.textbbox((0,0),config.tagline,font=font)[2]>width*.9:raise ValueError("Shorten the outro tagline or choose a larger creation size.")
            draw.text((width/2,height*.52),config.tagline,fill=config.accent,font=font,anchor="mm")
        card.save(root/"card.png")
        command=[self.ffmpeg,"-nostdin","-v","error","-xerror","-n","-loop","1","-framerate",str(fps),"-i","card.png"]
        command += (["-protocol_whitelist","file,pipe","-i",str(sound)] if sound else ["-f","lavfi","-i","anullsrc=r=48000:cl=stereo"])
        fade=.4
        command += ["-vf",f"fade=t=in:st=0:d={fade},fade=t=out:st={duration-fade}:d={fade},format=yuv420p",
            "-af",f"aresample=48000,apad,atrim=duration={duration},afade=t=in:d={fade},afade=t=out:st={duration-fade}:d={fade}",
            "-t",str(duration),"-c:v","libx264","-preset","veryfast","-crf","20","-threads","2","-pix_fmt","yuv420p",
            "-c:a","aac","-ar","48000","-ac","2","-movflags","+faststart","clip.mp4"]
        await self.process.run(command,cwd=root,canceled=canceled)
        return await self.inspect_asset(root/"clip.mp4",kind,canceled=canceled)

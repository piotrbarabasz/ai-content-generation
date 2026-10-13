"""Streaming CPU inspections; no re-encoding or changes to the input production."""
import asyncio
from fractions import Fraction
import json
from pathlib import Path
import re

from PIL import Image

from app.providers.ffmpeg_delivery import loudness_json
from app.providers.ffmpeg_render import checksum
from app.runtime.media_process import MediaProcess, RenderCanceled


def number(value, default=None):
    try: return float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError): return default


class FFmpegQAInspector:
    def __init__(self, ffmpeg, ffprobe, *, process_factory=MediaProcess):
        self.ffmpeg, self.ffprobe = Path(ffmpeg).resolve(strict=True), Path(ffprobe).resolve(strict=True)
        self.process_factory = process_factory

    def identity(self):
        return {"adapter": "ffmpeg-qa-v1", "ffmpeg_sha256": checksum(self.ffmpeg), "ffprobe_sha256": checksum(self.ffprobe)}

    def process(self, config): return self.process_factory(timeout=config.validator_timeout)

    async def probe(self, source, root, config, *, canceled, progress):
        # nb_frames is container metadata. Quick QA deliberately omits -count_frames.
        raw = await self.process(config).run([self.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", source], cwd=root, canceled=canceled)
        data = json.loads(raw); videos, audios = [], []
        for s in data["streams"]:
            duration = number(s.get("duration"), number(data["format"].get("duration"), 0))
            if s.get("codec_type") == "video":
                videos.append({"codec": s.get("codec_name"), "profile": s.get("profile", "unknown"), "pixel_format": s.get("pix_fmt"),
                               "width": int(s["width"]), "height": int(s["height"]), "fps": number(s.get("avg_frame_rate"), 0),
                               "duration": duration, "video_duration": duration, "frame_count": int(s["nb_frames"]) if str(s.get("nb_frames", "")).isdigit() else None,
                               "field_order": s.get("field_order", "unknown"), "sample_aspect_ratio": s.get("sample_aspect_ratio"),
                               "bitrate_bps": int(s["bit_rate"]) if str(s.get("bit_rate", "")).isdigit() else None})
            elif s.get("codec_type") == "audio":
                audios.append({"codec": s.get("codec_name"), "profile": s.get("profile", "unknown"), "sample_rate": int(s.get("sample_rate", 0)),
                               "channels": int(s.get("channels", 0)), "duration": duration})
        return {"container": data["format"]["format_name"], "video_streams": videos, "audio_streams": audios,
                "stream_count": len(data["streams"]), "reported_size": number(data["format"].get("size")), "frame_count_method": "container_metadata"}

    async def decode(self, source, root, config, *, canceled, progress):
        frames = [0]
        def line(value):
            if value.startswith("frame="):
                frames[0] = int(value.split("=", 1)[1]); progress("Full media decode", frames[0], 0)
        await self.process(config).run([self.ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-err_detect", "explode", "-i", source,
                                      "-map", "0:v:0", "-map", "0:a:0?", "-progress", "pipe:1", "-f", "null", "-"], cwd=root, canceled=canceled, on_line=line, capture_stdout=False)
        return {"decoded_frames": frames[0], "fully_decoded": True}

    async def audio(self, source, root, config, *, canceled, progress):
        intervals, start, count = [], [None], [0]
        def line(value):
            match = re.search(r"silence_start:\s*([\d.e+-]+)", value)
            if match: start[0] = max(0, float(match[1]))
            match = re.search(r"silence_end:\s*([\d.e+-]+)", value)
            if match and start[0] is not None:
                end = float(match[1]); count[0] += 1
                if len(intervals) < config.max_intervals and end > start[0]: intervals.append([start[0], end])
                start[0] = None
        filters = f"silencedetect=noise={config.silence_db}dB:d={config.silence_seconds},loudnorm=I=-16:TP=-2:LRA=11:print_format=json"
        raw = await self.process(config).run([self.ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-v", "info", "-xerror", "-i", source, "-map", "0:a:0", "-vn",
                                           "-af", filters, "-f", "null", "-"], cwd=root, canceled=canceled, capture_stderr=True, on_stderr_line=line)
        # loudnorm operates on decoded audio to a null sink, never on the source file.
        return {**loudness_json(raw), "silence": intervals, "interval_count": count[0], "truncated": count[0] > len(intervals), "method": "decoded_silencedetect_loudnorm"}

    async def visual(self, source, root, config, *, canceled, progress):
        black, freeze, starts, counts = [], [], {}, {"black": 0, "freeze": 0}
        crops = {}
        def add(kind, start, end):
            if end <= start: return
            counts[kind] += 1; values = black if kind == "black" else freeze
            if len(values) < config.max_intervals: values.append([max(0, start), end])
        def line(value):
            crop = re.search(r"t:([\d.e+-]+).*crop=(\d+):(\d+):(\d+):(\d+)", value)
            if crop:
                key = tuple(map(int, crop.groups()[1:])); time = float(crop[1])
                if key in crops:
                    crops[key]["end"] = time; crops[key]["count"] += 1
                elif len(crops) < config.max_intervals:
                    crops[key] = {"crop": list(key), "start": time, "end": time, "count": 1}
            match = re.search(r"black_start:([\d.e+-]+)\s+black_end:([\d.e+-]+)", value)
            if match: add("black", float(match[1]), float(match[2]))
            for event in ("freeze_start", "freeze_end"):
                match = re.search(event + r":\s*([\d.e+-]+)", value)
                if match:
                    time = float(match[1])
                    if event.endswith("start"): starts["freeze"] = time
                    elif "freeze" in starts: add("freeze", starts.pop("freeze"), time)
        filters = f"blackdetect=d={config.black_seconds}:pix_th={config.black_pixel}:pic_th=0.98,freezedetect=n=-60dB:d={config.freeze_seconds},cropdetect=limit=24:round=2:reset=0"
        await self.process(config).run([self.ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-v", "info", "-xerror", "-i", source, "-an", "-vf", filters,
                                       "-progress", "pipe:1", "-f", "null", "-"], cwd=root, canceled=canceled, on_stderr_line=line,
                                       on_line=lambda value: progress("Scanning black/low-motion intervals", int(value.split("=")[1]), 0) if value.startswith("frame=") else None,
                                       capture_stdout=False)
        # FFmpeg can omit freeze_end at EOF: the measured video end closes it.
        if "freeze" in starts:
            probe = await self.probe(source, root, config, canceled=canceled, progress=progress)
            add("freeze", starts["freeze"], probe["video_streams"][0]["duration"])
        return {"black": black, "freeze": freeze, "border_samples": list(crops.values()), "interval_counts": counts, "truncated": any(counts[k] > len(v) for k, v in (("black", black), ("freeze", freeze)))}

    async def frames(self, source, root, shots, config, *, canceled, progress):
        if not shots: return []
        count = min(len(shots), config.max_frames)
        indices = sorted({round(i * (len(shots) - 1) / max(1, count - 1)) for i in range(count)})
        result = []
        for i in indices:
            if canceled(): raise RenderCanceled("QA frame sampling canceled.")
            shot = shots[i]; time = (shot["start"] + shot["end"]) / 2
            result.append(await self.frame(source, root, time, config, canceled=canceled, index=i))
            progress("Sampling meaningful shot boundaries", len(result), len(indices))
        return result

    async def frame(self, source, root, time, config, *, canceled, index=0):
        name = f"frame-{index}-{len(list(Path(root).glob('frame-*.png')))}.png"
        await self.process(config).run([self.ffmpeg, "-nostdin", "-v", "error", "-n", "-ss", str(time), "-i", source,
                                      "-vf", "scale=384:384:force_original_aspect_ratio=decrease", "-frames:v", "1", "-threads", "1", name], cwd=root, canceled=canceled)
        path = Path(root) / name
        if path.stat().st_size > 2 * 1024 * 1024: raise ValueError("QA evidence exceeds its 2 MiB bound.")
        def read():
            raw = path.read_bytes()
            with Image.open(path) as image:
                if image.format != "PNG" or max(image.size) > 384: raise ValueError("Invalid QA evidence dimensions/format.")
                small = image.convert("L").resize((9, 8))
                pixels = [small.getpixel((x, y)) for y in range(8) for x in range(9)]
                digest = sum((pixels[y * 9 + x] > pixels[y * 9 + x + 1]) << (y * 8 + x) for y in range(8) for x in range(8))
            return {"shot_index": index, "time": time, "hash": digest, "raw": raw}
        return await asyncio.to_thread(read)

"""Two-pass loudness mastering and measured post-AAC delivery validation."""

from dataclasses import asdict
from fractions import Fraction
import json
import math
from pathlib import Path
import re

from app.domain.delivery import DELIVERY_PRESETS, delivery_status
from app.providers.ffmpeg_render import checksum
from app.runtime.media_process import MediaProcess, RenderCanceled
from app.storage.paths import contained_path


def loudness_json(raw):
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    matches = re.findall(r'\{\s*"input_i".*?\}', text, flags=re.S)
    if not matches:
        raise ValueError("FFmpeg loudness analysis returned no structured measurements.")
    try:
        data = json.loads(matches[-1])
        result = {}
        for name, key in (("integrated_lufs", "input_i"), ("true_peak_dbtp", "input_tp"),
                          ("lra", "input_lra"), ("threshold", "input_thresh"), ("offset", "target_offset")):
            value = float(data[key])
            if math.isnan(value):
                raise ValueError("NaN loudness measurement")
            result[name] = value if math.isfinite(value) else None
        result["normalization_type"] = data.get("normalization_type", "unknown")
        return result
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("FFmpeg loudness measurement JSON is incomplete or invalid.") from exc


class FFmpegDeliveryExporter:
    def __init__(self, ffmpeg, ffprobe, *, process=None):
        self.ffmpeg, self.ffprobe = Path(ffmpeg).resolve(strict=True), Path(ffprobe).resolve(strict=True)
        self.process = process or MediaProcess(timeout=1800)

    def identity(self):
        return {"adapter": "delivery-export-v1", "ffmpeg_sha256": checksum(self.ffmpeg),
                "ffprobe_sha256": checksum(self.ffprobe), "quiet_floor_lufs": -40, "max_final_peak_dbtp": -1,
                "loudness_tolerance_lu": 1, "max_normalization_gain_db": 24}

    async def probe(self, root, name, canceled):
        try:
            raw = await self.process.run([self.ffprobe, "-v", "error", "-count_frames", "-show_streams", "-show_format",
                                         "-of", "json", name], cwd=root, canceled=canceled)
            data = json.loads(raw)
            streams = data["streams"]
            if len(streams) != 2 or "mp4" not in data["format"]["format_name"].split(","):
                raise ValueError("Expected one video and one audio stream in MP4.")
            video = next(s for s in streams if s["codec_type"] == "video")
            audio = next(s for s in streams if s["codec_type"] == "audio")
            if ((video["codec_name"], video["pix_fmt"], audio["codec_name"]) != ("h264", "yuv420p", "aac")
                    or audio.get("profile", "LC") != "LC" or audio["channels"] not in (1, 2)
                    or video.get("field_order", "progressive") not in {"progressive", "unknown"}
                    or video.get("color_transfer") in {"smpte2084", "arib-std-b67"}):
                raise ValueError("Delivery supports progressive SDR H.264/yuv420p and mono/stereo AAC-LC sources.")
            result = {"width": video["width"], "height": video["height"], "fps": Fraction(video["avg_frame_rate"]),
                      "frame_count": int(video["nb_read_frames"]), "video_duration": Fraction(video["duration"]),
                      "audio_duration": Fraction(audio["duration"]), "sample_rate": int(audio["sample_rate"]),
                      "channels": audio["channels"], "audio_frames": int(audio["nb_read_frames"]),
                      "sar": video.get("sample_aspect_ratio", "1:1"), "profile": video.get("profile"),
                      "field_order": video.get("field_order", "unknown"),
                      "video_bitrate": int(video["bit_rate"]) if str(video.get("bit_rate", "N/A")).isdigit() else None,
                      "audio_bitrate": int(audio["bit_rate"]) if str(audio.get("bit_rate", "N/A")).isdigit() else None,
                      "color": {key: video[key] for key in ("color_range", "color_space", "color_transfer", "color_primaries")
                                if video.get(key) not in (None, "unknown", "unspecified")}}
            if (min(result[k] for k in ("width", "height", "fps", "frame_count", "video_duration", "audio_duration", "sample_rate", "audio_frames")) <= 0
                    or result["width"] % 2 or result["height"] % 2 or result["sar"] in {"0:1", "N/A"}):
                raise ValueError("Invalid delivery source stream measurements.")
            return result
        except (KeyError, StopIteration, TypeError, json.JSONDecodeError, ZeroDivisionError) as exc:
            raise ValueError("FFprobe delivery stream report is incomplete or invalid.") from exc

    @staticmethod
    def targets(config):
        return f"I={config.target_lufs}:TP={config.target_peak}:LRA={config.target_lra}"

    async def analyze(self, root, name, config, canceled):
        raw = await self.process.run([self.ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-v", "info", "-xerror",
            "-i", name, "-map", "0:a:0", "-vn", "-af", "loudnorm=" + self.targets(config) + ":print_format=json",
            "-f", "null", "-"], cwd=root, canceled=canceled, capture_stderr=True)
        return loudness_json(raw)

    async def packet_check(self, root, name, info, canceled):
        # Stream the packet list: long films must not exhaust the bounded stdout buffer.
        packets = {0: [], 1: []}
        def line(value):
            fields = value.split(",")
            if len(fields) < 3 or fields[0] not in {"0", "1"}:
                return
            try:
                packets[int(fields[0])].append((Fraction(fields[1]), Fraction(fields[2])))
            except ValueError as exc:
                raise ValueError("Delivery contains packets without valid timestamps.") from exc
        await self.process.run([self.ffprobe, "-v", "error", "-show_packets", "-show_entries",
            "packet=stream_index,pts_time,duration_time", "-of", "csv=p=0", name], cwd=root, canceled=canceled,
            on_line=line, capture_stdout=False)
        video, audio = sorted(packets[0]), sorted(packets[1])
        if len(video) != info["frame_count"] or not audio or abs(video[0][0]) > Fraction(2, 1000000):
            raise ValueError("Delivery packet count/start timestamps differ from source frame alignment.")
        for values, step in ((video, Fraction(1, info["fps"])), (audio, None)):
            for previous, current in zip(values, values[1:]):
                if abs(current[0] - previous[0] - (step or previous[1])) > Fraction(3, 1000000):
                    raise ValueError("Delivery has a packet timestamp discontinuity.")
        if (any(duration <= 0 or duration > Fraction(1024, 48000) + Fraction(2, 1000000) for _, duration in audio)
                or abs(audio[-1][0] + audio[-1][1] - info["audio_duration"]) > Fraction(3, 1000000)):
            raise ValueError("Delivery audio packet span does not match its measured duration.")
        if not -Fraction(1024, 48000) - Fraction(2, 1000000) <= audio[0][0] <= 0:
            raise ValueError("Unexpected audio offset at delivery start.")

    async def export(self, root, source_evidence, config, *, canceled, progress):
        root = Path(root)
        preset = DELIVERY_PRESETS[config.preset]
        source = await self.probe(root, "source.mp4", canceled)
        if (not source_evidence.get("fully_decoded") or source["frame_count"] != source_evidence["frame_count"]
                or (source["width"], source["height"]) != (source_evidence["width"], source_evidence["height"])
                or source["fps"] != Fraction(str(source_evidence["fps"]))
                or abs(source["video_duration"] - Fraction(*source_evidence["video_duration"])) > Fraction(2, 1000000)
                or abs(source["audio_duration"] - Fraction(*source_evidence["audio_duration"])) > Fraction(2, 1000000)):
            raise ValueError("Source streams differ from the selected verified artifact evidence.")
        progress("analyzing source audio", 0, 1)
        before = await self.analyze(root, "source.mp4", config, canceled)
        apply = bool(config.normalize and before["integrated_lufs"] is not None and before["integrated_lufs"] >= -40
                     and config.target_lufs - before["integrated_lufs"] <= 24
                     and all(before[k] is not None for k in ("true_peak_dbtp", "lra", "threshold", "offset")))
        filters = "anull"
        if apply:
            filters = ("loudnorm=" + self.targets(config)
                + f":measured_I={before['integrated_lufs']}:measured_TP={before['true_peak_dbtp']}"
                  f":measured_LRA={before['lra']}:measured_thresh={before['threshold']}:offset={before['offset']}"
                  ":linear=true:print_format=json")
        filters = (f"atrim=end_sample={round(source['audio_duration'] * source['sample_rate'])},asetpts=N/SR/TB,"
                   + filters + f",aresample=48000,atrim=end_sample={round(source['audio_duration'] * 48000)},asetpts=N/SR/TB")
        command = [self.ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-v", "info", "-xerror", "-n", "-i", "source.mp4",
            "-map", "0:v:0", "-map", "0:a:0", "-vf", f"format={preset.pixel_format},setsar={source['sar'].replace(':', '/')}",
            "-c:v", preset.video_codec, "-preset", preset.encoder_preset, "-crf", str(config.crf), "-profile:v", preset.video_profile,
            "-pix_fmt", preset.pixel_format, "-field_order", "progressive", "-fps_mode", "passthrough",
            "-threads", "2", "-af", filters, "-c:a", preset.audio_codec, "-profile:a", preset.audio_profile,
            "-b:a", str(preset.mono_bitrate if source["channels"] == 1 else preset.stereo_bitrate),
            "-ar", str(preset.sample_rate), "-ac", str(source["channels"]), "-movflags", "+faststart"]
        for field, option in (("color_range", "-color_range"), ("color_space", "-colorspace"),
                              ("color_transfer", "-color_trc"), ("color_primaries", "-color_primaries")):
            if field in source["color"]:
                command += [option, source["color"][field]]
        command += ["delivery.mp4"]
        progress("encoding delivery", 0, 1)
        logs = await self.process.run(command, cwd=root, canceled=canceled, capture_stderr=True)
        mode = loudness_json(logs)["normalization_type"] if apply else "disabled"
        progress("validating encoded media", 0, 1)
        path = contained_path(root, "delivery.mp4")
        if not path.is_file() or not path.stat().st_size:
            raise ValueError("Delivery encoder produced no output bytes.")
        digest, size = checksum(path), path.stat().st_size
        output = await self.probe(root, path.name, canceled)
        frame_tolerance = Fraction(1, source["fps"])
        audio_tolerance = Fraction(1024, 48000) + Fraction(2, 1000000)
        if ((output["width"], output["height"], output["fps"], output["frame_count"], output["sample_rate"], output["channels"])
                != (source["width"], source["height"], source["fps"], source["frame_count"], 48000, source["channels"])
                or output["sar"] != source["sar"] or output["profile"] != "High" or output["field_order"] != "progressive"
                or output["color"] != source["color"]
                or abs(output["video_duration"] - source["video_duration"]) > frame_tolerance
                or abs(output["audio_duration"] - source["audio_duration"]) > audio_tolerance
                or abs(output["video_duration"] - output["audio_duration"]) > frame_tolerance + audio_tolerance):
            raise ValueError("Delivery stream profile, frames, color, duration or A/V synchronization failed validation.")
        await self.packet_check(root, path.name, output, canceled)
        await self.process.run([self.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode", "-i", path.name,
            "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], cwd=root, canceled=canceled)
        progress("measuring final AAC loudness", 0, 1)
        after = await self.analyze(root, path.name, config, canceled)
        status, reasons = delivery_status(before, after, config, apply)
        if config.normalize and before["integrated_lufs"] is not None and config.target_lufs - before["integrated_lufs"] > 24:
            status = "REVIEW"
            reasons.append("Normalization skipped: requested gain exceeds the 24 dB editorial ceiling.")
        if canceled():
            raise RenderCanceled("Delivery export canceled after quality validation.")
        if checksum(path) != digest or path.stat().st_size != size:
            raise ValueError("Delivery output changed during validation.")
        report = {**output, "version": 1, "fully_decoded": True, "preset": config.preset, "status": status,
                  "reasons": reasons, "before": before, "after": after, "normalization_applied": apply,
                  "normalization_type": mode, "loudness_difference_lu": None if after["integrated_lufs"] is None else after["integrated_lufs"] - config.target_lufs,
                  "true_peak_difference_db": None if after["true_peak_dbtp"] is None else after["true_peak_dbtp"] - config.target_peak,
                  "av_discrepancy_seconds": float(output["video_duration"] - output["audio_duration"]),
                  "checksum": digest, "size_bytes": size, "encoding": {**asdict(preset), "crf": config.crf},
                  "video_codec": "h264", "audio_codec": "aac", "audio_profile": "LC", "progressive": True,
                  "packet_timestamps_verified": True}
        for field in ("video_duration", "audio_duration"):
            report[field] = [output[field].numerator, output[field].denominator]
        report["fps"] = str(output["fps"])
        progress("quality " + status, 1, 1)
        return report

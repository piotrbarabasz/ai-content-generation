"""Independent deterministic validators, with editorial uncertainty kept advisory."""
from collections import Counter
from statistics import mean, median

from app.domain.caption_track import validate_caption_segments
from app.domain.production_quality import QAFinding


def finding(context, rule, severity, category, explanation, action, actual=None, expected=None, *, start=None, end=None, refs=()):
    return QAFinding(rule, severity, category, context.source_id, context.source_checksum, explanation, action,
                     actual, expected, start, end, tuple(refs))


class SourceValidator:
    id, version = "source", "1"
    def validate(self, c, config, m):
        result = []
        if not c.facts.get("owner_ok", True):
            result.append(finding(c, "SOURCE_OWNERSHIP_INVALID", "BLOCKED", "integrity", "Selected video is not owned by this project.", "Select an owned production artifact."))
        if m.get("source_missing"):
            result.append(finding(c, "SOURCE_MISSING", "BLOCKED", "integrity", "The retained MP4 file is missing.", "Restore or export the production through normal tools."))
        elif (m.get("source_checksum"), m.get("source_size")) != (c.source_checksum, c.size_bytes):
            result.append(finding(c, "SOURCE_CHECKSUM_MISMATCH", "BLOCKED", "integrity", "Video bytes differ from their retained manifest.", "Restore the exact source or produce a new verified artifact.",
                                  {"checksum": m.get("source_checksum"), "size": m.get("source_size")}, {"checksum": c.source_checksum, "size": c.size_bytes}))
        for reason in c.facts.get("integrity_errors", []):
            result.append(finding(c, "SOURCE_MAPPING_INVALID", "BLOCKED", "integrity", reason, "Review the source revision and derivation bindings.", actual=reason))
        return tuple(result)


class TechnicalValidator:
    id, version = "technical", "1"
    def validate(self, c, config, m):
        p = m["probe"]; result = []
        def check(rule, valid, actual, expected, explanation):
            if not valid: result.append(finding(c, rule, "BLOCKED", "technical", explanation, "Rebuild/select a supported verified production through normal export tools.", actual, expected))
        videos, audios = p["video_streams"], p["audio_streams"]
        check("VIDEO_STREAM_COUNT", len(videos) == 1, len(videos), 1, "Expected one video stream.")
        check("AUDIO_MISSING_OR_EXTRA", len(audios) == 1, len(audios), 1, "Expected one narration audio stream.")
        check("UNSUPPORTED_CONTAINER", "mp4" in p["container"].split(","), p["container"], "MP4", "The selected final production is not an MP4 container.")
        if len(videos) != 1: return tuple(result)
        v = videos[0]; a = audios[0] if len(audios) == 1 else None
        v = {**v, "video_duration": v["duration"]}
        check("VIDEO_CODEC_PROFILE", v["codec"] == "h264" and v["pixel_format"] == "yuv420p" and v["profile"] in {"Baseline", "Constrained Baseline", "Main", "High"},
              [v["codec"], v["pixel_format"], v["profile"]], "Supported 8-bit H.264 baseline/main/high, yuv420p", "Unsupported video codec, pixel format or profile.")
        check("INTERLACED_VIDEO", v["field_order"] in {"progressive", "unknown"}, v["field_order"], "progressive (historical unspecified accepted)", "Unexpected interlaced video.")
        expected = c.facts.get("expected", {})
        for key, rule, tolerance in (("width", "OUTPUT_WIDTH", 0), ("height", "OUTPUT_HEIGHT", 0), ("fps", "OUTPUT_FPS", .00001),
                                    ("video_duration", "VIDEO_TRUNCATED_OR_CHANGED", max(config.av_tolerance, 1 / max(v["fps"], 1)))):
            if key in expected:
                check(rule, abs(v[key] - expected[key]) <= tolerance, v[key], expected[key], "Measured video differs from the retained production contract.")
        check("ZERO_LENGTH_VIDEO", v["duration"] > 0 and v["width"] > 0 and v["height"] > 0 and v["fps"] > 0,
              v, "positive duration/geometry/FPS", "Invalid or zero-length video stream.")
        if "video_duration" not in v: v["video_duration"] = v["duration"]
        frames = m.get("decoded_frames", v["frame_count"])
        if frames is not None:
            check("FRAME_COUNT_INVALID", frames > 0 and ("frame_count" not in expected or frames == expected["frame_count"]),
                  frames, expected.get("frame_count", "positive"), "Frame count differs from the production contract.")
        else:
            result.append(finding(c, "FRAME_COUNT_UNAVAILABLE", "REVIEW", "technical", "Container does not report frame count; Quick QA did not decode it.", "Run Deep QA to count decoded frames."))
        if a:
            check("AUDIO_CODEC", a["codec"] == "aac" and a["profile"] in {"LC", "unknown"} and a["channels"] in (1, 2), a, "mono/stereo AAC-LC", "Unsupported audio codec/profile/channel layout.")
            check("AUDIO_SAMPLE_RATE", a["sample_rate"] == expected.get("sample_rate", 48000), a["sample_rate"], expected.get("sample_rate", 48000), "Unexpected audio sample rate.")
            check("ZERO_LENGTH_AUDIO", a["duration"] > 0, a["duration"], "> 0", "The audio stream has zero duration.")
            delta = abs(v["duration"] - a["duration"])
            check("AV_DESYNCHRONIZATION", delta <= max(config.av_tolerance, 1 / max(v["fps"], 1) + 1024 / max(a["sample_rate"], 1)), delta, config.av_tolerance, "Significant video/audio duration discrepancy.")
            if "audio_duration" in expected:
                check("AUDIO_TRUNCATED_OR_CHANGED", abs(a["duration"] - expected["audio_duration"]) <= config.av_tolerance,
                      a["duration"], expected["audio_duration"], "Audio duration differs from the retained source contract.")
        result.append(finding(c, "MEDIA_MEASUREMENTS", "INFO", "technical", "Local FFprobe stream/container measurements; low bitrate alone is not a failure.", "Review the measurements alongside the chosen export profile.", p))
        return tuple(result)


class DurationValidator:
    id, version = "duration", "1"
    def validate(self, c, config, m):
        durations = dict(c.facts.get("durations", {})); videos = m.get("probe", {}).get("video_streams", [])
        if videos: durations["selected_video_seconds"] = videos[0]["duration"]
        profile = c.facts.get("duration_profile")
        if profile in {"short", "social"} and c.facts.get("narration_unpadded"):
            audio = m.get("probe", {}).get("audio_streams", [])
            if len(audio) == 1: durations["actual_narration_seconds"] = audio[0]["duration"]
        m["durations"] = durations
        actual = durations.get("actual_narration_seconds")
        if profile not in {"standard", "social", "short"} or actual is None:
            return (finding(c, "DURATION_ACCEPTANCE_UNAVAILABLE", "INFO", "duration", "Narrative duration acceptance is UNAVAILABLE: no verified plan/derivation mapping.", "Retain a verified Video Plan and source composition mapping; QA does not guess.", durations),)
        bounds = [540, 660] if profile == "standard" else c.facts.get("duration_bounds", [30, 60])
        if not bounds[0] <= actual <= bounds[1]:
            return (finding(c, "DURATION_OUTSIDE_PROFILE", "BLOCKED", "duration", "Measured narration is outside the accepted format range; branding padding is excluded.", "Revise and regenerate narration through normal production tools, then rerun QA.", durations, {"profile": profile, "narration_seconds": bounds}),)
        return (finding(c, "NARRATION_DURATION", "INFO", "duration", "Verified narration satisfies the format range; original/branded/delivery times are recorded separately.", "No duration correction indicated.", durations, bounds),)


class CaptionValidator:
    id, version = "captions", "1"
    def validate(self, c, config, m):
        caption = c.facts.get("captions", {"state": "not_requested"}); state = caption["state"]
        m["captions"] = {k: v for k, v in caption.items() if k != "segments"}
        if state == "not_requested":
            return (finding(c, "CAPTIONS_NOT_REQUESTED", "INFO", "captions", "No subtitles were requested; omission is not a blocking error.", "Enable captions in production if desired."),)
        if state != "valid":
            severity = "BLOCKED" if state in {"stale", "invalid"} else "REVIEW"
            return (finding(c, "CAPTIONS_" + state.upper(), severity, "captions", caption.get("reason", "Requested captions are unavailable."), "Regenerate or select current synchronized captions through normal tools."),)
        result = []
        try: segments = validate_caption_segments(caption["segments"])
        except ValueError as exc:
            return (finding(c, "CAPTION_TIMING_INVALID", "BLOCKED", "captions", str(exc), "Correct the subtitle artifact and rerun QA."),)
        duration = m["probe"]["video_streams"][0]["duration"]
        for s in segments:
            if s.end_ms > round(duration * 1000):
                result.append(finding(c, "CAPTION_PAST_VIDEO", "BLOCKED", "captions", "Caption extends past the selected video.", "Correct the timing/rebasing before export.", s.end_ms / 1000, duration,
                                      start=s.start_ms / 1000, end=s.end_ms / 1000))
            if len(s.text.splitlines()) > config.caption_lines or any(len(line) > config.caption_chars for line in s.text.splitlines()):
                result.append(finding(c, "CAPTION_READABILITY", "REVIEW", "captions", "Caption exceeds configured line/character guidance.", "Review wrapping and reading speed in the actual player.", s.text, {"chars": config.caption_chars, "lines": config.caption_lines},
                                      start=s.start_ms / 1000, end=s.end_ms / 1000))
        speech = c.facts.get("verified_speech", [])
        if config.mode == "deep" and speech:
            spans = [(s.start_ms / 1000, s.end_ms / 1000) for s in segments]
            total = sum(end - start for start, end in speech)
            covered = sum(sum(max(0, min(end, b) - max(start, a)) for a, b in spans) for start, end in speech)
            ratio = covered / total if total else None
            m["caption_coverage"] = {"method": caption.get("speech_method"), "covered_speech_seconds": covered, "verified_speech_seconds": total, "ratio": ratio}
            if ratio is not None and ratio < config.caption_min_coverage:
                result.append(finding(c, "CAPTION_COVERAGE_LOW", "REVIEW", "captions", "Caption coverage is low over verified aligned speech regions.", "Review missing caption regions against the measured speech mapping.", ratio, config.caption_min_coverage))
        elif config.mode == "deep": m["caption_coverage"] = {"state": "UNAVAILABLE", "reason": "No verified speech alignment; plain text was not used to invent coverage."}
        return tuple(result)


def uncovered(start, end, intentional):
    spans = [(start, end)]
    for a, b in intentional:
        next_spans = []
        for lo, hi in spans:
            if b <= lo or a >= hi: next_spans.append((lo, hi))
            else:
                if lo < a: next_spans.append((lo, a))
                if b < hi: next_spans.append((b, hi))
        spans = next_spans
    return spans


class AudioValidator:
    id, version = "audio", "1"
    def validate(self, c, config, m):
        audio = m["audio"]; result = []
        targets = c.facts.get("delivery_targets", {}) if config.inherit_delivery else {}
        lufs, peak = targets.get("target_lufs", config.target_lufs), targets.get("target_peak", config.true_peak)
        m["audio_targets"] = {"lufs": lufs, "peak_dbtp": peak, "tolerance_lu": config.lufs_tolerance, "editorial_guidance": True}
        for key, target, bad in (("integrated_lufs", lufs, lambda value: abs(value - lufs) > config.lufs_tolerance),
                                  ("true_peak_dbtp", peak, lambda value: value > peak)):
            value = audio.get(key)
            if value is None or bad(value):
                result.append(finding(c, "AUDIO_" + key.upper(), "REVIEW", "audio", "Decoded audio is outside the configured editorial target or has no finite measurement.", "Listen and review mastering using normal production tools; these are not mandatory YouTube rules.", value, target))
        intentional = c.facts.get("intentional_silence", [])
        for start, end in audio["silence"]:
            remaining = uncovered(start, end, intentional)
            if not remaining:
                result.append(finding(c, "INTENTIONAL_SILENCE", "INFO", "audio", "Silence lies inside verified branding/silent source regions.", "No automatic repair is indicated.", end - start, start=start, end=end))
            for lo, hi in remaining:
                if hi - lo < config.silence_seconds: continue
                overlaps = any(a < hi and b > lo for a, b in c.facts.get("verified_speech", []))
                result.append(finding(c, "SUSPICIOUS_NARRATION_SILENCE", "REVIEW", "audio", "Long decoded silence " + ("overlaps a verified speech region." if overlaps else "has no matching intentional-silence explanation."),
                                      "Listen at this interval; a pause may be intentional. QA does not infer missing words.", hi - lo, config.silence_seconds, start=lo, end=hi))
        return tuple(result)


class VisualValidator:
    id, version = "visual", "1"
    def validate(self, c, config, m):
        result = []; shots = c.facts.get("shots", [])
        for kind, intervals in m["visual"].items():
            if kind not in {"black", "freeze"}: continue
            for start, end in intervals:
                overlapping = [s for s in shots if s["start"] < end and s["end"] > start]
                known = not uncovered(start, end, c.facts.get("branding_intervals", []))
                static = kind == "freeze" and overlapping and all(s["motion"] == "static" for s in overlapping)
                severity = "INFO" if known or static else "REVIEW"
                result.append(finding(c, "BLACK_INTERVAL" if kind == "black" else "LOW_MOTION_INTERVAL", severity, "visual",
                    "Detected " + kind + " interval. " + ("Verified branding region." if known else "Static footage is intentional under the source motion policy." if static else "This may be an intentional fade or still image; it is not a proven defect."),
                    "Inspect the evidence and intended scene/motion policy.", {"duration": end - start, "classification": "intentional" if known or static else "ambiguous"},
                    start=start, end=end, refs=tuple(s["id"] for s in overlapping)))
        seen = {}
        for second in shots:
            digest = second.get("image_checksum")
            first = seen.get(digest) if digest else None
            if first and first["id"] != second["id"]:
                result.append(finding(c, "EXACT_REUSED_IMAGE", "REVIEW", "visual", "Distinct shots reuse the exact same verified source image; continuity within one shot is excluded.", "Review whether this reuse is intentional.", first.get("image_id"), start=first["start"], end=second["end"], refs=(first["id"], second["id"])))
            elif digest: seen[digest] = second
        for row in m.get("near_duplicates", []):
            result.append(finding(c, "POSSIBLE_REPEATED_VISUAL", "REVIEW", "visual", "Boundary-sampled shots have similar perceptual hashes; similarity is advisory and can reflect legitimate continuity.", "Compare the referenced shots and their intended concepts.", row["distance"], config.hash_distance,
                                  start=row["start"], end=row["end"], refs=tuple(row["references"])))
        for s in shots:
            if s.get("mode") == "fill" and s.get("crop_fraction", 1) < .4:
                result.append(finding(c, "VERTICAL_CROP_REVIEW", "REVIEW", "composition", "Fill cropping retains less than 40% of the source width; subject loss is possible, not proven.", "Inspect the portrait preview/focal point or choose Fit mode.", s["crop_fraction"], .4,
                                      start=s["start"], end=s["end"], refs=(s["id"],)))
        video = m.get("probe", {}).get("video_streams", [{}])[0]
        for row in m["visual"].get("border_samples", []):
            width, height, x, y = row["crop"]
            if row["count"] < 25 or row["end"] <= row["start"] or not width or not height: continue
            if width * height >= .85 * video.get("width", width) * video.get("height", height): continue
            related = [s for s in shots if s["start"] < row["end"] and s["end"] > row["start"]]
            intentional = related and all(s.get("mode") == "fit" for s in related)
            result.append(finding(c, "BORDER_REVIEW", "INFO" if intentional else "REVIEW", "composition", "Decoded dark-border estimate; " + ("Fit mode permits padding." if intentional else "dark content can resemble unexpected borders."),
                "Inspect the actual frame and intended Fit/Fill policy; no OCR or subject inference was used.", row["crop"], start=row["start"], end=row["end"], refs=tuple(s["id"] for s in related)))
        return tuple(result)


class PacingValidator:
    id, version = "pacing", "1"
    def validate(self, c, config, m):
        shots = c.facts.get("shots", []); result = []
        if not shots: return ()
        durations = [s["end"] - s["start"] for s in shots]
        m["pacing"] = {"shots": len(shots), "average_seconds": mean(durations), "median_seconds": median(durations)}
        for s, duration in zip(shots, durations):
            if duration < config.short_shot or duration > config.long_shot:
                result.append(finding(c, "SHOT_LENGTH_REVIEW", "REVIEW", "pacing", "Shot duration falls outside configured editorial guidance.", "Review pacing; this is not a technical error.", duration, [config.short_shot, config.long_shot], start=s["start"], end=s["end"], refs=(s["id"],)))
        common, count = Counter(round(v, 3) for v in durations).most_common(1)[0]
        if len(shots) >= 5 and count / len(shots) >= .8:
            result.append(finding(c, "REPEATED_PACING_PATTERN", "REVIEW", "pacing", "Most shots have the same duration; constant pacing may be intentional.", "Review rhythm and topic transitions without assuming a defect.", {"seconds": common, "count": count}, start=shots[0]["start"], end=shots[-1]["end"]))
        return tuple(result)

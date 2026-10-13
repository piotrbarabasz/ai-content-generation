"""Read-only adapters for actual D019/D068-D072 retained artifacts and mappings."""
from dataclasses import asdict
from fractions import Fraction
from hashlib import sha256
import json
import re

from app.domain.branding import BrandingConfig
from app.domain.caption_track import PublishedCaptionTrack, build_synchronized_caption_track, serialize_srt, validate_caption_segments
from app.domain.delivery import DeliveryConfig, DELIVERY_PRESETS
from app.domain.production_quality import QAContext
from app.domain.publishing_package import CompositionTiming
from app.domain.publishing_package import validate_metadata, validate_chapters
from app.domain.render_result import delivery_profile
from app.domain.shorts import clip_captions
from app.domain.speech_alignment import SpeechAlignment
from app.domain.timeline import TimelineRevision

VIDEO_TYPES = {"video_render": ("original", "render"), "branded_video": ("branded", "finishing"),
               "delivery_video": ("delivery", "delivery"), "shorts_video": ("excerpt", "shorts"),
               "shorts_director_video": ("director", "director")}


def seconds(value): return float(Fraction(*value)) if type(value) is list else float(Fraction(str(value)))


def parse_srt(raw):
    text = raw.decode("utf-8-sig").replace("\r\n", "\n").strip()
    rows = []
    def clock(value):
        h, m, s, ms = map(int, re.split("[:,]", value))
        if m >= 60 or s >= 60: raise ValueError("Invalid SRT clock.")
        return ((h * 60 + m) * 60 + s) * 1000 + ms
    if not text: raise ValueError("Empty requested SRT.")
    for index, block in enumerate(re.split(r"\n\s*\n", text), 1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(index): raise ValueError("Invalid SRT numbering/content.")
        match = re.fullmatch(r"(\d{2,}:\d{2}:\d{2},\d{3}) --> (\d{2,}:\d{2}:\d{2},\d{3})", lines[1])
        if not match: raise ValueError("Invalid SRT timing syntax.")
        rows.append({"start_ms": clock(match[1]), "end_ms": clock(match[2]), "text": "\n".join(lines[2:])})
    return [asdict(s) for s in validate_caption_segments(rows)]


class ProjectQualitySources:
    def __init__(self, publishing):
        self.publishing, self.session, self.store = publishing, publishing.session, publishing.store
        self.workspace = self.session.repository.workspace

    def manifests(self): return {m.artifact_id: m for m in self.store.list_artifacts()}

    def candidates(self):
        selected = self.publishing.renders.index.selected().get("project:video_render")
        return sorted((m for m in self.manifests().values() if m.artifact_type in VIDEO_TYPES and
                      (m.metadata.get("project_id") == self.session.project.id or m.artifact_id == selected)),
                      key=lambda m: (m.created_at, m.artifact_id), reverse=True)

    def _bytes(self, manifest):
        if manifest.metadata.get("project_id") not in (None, self.session.project.id): raise ValueError("Related artifact belongs to another project.")
        raw = self.store.read_artifact(manifest.storage_key)
        if len(raw) != manifest.size_bytes or sha256(raw).hexdigest() != manifest.checksum: raise ValueError("Related artifact checksum mismatch.")
        return raw

    def _read(self, manifest): return json.loads(self._bytes(manifest))

    def _latest(self, values, kind, **metadata):
        found = [m for m in values.values() if m.artifact_type == kind and m.metadata.get("project_id") == self.session.project.id
                 and all(m.metadata.get(k) == v for k, v in metadata.items())]
        return max(found, key=lambda m: (m.created_at, m.artifact_id)) if found else None

    def _original(self, m, values):
        settings = m.metadata.get("desktop_dependencies", {}).get("request", {}).get("settings", {})
        timeline = TimelineRevision.from_payload(settings["timeline"])
        if timeline.project_id != self.session.project.id or m.metadata["render"]["timeline_id"] != timeline.id:
            raise ValueError("Original render Timeline ownership/identity mismatch.")
        current = self.publishing.timelines.current() if self.publishing.timelines else None
        selected = self.publishing.renders.index.selected().get("project:video_render")
        sections = {s.section_id: s for s in self.session.active_script.sections}
        stale = selected != m.artifact_id or current is None or current.timeline.id != timeline.id or any(
            c.media.section_id not in sections or sections[c.media.section_id].id != c.media.section_revision_id for c in timeline.clips)
        if hasattr(self.publishing.renders, "motion_current") and not self.publishing.renders.motion_current(m): stale = True
        for c in timeline.clips:
            for ref in (c.media.image, c.media.audio):
                row = values.get(ref.artifact_id)
                if row is None or row.checksum != ref.checksum: raise ValueError("Frozen Timeline references missing/changed source artifacts.")
        _, width, height = delivery_profile(timeline)
        motion = "static" if settings.get("profile") == "static-mp4-720p25-v1" else "unknown"
        if "zoom_intensity" in settings:
            motion = "static" if settings["zoom_intensity"] == settings["pan_intensity"] == "off" else "subtle"
        shots = [{"id": c.media.scene_id, "section_id": c.media.section_id, "start": float(c.start_frame * timeline.timebase.seconds),
                  "end": float(c.end_frame * timeline.timebase.seconds), "image_id": c.media.image.artifact_id,
                  "image_checksum": c.media.image.checksum, "width": c.media.image.width, "height": c.media.image.height,
                  "mode": timeline.fit_policy, "motion": motion} for c in timeline.clips]
        plan = self.publishing.plans.selected(); profile = None; planned = None
        if plan:
            binding = self.publishing.plan_scripts.active_binding()
            if binding and binding.video_plan_revision_id == plan.id and binding.script_id == self.session.active_script.script_id and not stale:
                profile, planned = plan.format.value, plan.target_duration_seconds
        facts = {"timeline_id": timeline.id, "stale": stale, "shots": shots,
                 "expected": {"width": width, "height": height, "fps": float(1 / timeline.timebase.seconds),
                              "frame_count": timeline.total_frames, "video_duration": float(timeline.video_duration),
                              "audio_duration": float(timeline.duration), "sample_rate": 48000},
                 "duration_profile": profile, "durations": {"planned_narration_seconds": planned,
                    "actual_narration_seconds": float(timeline.duration) if not stale else None, "original_video_seconds": float(timeline.video_duration)},
                 "mapping_ids": [[c.media.audio.artifact_id, c.media.audio.checksum, c.media.section_revision_id] for c in timeline.clips],
                 "branding_intervals": [], "intentional_silence": [], "verified_speech": [],
                 "captions": {"state": "not_requested"}}
        return facts, timeline, settings.get("captions"), CompositionTiming(timeline.duration, timeline.video_duration)

    def _composition(self, m, values, visiting=()):
        if m.artifact_id in visiting or len(visiting) > 4: raise ValueError("Invalid cyclic/deep derivation mapping.")
        kind = VIDEO_TYPES[m.artifact_type][0]
        if kind == "original": return self._original(m, values)
        identity = m.metadata["identity"]
        source_id = identity.get("source_artifact_id", identity.get("source_id"))
        source = values.get(source_id)
        if source is None or source.checksum != identity.get("source_checksum") or source.metadata.get("project_id") != self.session.project.id:
            raise ValueError("Derived video source ownership/checksum mismatch.")
        facts, timeline, captions, timing = self._composition(source, values, (*visiting, m.artifact_id))
        evidence = m.metadata[VIDEO_TYPES[m.artifact_type][1]]
        if kind == "delivery":
            config = DeliveryConfig.from_payload(identity["settings"])
            if identity["preset"] != asdict(DELIVERY_PRESETS[config.preset]): raise ValueError("Delivery preset identity mismatch.")
            if evidence.get("source_artifact_id") != source.artifact_id or evidence.get("source_checksum") != source.checksum:
                raise ValueError("Delivery evidence belongs to another source.")
            if (evidence["frame_count"] != facts["expected"]["frame_count"] or abs(seconds(evidence["video_duration"]) - facts["expected"]["video_duration"]) > .001):
                raise ValueError("Delivery composition differs from its verified source.")
            current = self.publishing.delivery.artifacts.current() if self.publishing.delivery else None
            facts["stale"] |= current != config
            facts["durations"]["delivery_video_seconds"] = seconds(evidence["video_duration"])
            if config.normalize: facts["delivery_targets"] = {"target_lufs": config.target_lufs, "target_peak": config.target_peak}
            return facts, timeline, captions, timing
        if kind != "branded": raise ValueError("Unsupported long-form derivation mapping.")
        config = BrandingConfig.from_payload(identity["settings"])
        current = self.publishing.finishing.branding.current() if self.publishing.finishing else None
        facts["stale"] |= current != config
        intro, outro = config.intro_seconds if config.intro else 0, config.outro_seconds if config.outro else 0
        insertion = Fraction(0)
        if intro and config.placement == "after_cold_open":
            boundary = identity["boundary"]
            if boundary["timeline_id"] != timeline.id: raise ValueError("Branding boundary belongs to another Timeline.")
            insertion = Fraction(boundary["audio_sample"], source.metadata["render"]["sample_rate"])
            if Fraction(boundary["audio_sample"], source.metadata["render"]["sample_rate"]) not in {c.audio_offset + c.duration for c in timeline.clips}:
                raise ValueError("Branding insertion is not an exact measured boundary.")
        timing = CompositionTiming(timeline.duration, Fraction(*evidence["video_duration"]), intro=intro, insertion=insertion, outro=outro)
        insertion = float(insertion)
        expected_frames = timeline.total_frames + round((intro + outro) * facts["expected"]["fps"])
        if evidence["frame_count"] != expected_frames: raise ValueError("Branding frame count differs from composition mapping.")
        for s in facts["shots"]:
            if s["start"] >= insertion: s["start"] += intro; s["end"] += intro
        cards = ([[insertion, insertion + intro]] if intro else []) + ([[seconds(evidence["video_duration"]) - outro, seconds(evidence["video_duration"])]] if outro else [])
        facts["branding_intervals"] = cards; facts["intentional_silence"] = cards
        facts["expected"].update(frame_count=expected_frames, video_duration=seconds(evidence["video_duration"]), audio_duration=seconds(evidence["audio_duration"]))
        facts["durations"]["branded_video_seconds"] = seconds(evidence["video_duration"])
        return facts, timeline, captions, timing

    def _word_captions(self, source, values, timeline, payload, timing, facts):
        # Select only already retained artifacts. Never call subtitles(create=True).
        subtitle = self._latest(values, "publishing_subtitles")
        if subtitle and (subtitle.metadata.get("identity", {}).get("source_id"), subtitle.metadata.get("identity", {}).get("source_checksum")) == (source.artifact_id, source.checksum):
            payload = subtitle.metadata["identity"]["track"]
        else: subtitle = None
        if payload is None:
            draft = self._latest(values, "publishing_draft")
            if draft:
                d = self._read(draft)
                if d.get("subtitle_id") and d.get("source_kind") == VIDEO_TYPES[source.artifact_type][0]:
                    facts["captions"] = {"state": "unavailable", "reason": "Selected publishing subtitles are missing or do not match this source."}
            return
        try:
            published = PublishedCaptionTrack.from_payload(payload); track = published.track
            if track.timeline_id != timeline.id or track.project_id != self.session.project.id: raise ValueError("Caption Timeline/ownership mismatch.")
            if self.publishing.captions.published(track.id) != published: raise ValueError("Published caption reference is no longer current/valid.")
            for id, digest in ((published.track_artifact_id, published.track_checksum), (published.srt_artifact_id, published.srt_checksum), (published.ass_artifact_id, published.ass_checksum)):
                if id not in values or values[id].checksum != digest: raise ValueError("Caption artifact identity/checksum mismatch.")
                self._bytes(values[id])
            alignments = {}
            digests = []
            for id in track.alignment_ids:
                manifest = next((m for m in values.values() if m.artifact_type == "desktop_speech_alignment" and m.metadata.get("value_id") == id), None)
                if manifest is None: raise ValueError("Caption alignment is missing.")
                alignment = self.publishing.captions.alignments.alignment(id)
                latest = self.publishing.captions.alignments.latest(alignment.section_id, revision_id=alignment.revision_id, audio_artifact_id=alignment.audio_artifact_id)
                if latest != alignment: raise ValueError("Caption alignment identities are stale.")
                alignments[alignment.section_id] = alignment
                digests.append([manifest.artifact_id, manifest.checksum])
            texts = {id: self.session.active_script.section(id).text for id in alignments}
            desired = build_synchronized_caption_track(timeline, texts, alignments, language=track.language)
            if desired != track: raise ValueError("Caption track no longer matches selected audio/text/alignment.")
            segments = timing.captions(track.segments)
            if subtitle:
                if self._bytes(subtitle) != serialize_srt(segments).encode("utf-8"): raise ValueError("Shifted SRT differs from its verified composition mapping.")
            elif self._bytes(values[published.srt_artifact_id]) != serialize_srt(track.segments).encode("utf-8"):
                raise ValueError("SRT content differs from retained caption timing.")
            speech = []
            for c in timeline.clips:
                alignment = alignments[c.media.section_id]; span = c.media.audio
                for w in alignment.words:
                    if span.start_sample <= w.start_frame < w.end_frame <= span.end_sample:
                        a = c.audio_offset + Fraction(w.start_frame - span.start_sample, span.sample_rate)
                        b = c.audio_offset + Fraction(w.end_frame - span.start_sample, span.sample_rate)
                        shift = timing.intro if a >= timing.insertion else 0
                        speech.append([float(a + shift), float(b + shift)])
            facts["verified_speech"] = speech
            facts["captions"] = {"state": "valid", "segments": [asdict(s) for s in segments], "track_id": track.id,
                                 "srt_id": subtitle.artifact_id if subtitle else published.srt_artifact_id,
                                 "alignment_artifacts": digests, "speech_method": "measured_word_alignment_v1"}
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            facts["captions"] = {"state": "stale", "reason": str(exc)}

    def _short(self, m, values):
        kind = VIDEO_TYPES[m.artifact_type][0]; identity = m.metadata["identity"]; evidence = m.metadata[VIDEO_TYPES[m.artifact_type][1]]
        facts = {"stale": False, "expected": {"width": 1080, "height": 1920, "fps": float(Fraction(str(evidence["fps"]))),
                 "frame_count": evidence["frame_count"], "video_duration": seconds(evidence["video_duration"]),
                 "audio_duration": seconds(evidence["audio_duration"]), "sample_rate": 48000}, "duration_profile": "short",
                 "duration_bounds": [30, 60], "durations": {}, "narration_unpadded": False,
                 "branding_intervals": [], "intentional_silence": [], "verified_speech": [], "shots": [], "captions": {"state": "not_requested"}}
        if kind == "director":
            p = identity["source"]; parent = values.get(p["source_id"])
            if parent is None or parent.checksum != p["source_checksum"]: raise ValueError("Director parent source identity mismatch.")
            parent_facts, _, _, _ = self._composition(parent, values)
            candidate, language = m.metadata["candidate_id"], m.metadata["director_language"]
            selector = {"candidate_id": candidate, "director_language": language}
            for field, record_kind in (("script_id", "script"), ("storyboard_id", "storyboard")):
                latest = self._latest(values, "shorts_director_" + record_kind, **selector)
                facts["stale"] |= latest is None or latest.artifact_id != identity[field]
            narration = values.get(identity["narration_id"])
            if narration is None: raise ValueError("Director narration identity is missing.")
            narration = self._read(narration)
            facts["durations"] = {"actual_narration_seconds": seconds(narration["duration"]), "planned_narration_seconds": narration["target"]}
            voice = self._latest(values, "shorts_director_voice_choice", **selector)
            if voice:
                facts["stale"] |= self._read(voice) != {"selection": narration["identity"]["selection"], "variant": narration["identity"]["variant"]}
            selected_audio = self._latest(values, "shorts_director_narration_selection", **selector)
            if selected_audio: facts["stale"] |= self._read(selected_audio)["narration_id"] != identity["narration_id"]
            finish = self._latest(values, "shorts_director_finish", **selector)
            if finish: facts["stale"] |= self._read(finish) != identity["finish"]
            selected = self._latest(values, "shorts_director_selection", **selector)
            facts["stale"] |= bool(parent_facts["stale"] or selected is None or self._read(selected)["video_id"] != m.artifact_id)
            board = identity["storyboard"]; images = {s["id"]: image for s, image in zip(board["scenes"], identity["images"])}
            if self._read(values[identity["storyboard_id"]]) != board: raise ValueError("Director storyboard mapping mismatch.")
            dimensions = {s.get("image_id"): (s.get("width"), s.get("height")) for s in parent_facts.get("shots", [])}
            for s in board["scenes"]:
                img = images[s["id"]]; row = values.get(img[0])
                if row is None or row.checksum != img[1]: raise ValueError("Director storyboard image identity changed.")
                width, height = row.metadata.get("width"), row.metadata.get("height")
                if not width or not height: width, height = dimensions.get(img[0], (None, None))
                facts["shots"].append({"id": s["id"], "section_id": None, "start": s["start_frame"] / facts["expected"]["fps"],
                    "end": s["end_frame"] / facts["expected"]["fps"], "image_id": img[0], "image_checksum": img[1], "mode": s["mode"],
                    "motion": "static" if s["zoom"] == s["pan"] == "off" else "subtle",
                    "crop_fraction": min(1, (9 / 16) * height / width) if width and height else 1})
            caption = identity["captions"]
            if caption.get("method") != "verified_sentence_assembly": raise ValueError("Unsupported Director caption timing method.")
            if (caption["audio_id"], caption["audio_checksum"]) != (narration["audio_id"], narration["checksum"]):
                raise ValueError("Director caption/narration identity mismatch.")
            script = self._read(values[identity["script_id"]])
            if caption["text_checksum"] != sha256(script["narration"].encode("utf-8")).hexdigest():
                raise ValueError("Director caption/script identity mismatch.")
            if selected:
                selection = self._read(selected); srt = values.get(selection["srt_id"])
                if srt is None: raise ValueError("Director SRT is missing.")
                parsed = parse_srt(self._bytes(srt))
                if parsed != caption["segments"]: raise ValueError("Director SRT timing differs from new narration.")
            # Caption spans alone cannot serve as independent speech evidence.
            # Technical timing is checked; coverage remains unavailable without the measured audio map.
            facts["captions"] = {"state": "valid", "segments": caption["segments"], "speech_method": "verified_sentence_assembly",
                                  "audio_id": caption["audio_id"], "audio_checksum": caption["audio_checksum"]}
            facts["mapping_ids"] = [identity["script_id"], identity["narration_id"], identity["storyboard_id"], caption["audio_id"], caption["audio_checksum"]]
        else:
            # D071 freezes the original Timeline and explicit excerpt edit inside output identity.
            parent = values.get(identity["source_id"])
            if parent is None or parent.checksum != identity["source_checksum"]: raise ValueError("Excerpt parent identity mismatch.")
            parent_facts, timeline, _, timing = self._composition(parent, values)
            revision = self._latest(values, "shorts_edit", candidate_id=m.metadata["candidate_id"])
            edit = identity["edit"]
            facts["stale"] = parent_facts["stale"] or revision is None or revision.artifact_id != edit["revision_id"]
            start, end = seconds(evidence["source_start"]), seconds(evidence["source_end"])
            for s in parent_facts["shots"]:
                lo, hi = max(start, s["start"]), min(end, s["end"])
                if lo < hi:
                    s = dict(s); s.update(start=lo - start, end=hi - start, mode=edit["style"]["mode"])
                    s["crop_fraction"] = min(1, (9 / 16) * s["height"] / s["width"]); facts["shots"].append(s)
            facts["durations"]["actual_narration_seconds"] = seconds(evidence["speech_end"]) - seconds(evidence["speech_start"])
            selected = self._latest(values, "shorts_selection", candidate_id=m.metadata["candidate_id"])
            facts["stale"] |= selected is None or self._read(selected)["video_id"] != m.artifact_id
            if edit["style"]["captions"]:
                if selected and self._read(selected).get("srt_id"):
                    srt = values[self._read(selected)["srt_id"]]
                    ci = identity["caption_identity"]; original_srt = values[ci["artifact_id"]]
                    if original_srt.checksum != ci["checksum"]: raise ValueError("Excerpt caption source checksum mismatch.")
                    self._bytes(original_srt)
                    retained = original_srt.metadata["identity"]
                    if (retained["source_id"], retained["source_checksum"]) != (parent.artifact_id, parent.checksum):
                        raise ValueError("Excerpt caption source identity mismatch.")
                    published = PublishedCaptionTrack.from_payload(retained["track"])
                    if published.track.id != ci["track_id"] or published.track.timeline_id != timeline.id:
                        raise ValueError("Excerpt caption Timeline identity mismatch.")
                    self._word_captions(parent, values, timeline, retained["track"], timing, parent_facts)
                    if parent_facts["captions"]["state"] != "valid": raise ValueError("Excerpt source caption mapping mismatch.")
                    expected = clip_captions(timing.captions(published.track.segments), Fraction(str(start)), Fraction(str(end)))
                    parsed = parse_srt(self._bytes(srt))
                    if parsed != [asdict(s) for s in expected]: raise ValueError("Excerpt SRT rebasing differs from verified source timing.")
                    facts["verified_speech"] = [[max(a, start)-start, min(b, end)-start] for a, b in parent_facts["verified_speech"] if max(a, start) < min(b, end)]
                    facts["captions"] = {"state": "valid", "segments": parsed, "srt_id": srt.artifact_id,
                                          "speech_method": "verified_rebased_source_captions"}
                else: facts["captions"] = {"state": "unavailable", "reason": "Requested excerpt captions are missing."}
        return facts

    def capture(self, source_id):
        values = self.manifests(); m = values.get(source_id)
        if m is None or m.artifact_type not in VIDEO_TYPES: raise ValueError("Select a retained production MP4.")
        kind, report_key = VIDEO_TYPES[m.artifact_type]
        evidence = m.metadata.get(report_key, {})
        base = {"owner_ok": m.metadata.get("project_id") == self.session.project.id, "integrity_errors": [], "stale": False,
                "captions": {"state": "not_requested"}, "mapping_available": False}
        try:
            if not base["owner_ok"]: raise ValueError("Source project ownership mismatch.")
            if evidence.get("checksum") != m.checksum or evidence.get("size_bytes") != m.size_bytes:
                raise ValueError("Retained render evidence differs from manifest identity.")
            if not evidence.get("fully_decoded"): raise ValueError("Required completed-render evidence is missing: identity mismatch.")
            if kind in {"excerpt", "director"}: facts = self._short(m, values)
            else:
                facts, timeline, captions, timing = self._composition(m, values)
                self._word_captions(m, values, timeline, captions, timing, facts)
            base.update(facts, mapping_available=True)
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
            # Missing mapping does not invent narration acceptance. Explicit mismatches block.
            reason = str(exc)
            base["mapping_reason"] = reason
            if isinstance(exc, OSError) or any(word in reason.lower() for word in ("mismatch", "differs", "changed")): base["integrity_errors"].append(reason)
        # A boundary split belonging to the same scene is continuity, not a new shot.
        merged = []
        for s in base.get("shots", []):
            if merged and all(merged[-1].get(k) == s.get(k) for k in ("id", "image_checksum", "mode")) and abs(merged[-1]["end"] - s["start"]) < .0001:
                merged[-1]["end"] = s["end"]
            else: merged.append(dict(s))
        base["shots"] = merged
        base["publishing_findings"] = []
        if kind in {"original", "branded", "delivery"}:
            draft = self._latest(values, "publishing_draft")
            if draft:
                try:
                    d = self._read(draft); saved = draft.metadata.get("context", {})
                    if d["source_kind"] == kind:
                        if (saved.get("source_artifact_id"), saved.get("source_checksum"), saved.get("timeline_id")) != (m.artifact_id, m.checksum, base.get("timeline_id")):
                            raise ValueError("Publishing draft source/Timeline is stale.")
                        validate_metadata(d["metadata"], allowed_urls=d["links"])
                        if d["chapters_enabled"]: validate_chapters(d["chapters"], Fraction(*evidence["video_duration"]))
                        if d["thumbnail_id"]:
                            thumbnail = values[d["thumbnail_id"]]; self.publishing.verify_thumbnail(thumbnail)
                        if not d["approved"]: raise ValueError("Publishing metadata requires explicit editorial review.")
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    base["publishing_findings"].append({"rule": "PUBLISHING_ASSETS_REVIEW", "severity": "REVIEW", "reason": str(exc)})
        try:
            stat = self.store._artifact_path(m.storage_key).stat(); base["file_signature"] = [stat.st_size, stat.st_mtime_ns]
        except OSError: base["file_signature"] = None
        return QAContext(self.session.project.id, m.artifact_id, m.checksum, m.size_bytes, kind,
                         self.store._artifact_path(m.storage_key), base)

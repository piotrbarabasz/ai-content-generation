"""Repurpose verified media; parent script, audio, images and Timeline are read-only."""
import asyncio
from copy import deepcopy
from fractions import Fraction
from hashlib import file_digest, sha256
from io import BytesIO
import shutil
from uuid import uuid4
import zipfile

from app.domain.caption_track import serialize_srt
from app.domain.dependencies import canonical_json, content_fingerprint
from app.domain.plan_script import planned_section_identity
from app.domain.shorts import (ALGORITHM, ShortStyle, clip_captions, mapped_interval, output_identity, overlap, overlays, ratio, speech_interval)
from app.providers.shorts import ShortsRenderer
from app.runtime.media_process import RenderCanceled
from app.storage.paths import contained_path
from app.storage.shorts import ProjectShorts


class ShortsService:
    def __init__(self, publishing, renderer: ShortsRenderer):
        self.publishing, self.renderer = publishing, renderer
        self.store, self.session = publishing.store, publishing.session
        self.artifacts = ProjectShorts(self.store, self.session.project.id)
        self._boundaries = {}

    def source(self, kind="original"):
        value = self.publishing.source(kind)
        plan = self.publishing.plans.selected()
        if plan is None or plan.format.value != "standard": raise ValueError("Shorts requires a completed Standard project.")
        binding = self.publishing.plan_scripts.active_binding()
        if binding is None or binding.video_plan_revision_id != plan.id or binding.script_id != self.session.active_script.script_id:
            raise ValueError("Current Standard plan/script binding is required.")
        self.publishing.timing(kind)
        return value

    def boundaries(self, timeline):
        if timeline.id in self._boundaries: return self._boundaries[timeline.id]
        result = set()
        for c in timeline.clips:
            span = c.media.audio
            for edge in self.publishing.renders.media.boundaries(c.media):
                if span.start_sample <= edge <= span.end_sample:
                    result.add(c.audio_offset + Fraction(edge - span.start_sample, span.sample_rate))
        self._boundaries[timeline.id] = tuple(sorted(result))
        return self._boundaries[timeline.id]

    def pool(self, kind="original", target=45):
        if type(target) is not int or not 30 <= target <= 60: raise ValueError("Target must be 30-60 seconds.")
        source, evidence, timeline = self.source(kind)
        timing = self.publishing.timing(kind); fps = Fraction(str(evidence["fps"]))
        boundaries = self.boundaries(timeline)
        plan = self.publishing.plans.selected()
        owners = {planned_section_identity(plan.project_id, s.id): g for g in plan.groups for s in g.sections}
        blocks = []
        for c in timeline.clips:
            g = owners.get(c.media.section_id)
            if g is None: raise ValueError("Timeline section has no current plan group.")
            if blocks and blocks[-1][0].id == g.id: blocks[-1][1].append(c)
            else: blocks.append((g, [c]))
        result = []
        audio_cache = {}
        for group, clips in blocks:
            if group.kind != "chapter": continue
            lo, hi = clips[0].audio_offset, clips[-1].audio_offset + clips[-1].duration
            edges = [t for t in boundaries if lo <= t <= hi]
            ranges = []
            for a in edges:
                for b in edges:
                    if not 30 <= b-a <= 60: continue
                    try: start, end = mapped_interval(a, b, timing, fps, boundaries)
                    except ValueError: continue
                    ranges.append((a, b, start, end))
            # Prefer the whole chapter, then whole sections, then measured scenes/sentences.
            section_edges = {c.audio_offset for i,c in enumerate(clips) if i == 0 or c.media.section_id != clips[i-1].media.section_id}
            section_edges.add(hi)
            ranges.sort(key=lambda r: (0 if r[0] == lo and r[1] == hi else 1 if r[0] in section_edges and r[1] in section_edges else 2,
                                        abs(float(r[3]-r[2]) - target), r[0]))
            for a,b,start,end in ranges[:8]:
                selected = [c for c in clips if c.audio_offset < b and c.audio_offset+c.duration > a]
                sections = list(dict.fromkeys(c.media.section_id for c in selected))
                text = []
                for section_id in sections:
                    section = self.session.active_script.section(section_id)
                    # Supply only spoken coverage where measured sentence text is available.
                    for c in [c for c in selected if c.media.section_id == section_id]:
                        if c.media.audio.artifact_id not in audio_cache:
                            audio_cache[c.media.audio.artifact_id] = self.publishing.renders.media.audio._read(c.media.audio.artifact_id)[1]
                        audio = audio_cache[c.media.audio.artifact_id]
                        mapping = audio.speech_boundary_map
                        if mapping and mapping.quality == "measured_sentence_blocks":
                            for block in mapping.blocks:
                                t0 = c.audio_offset + Fraction(block.start_frame-c.media.audio.start_sample, c.media.audio.sample_rate)
                                t1 = c.audio_offset + Fraction(block.end_frame-c.media.audio.start_sample, c.media.audio.sample_rate)
                                if a <= t0 < t1 <= b: text.append(section.text[block.source_start:block.source_end])
                        else: text.append("[Timing review needed: section context] " + section.text)
                item = {"source_kind": kind, "source_id": source.artifact_id, "source_checksum": source.checksum,
                    "timeline_id": timeline.id, "original_start": ratio(a), "original_end": ratio(b),
                    "start": ratio(start), "end": ratio(end), "duration": ratio(end-start), "title": group.title,
                    "topic": group.title, "group_ids": [group.id], "section_ids": sections,
                    "scene_ids": [c.media.scene_id for c in selected], "text": " ".join(dict.fromkeys(text)),
                    "hook_assessment": "Review the opening for context and dangling references.",
                    "reason": "Verified contiguous chapter coverage; review beginning, payoff and visual crop.",
                    "status": "REVIEW", "headline": ""}
                item["id"] = "short_" + content_fingerprint([source.artifact_id, source.checksum, timeline.id, ratio(a), ratio(b)])[:24]
                result.append(item)
        return result

    async def analyze(self, kind="original", count=3, target=45, *, canceled=lambda: False):
        if type(count) is not int or not 1 <= count <= 10: raise ValueError("Choose 1-10 candidates.")
        pool = self.pool(kind, target)
        if not pool: raise ValueError("REVIEW: no coherent 30-60 second chapter interval at verified boundaries.")
        llm = self.publishing.llm
        if llm:
            fields = {k: {"type": "string", "minLength": 1, "maxLength": 500} for k in ("interval_id", "title", "hook_assessment", "reason", "headline")}
            fields["interval_id"] = {"type": "string", "enum": [p["id"] for p in pool]}
            schema = {"type": "object", "additionalProperties": False, "required": ["candidates"], "properties": {
                "candidates": {"type": "array", "minItems": count, "maxItems": count, "items": {"type": "object",
                    "additionalProperties": False, "required": list(fields), "properties": fields}}}}
            prompt = ("Select distinct self-contained spoken excerpts from supplied verified intervals only. No invented timestamps or narration. "
                "Prefer complete explanations with understandable openings and payoffs; reject dangling pronouns and repetitive content. "
                "Assess existing facts only; headline must be supported by supplied spoken coverage. Editorial review required. Return interval IDs "
                "and assessments; timing/references are resolved by application. Intervals: " + canonical_json([
                    {k:p[k] for k in ("id", "title", "section_ids", "scene_ids", "text")} for p in pool]))
            value = await asyncio.to_thread(llm.generate_structured, prompt, deepcopy(schema))
            if type(value) is not dict or set(value) != {"candidates"} or type(value["candidates"]) is not list or len(value["candidates"]) != count:
                raise ValueError("Invalid structured Short analysis.")
            selected = []
            for v in value["candidates"]:
                if type(v) is not dict or set(v) != set(fields) or any(type(x) is not str or not x.strip() or len(x)>500 for x in v.values()):
                    raise ValueError("Invalid Short assessment fields.")
                item = next((p for p in pool if p["id"] == v["interval_id"]), None)
                if item is None: raise ValueError("LLM referenced an unknown interval/section/scene.")
                selected.append({**item, **{k:v[k] for k in fields if k != "interval_id"}})
            origin = "LLM suggestions; editorial review required"
        else:
            selected = []
            for item in pool:
                if any(set(item["group_ids"]) & set(p["group_ids"]) or overlap(item,p) for p in selected): continue
                selected.append(item)
                if len(selected) == count: break
            origin = "Heuristic section/sentence discovery; no retention or virality prediction"
        if len({p["id"] for p in selected}) != len(selected) or any(overlap(a,b) for i,a in enumerate(selected) for b in selected[i+1:]):
            raise ValueError("Suggestions repeat or overlap; review distinct intervals.")
        if canceled(): raise RenderCanceled("Analysis canceled; retained candidates unchanged.")
        source, _, timeline = self.source(kind)
        if any(p["source_checksum"] != source.checksum or p["source_id"] != source.artifact_id or p["timeline_id"] != timeline.id for p in selected):
            raise ValueError("Source changed during analysis.")
        return self.artifacts.save("analysis", {"version": 1, "origin": origin, "requested_count": count,
            "target_seconds": target, "candidates": selected, "status": "REVIEW"})

    def candidates(self):
        values = {}
        for m in self.artifacts.history("analysis"):
            for p in self.artifacts.read(m)["candidates"]:
                # Reanalysis never overwrites a retained editorial assessment.
                values.setdefault(p["id"], p)
        return list(values.values())

    def candidate(self, identity):
        value = next((p for p in self.candidates() if p["id"] == identity), None)
        if value is None: raise ValueError("Unknown Short candidate.")
        source, _, timeline = self.source(value["source_kind"])
        if (source.artifact_id, source.checksum, timeline.id) != (value["source_id"], value["source_checksum"], value["timeline_id"]):
            raise ValueError("STALE: candidate source changed; analyze the current source.")
        return value

    def decide(self, identity, accepted):
        if type(accepted) is not bool: raise ValueError("Choose accept or reject explicitly.")
        if accepted: self.candidate(identity)
        elif not any(p["id"] == identity for p in self.candidates()): raise ValueError("Unknown Short candidate.")
        return self.artifacts.save("decision", {"candidate_id": identity, "accepted": accepted}, candidate_id=identity)

    def accepted(self, identity):
        m = self.artifacts.latest("decision", identity)
        return bool(m and self.artifacts.read(m)["accepted"])

    def edit(self, identity):
        m = self.artifacts.latest("edit", identity)
        if m: return m, self.artifacts.read(m)
        p = self.candidate(identity)
        return None, {"candidate_id": identity, "original_start": p["original_start"], "original_end": p["original_end"],
            "title": p["title"][:100], "style": ShortStyle().to_payload(), "parent_revision_id": None}

    def save_edit(self, value, expected=None):
        p = self.candidate(value["candidate_id"])
        latest = self.artifacts.latest("edit", p["id"])
        if (latest.artifact_id if latest else None) != expected: raise ValueError("Short edit changed; reload before saving.")
        required = {"candidate_id", "original_start", "original_end", "title", "style", "parent_revision_id"}
        if set(value) != required or type(value["title"]) is not str or not value["title"].strip() or len(value["title"])>100:
            raise ValueError("Invalid Short edit structure/title.")
        self.prepare(p, value)
        if latest:
            previous = self.artifacts.read(latest)
            if all(value[k] == previous[k] for k in required - {"parent_revision_id"}): return latest
        result = {**value, "parent_revision_id": expected}
        return self.artifacts.save("edit", result, candidate_id=p["id"])

    def prepare(self, p, edit):
        source, evidence, timeline = self.source(p["source_kind"])
        if (source.artifact_id, source.checksum, timeline.id) != (p["source_id"], p["source_checksum"], p["timeline_id"]):
            raise ValueError("STALE: Short references a changed source.")
        start, end = mapped_interval(Fraction(*edit["original_start"]), Fraction(*edit["original_end"]),
            self.publishing.timing(p["source_kind"]), Fraction(str(evidence["fps"])), self.boundaries(timeline))
        style = ShortStyle.from_payload(edit["style"])
        timing = self.publishing.timing(p["source_kind"])
        shots = []
        a,b = Fraction(*edit["original_start"]), Fraction(*edit["original_end"])
        for c in timeline.clips:
            if c.audio_offset >= b or c.audio_offset+c.duration <= a: continue
            # Crop changes follow actual original shot frame boundaries.
            shift = timing.intro if c.audio_offset >= timing.insertion else 0
            lo = max(start, c.start_frame * timeline.timebase.seconds + shift)
            hi = min(end, c.end_frame * timeline.timebase.seconds + shift)
            if lo < hi: shots.append({"scene_id": c.media.scene_id, "section_id": c.media.section_id,
                "start": ratio(lo-start), "end": ratio(hi-start)})
        if set(dict(style.focal_points)) - {s["scene_id"] for s in shots}: raise ValueError("Crop references a shot outside the excerpt.")
        caption_id, segments = None, ()
        if style.captions:
            subtitle = self.publishing.subtitles(p["source_kind"])
            identity = subtitle.metadata["identity"]
            if identity["track"]["track"]["timeline_id"] != timeline.id or identity["source_id"] != source.artifact_id or identity["source_checksum"] != source.checksum:
                raise ValueError("Captions do not belong to this exact source and Timeline.")
            from app.domain.caption_track import PublishedCaptionTrack
            track = PublishedCaptionTrack.from_payload(identity["track"]).track
            segments = clip_captions(timing.captions(track.segments), start, end)
            if not segments: raise ValueError("No measured captions overlap the excerpt.")
            caption_id = {"artifact_id": subtitle.artifact_id, "checksum": subtitle.checksum, "track_id": track.id}
        overlays(style, segments)
        return source, evidence, timeline, start, end, shots, style, segments, caption_id

    def stage(self, source):
        root = contained_path(self.session.repository.workspace, "work/shorts/" + uuid4().hex); root.mkdir(parents=True)
        with self.store.open_artifact_id(source.artifact_id) as stream, (root/"source.mp4").open("xb") as target:
            shutil.copyfileobj(stream, target, 1024*1024)
        with (root/"source.mp4").open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != source.checksum: raise ValueError("Source changed while staging.")
        return root

    async def preview(self, identity, edit, time=None, *, canceled=lambda: False):
        if self.renderer is None: raise ValueError("Configure trusted local FFmpeg/FFprobe for Shorts previews.")
        p = self.candidate(identity)
        source,e,t,a,b,shots,style,segments,_ = self.prepare(p, edit)
        relative = Fraction(time) if time is not None else Fraction(0)
        if not 0 <= relative < b-a: raise ValueError("Preview time outside excerpt.")
        raw = await self.renderer.preview(self.stage(source), e, a+relative, shots,
            style, segments, canceled=canceled, relative=relative)
        if self.source(p["source_kind"])[0].artifact_id != source.artifact_id: raise ValueError("Source changed during preview.")
        return raw

    def identity(self, p, revision):
        if self.renderer is None: raise ValueError("Configure trusted local FFmpeg/FFprobe for Shorts rendering.")
        edit = self.artifacts.read(revision)
        values = self.prepare(p, edit)
        source, _, timeline, _, _, _, _, _, caption = values
        identity, fingerprint = output_identity(source, timeline.id, {"revision_id": revision.artifact_id, **edit}, self.renderer.identity(), caption)
        return values, identity, fingerprint

    def current_output(self, identity):
        p = self.candidate(identity); revision = self.artifacts.latest("edit", identity)
        if revision is None: raise ValueError("Save the Short edit first.")
        _, _, fingerprint = self.identity(p, revision)
        m = self.artifacts.selected(identity)
        if m is None or m.metadata.get("fingerprint") != fingerprint: raise ValueError("MISSING/STALE: render this Short revision.")
        return m

    async def render(self, identity, *, canceled=lambda: False, progress=lambda *_: None):
        p = self.candidate(identity)
        if not self.accepted(identity): raise ValueError("Accept the excerpt after editorial/audio review before rendering.")
        revision = self.artifacts.latest("edit", identity)
        if revision is None: raise ValueError("Save a Short edit first.")
        values, output_id, fingerprint = self.identity(p, revision)
        if canceled(): raise RenderCanceled("Short canceled before rendering.")
        cached = self.artifacts.cached(fingerprint)
        if cached: return cached
        source,e,t,a,b,shots,style,segments,caption = values
        root = self.stage(source)
        edit = self.artifacts.read(revision)
        audio_bounds = speech_interval(Fraction(*edit["original_start"]), Fraction(*edit["original_end"]), self.publishing.timing(p["source_kind"]))
        report = await self.renderer.render(root,e,a,b,shots,style,segments,canceled=canceled,progress=progress,audio_bounds=audio_bounds)
        if canceled(): raise RenderCanceled("Short canceled before publication.")
        latest = self.artifacts.latest("edit", identity)
        if latest.artifact_id != revision.artifact_id or self.identity(p, revision)[1] != output_id or not self.accepted(identity):
            raise ValueError("Short edit/source/decision changed while rendering.")
        if (not report.get("fully_decoded") or report.get("status") != "READY" or report.get("source_start") != ratio(a)
                or report.get("source_end") != ratio(b) or (report.get("width"),report.get("height")) != (1080,1920)
                or report.get("video_codec") != "h264" or report.get("audio_codec") != "aac"
                or report.get("sample_rate") != 48000 or Fraction(str(report["fps"])) != Fraction(str(e["fps"]))
                or abs(Fraction(*report["video_duration"])-(b-a)) > Fraction(2,1000000) or report.get("frame_count") != (b-a)*Fraction(str(e["fps"]))
                or abs(Fraction(*report["audio_duration"])-(b-a)) > Fraction(1024,48000)):
            raise ValueError("Incomplete or invalid Short evidence; previous valid selection retained.")
        with (root/"short.mp4").open("rb") as stream:
            class Checked:
                def __init__(self): self.digest, self.size = sha256(), 0
                def read(self, n):
                    if canceled(): raise RenderCanceled("Short publication canceled.")
                    raw=stream.read(n); self.digest.update(raw); self.size+=len(raw)
                    if not raw and (self.digest.hexdigest()!=report["checksum"] or self.size!=report["size_bytes"]):
                        raise ValueError("Short bytes changed after decode.")
                    return raw
            video = self.store.import_stream("short.mp4", Checked(), {"artifact_type":"shorts_video", "module_name":"desktop_shorts",
                "project_id":self.session.project.id, "candidate_id":identity, "fingerprint":fingerprint, "identity":output_id, "shorts":report})
        edit = self.artifacts.read(revision)
        metadata = {"parent_project_id":self.session.project.id, "source_artifact_id":source.artifact_id, "source_checksum":source.checksum,
            "timeline_id":t.id, "start":ratio(a), "end":ratio(b), "original_start":edit["original_start"], "original_end":edit["original_end"],
            "duration":report["video_duration"], "title":edit["title"], "section_ids":list(dict.fromkeys(s["section_id"] for s in shots)),
            "framing":style.to_payload(), "caption_identity":caption, "renderer":self.renderer.identity(), "algorithm":ALGORITHM,
            "output_checksum":video.checksum, "video_artifact_id":video.artifact_id, "edit_revision_id":revision.artifact_id,
            "video_codec":"h264", "audio_codec":"aac", "resolution":[1080,1920], "fps":report["fps"]}
        meta = self.artifacts.save("metadata",metadata,candidate_id=identity,video_id=video.artifact_id)
        srt = self.store.save_artifact("captions.srt", serialize_srt(segments), {"artifact_type":"shorts_srt", "module_name":"desktop_shorts",
            "project_id":self.session.project.id,"video_id":video.artifact_id}) if segments else None
        # A small per-Short archive, not a second publishing package engine.
        archive = BytesIO()
        with zipfile.ZipFile(archive,"w",zipfile.ZIP_STORED) as z:
            with z.open("short-01/short.mp4","w") as target, self.store.open_artifact_id(video.artifact_id) as stream: shutil.copyfileobj(stream,target,1024*1024)
            z.writestr("short-01/metadata.json",canonical_json(metadata))
            description = edit["title"] + "\n\nExcerpt from: " + self.publishing.plans.selected().working_title
            z.writestr("short-01/description.txt",description)
            if srt: z.writestr("short-01/captions.srt",self.artifacts.records.verified_bytes(srt))
        package = self.store.save_artifact("short-package.zip",archive.getvalue(), {"artifact_type":"shorts_package","module_name":"desktop_shorts",
            "project_id":self.session.project.id,"video_id":video.artifact_id})
        self.artifacts.save("selection",{"candidate_id":identity,"video_id":video.artifact_id,"metadata_id":meta.artifact_id,
            "srt_id":srt.artifact_id if srt else None,"package_id":package.artifact_id},candidate_id=identity)
        return video

    async def render_accepted(self, *, canceled=lambda:False, progress=lambda *_:None):
        result=[]
        for p in self.candidates():
            if not self.accepted(p["id"]): continue
            try: self.candidate(p["id"])
            except ValueError as exc:
                if not str(exc).startswith("STALE:"): raise
                progress("Skipping retained stale Short; analyze/review the current source", 0, 1)
                continue
            result.append(await self.render(p["id"],canceled=canceled,progress=progress))
        return result

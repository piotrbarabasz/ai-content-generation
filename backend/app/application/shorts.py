"""Repurpose verified media; parent script, audio, images and Timeline are read-only."""
import asyncio
from copy import copy, deepcopy
from fractions import Fraction
from hashlib import sha256
import os
from uuid import uuid4
from types import SimpleNamespace
import zipfile

from app.domain.caption_track import serialize_srt
from app.domain.dependencies import canonical_json, content_fingerprint
from app.domain.plan_script import planned_section_identity
from app.domain.shorts import (ALGORITHM, ShortStyle, ShortAnalysisError, clip_captions, excerpt_capacity,
    mapped_interval, output_identity, overlap, overlays, ratio, select_excerpts, speech_interval)
from app.providers.shorts import ShortsRenderer
from app.runtime.media_process import RenderCanceled
from app.storage.paths import contained_path
from app.storage.shorts import ProjectShorts
from app.storage.media_snapshot import MediaSnapshotStore
from app.runtime.media_worker import media_worker, check_canceled


class ShortsService:
    def __init__(self, publishing, renderer: ShortsRenderer):
        self.publishing, self.renderer = publishing, renderer
        self.store, self.session = publishing.store, publishing.session
        self.artifacts = ProjectShorts(self.store, self.session.project.id)
        self._boundaries = {}
        self._rendering = set()

    def media_reader(self):
        """Capture SQLite on its owner, then compose file-only media readers."""
        reader, verify_current = self.analysis_reader('original')
        if reader is self:
            reader = copy(self)
            reader.publishing = copy(self.publishing)
            reader.session = SimpleNamespace(project=self.session.project,active_script=self.session.active_script,
                repository=SimpleNamespace(workspace=self.session.repository.workspace))
            reader.publishing.session = reader.session
        publishing = reader.publishing
        publishing._original_verification = getattr(self.publishing,'_original_verification',None)
        repository = publishing.session.repository
        store = MediaSnapshotStore(reader.store, repository=repository)
        reader.store = publishing.store = store
        reader.artifacts = ProjectShorts(store, self.session.project.id)
        reader.renderer = copy(self.renderer)
        reader._boundaries = dict(self._boundaries)
        if hasattr(publishing.renders, 'index'):
            from app.storage.timeline import ProjectTimelineMedia
            from app.storage.video_plans import ProjectVideoPlans
            from app.storage.plan_script import ProjectPlanScripts
            from app.storage.timeline_edits import ProjectTimelineEdits
            from app.storage.captions import ProjectCaptionTracks
            from app.storage.publishing_package import ProjectPublishing
            from app.storage.branding import ProjectBranding
            from app.storage.delivery import ProjectDelivery
            publishing.renders = copy(publishing.renders)
            store._index.selected = publishing.renders.index.selected
            store._index.project_id = publishing.renders.index.project_id
            publishing.renders.index = store._index
            publishing.renders.store = store
            publishing.renders.media = ProjectTimelineMedia(publishing.renders.index, store)
            publishing.plans = ProjectVideoPlans(repository, store)
            publishing.plan_scripts = ProjectPlanScripts(repository, store, publishing.plans)
            publishing.timelines = ProjectTimelineEdits(repository, store)
            publishing.captions = ProjectCaptionTracks(repository, store)
            publishing.artifacts = ProjectPublishing(store, self.session.project.id)
            if publishing.finishing:
                finishing = copy(publishing.finishing)
                finishing.session, finishing.renders = publishing.session, publishing.renders
                finishing.branding = ProjectBranding(store, self.session.project.id)
                finishing.video_plans, finishing.plan_scripts, finishing.timelines = publishing.plans, publishing.plan_scripts, publishing.timelines
                # ChannelLibrary opens/closes its own connection per operation;
                # do not pass a lazy owner-thread composition callback to workers.
                finishing._channel_library = publishing.finishing.channel_library
                finishing.channel_library_factory = None
                publishing.finishing = finishing
            if publishing.delivery:
                delivery = copy(publishing.delivery)
                delivery.session, delivery.renders = publishing.session, publishing.renders
                delivery.finishing = publishing.finishing
                delivery.artifacts = ProjectDelivery(store, self.session.project.id)
                publishing.delivery = delivery
        # Receipts live only within this detached operation. Every later action
        # captures and verifies fresh bytes; no stat-only persistent hash cache.
        def memoize(method):
            cache = {}
            def memo(*args):
                if args not in cache: cache[args] = method(*args)
                return cache[args]
            return memo
        if hasattr(publishing, 'original'):
            publishing.original = memoize(publishing.original)
        publishing.source = memoize(publishing.source)
        publishing.timing = memoize(publishing.timing)
        reader.source = memoize(reader.source)
        if reader.renderer:
            reader.renderer.identity = memoize(reader.renderer.identity)
        return reader, verify_current

    async def read_async(self, operation, *args, canceled=lambda: False, **kwargs):
        reader, verify_current = self.media_reader()
        def read(stop):
            reader.store.canceled = stop
            result = (getattr(reader, operation)(*args, **kwargs) if isinstance(operation, str)
                      else operation(reader))
            reader.store.validate_reads()
            return result
        result = await media_worker(read, canceled=canceled)
        verify_current()
        reader.store.validate_reads()
        reader.store.publish(self.store)
        self._boundaries.update(reader._boundaries)
        return result

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

    def analysis_reader(self, kind):
        """Capture on the SQLite owner; only detached media reads enter workers."""
        if not hasattr(self.publishing.renders, "frozen"):
            # Provider-neutral, index-free compositions (including offline fixtures).
            if self.store._index is not None:
                raise ValueError("Analysis requires a detached media reader for this project.")
            return self, lambda: None
        from app.storage.video_plans import ProjectVideoPlans
        from app.storage.plan_script import ProjectPlanScripts
        from app.storage.timeline_edits import ProjectTimelineEdits

        renders = self.publishing.renders.frozen()
        repository, store = renders.index.repository, renders.store
        publishing = copy(self.publishing)
        publishing.renders, publishing.store = renders, store
        publishing.session = SimpleNamespace(repository=repository, project=self.session.project,
                                              active_script=self.session.active_script)
        publishing.plans = ProjectVideoPlans(repository, store)
        publishing.plan_scripts = ProjectPlanScripts(repository, store, publishing.plans)
        publishing.timelines = ProjectTimelineEdits(repository, store)
        publishing._original_verification = None
        if kind != "original":
            # Derived-source composition can own other databases (channel library).
            # Resolve that lineage on its owner; discovery still uses detached media.
            source, timing = self.source(kind), self.publishing.timing(kind)
            publishing.source = lambda _: source
            publishing.timing = lambda _: timing
        reader = ShortsService(publishing, None)

        def verify_current():
            live = self.publishing.renders.index
            # Appending independent selection evidence does not change source inputs.
            if (self.session.active_script != publishing.session.active_script
                    or live.repository.active_script() != repository.active_script()
                    or live.selected() != renders.index.selected()
                    or tuple(m for m in live.manifests() if m.artifact_type != 'shorts_analysis_response')
                    != tuple(m for m in renders.index.manifests() if m.artifact_type != 'shorts_analysis_response')):
                raise ValueError("Project changed during analysis; refresh and analyze the current source.")
            if kind == "original":
                # Same owning inputs: reuse the verification performed off-thread
                # when the UI restores candidates. MP4 bytes remain rechecked.
                self.publishing._original_verification = publishing._original_verification
            elif self.source(kind) != source or self.publishing.timing(kind) != timing:
                raise ValueError("Source changed during analysis; refresh the verified source.")
        return reader, verify_current

    async def analyze(self, kind="original", count=3, target=45, *, canceled=lambda: False, progress=lambda _: None):
        if type(count) is not int or not 1 <= count <= 10: raise ValueError("Choose 1-10 candidates.")
        if type(target) is not int or not 30 <= target <= 60: raise ValueError("Target must be 30-60 seconds.")
        def check_cancel():
            if canceled(): raise RenderCanceled("Analysis canceled; retained candidates unchanged.")
        check_cancel()
        progress("Checking source")
        await asyncio.sleep(0)
        reader, verify_current = self.analysis_reader(kind)
        await asyncio.to_thread(reader.source, kind)
        check_cancel()
        progress("Finding excerpts")
        pool = await asyncio.to_thread(reader.pool, kind, target)
        check_cancel()
        verify_current()
        self._boundaries.update(reader._boundaries)
        if not pool:
            raise ShortAnalysisError('insufficient_candidates', "No valid 30-60 second chapter excerpts at verified boundaries. "
                             "Review chapter coverage and measured scene/sentence boundaries in Timeline; "
                             "refresh the verified source after correcting them. No candidates saved.")
        llm = self.publishing.llm
        provider = (llm.generation_identity() if llm and hasattr(llm, "generation_identity")
                    else type(llm).__qualname__ if llm else None)
        analysis_key = content_fingerprint([ALGORITHM, kind, count, target, provider, pool])
        for manifest in reversed(self.artifacts.history("analysis")):
            retained = self.artifacts.read(manifest)
            if retained.get("analysis_key") == analysis_key and len(retained['candidates']) == count:
                return manifest  # Refresh/repeated clicks must not repeat a paid request.
        available = excerpt_capacity(pool)
        if available < count:
            raise ShortAnalysisError('insufficient_candidates',
                f"Only {available} non-overlapping verified excerpts are available. "
                "Lower Candidates or review chapter coverage in Timeline; no AI request sent.")
        response_manifest = None
        if llm:
            progress("AI analysis")
            fields = {k: {"type": "string", "minLength": 1, "maxLength": 500} for k in ("interval_id", "title", "hook_assessment", "reason", "headline")}
            fields["interval_id"] = {"type": "string", "enum": [p["id"] for p in pool]}
            schema = {"type": "object", "additionalProperties": False, "required": ["candidates"], "properties": {
                "candidates": {"type": "array", "minItems": count, "maxItems": count, "items": {"type": "object",
                    "additionalProperties": False, "required": list(fields), "properties": fields}}}}
            prompt = ("Select distinct self-contained spoken excerpts from supplied verified intervals only. No invented timestamps or narration. "
                "Prefer complete explanations with understandable openings and payoffs; reject dangling pronouns and repetitive content. "
                "Assess existing facts only; headline must be supported by supplied spoken coverage. Editorial review required. Return interval IDs "
                "and assessments in editorial preference order, highest first; timing/references are resolved by application. "
                "Selected intervals must not overlap. "
                "Use the supplied start/end coverage to check overlap. Intervals: " + canonical_json([
                    {k:p[k] for k in ("id", "title", "group_ids", "section_ids", "scene_ids", "start", "end", "text")} for p in pool]))
            response_manifest, value = self.artifacts.analysis_response(analysis_key)
            if response_manifest is None:
                value = await asyncio.to_thread(llm.generate_structured, prompt, deepcopy(schema))
            if type(value) is not dict or set(value) != {"candidates"} or type(value["candidates"]) is not list or len(value["candidates"]) != count:
                raise ShortAnalysisError('invalid_response', "Invalid structured Short analysis.")
            ranked = []
            for v in value["candidates"]:
                if type(v) is not dict or set(v) != set(fields) or any(type(x) is not str or not x.strip() or len(x)>500 for x in v.values()):
                    raise ShortAnalysisError('invalid_response', "Invalid Short assessment fields.")
                item = next((p for p in pool if p["id"] == v["interval_id"]), None)
                if item is None:
                    raise ShortAnalysisError('unknown_candidate_id', "LLM referenced an unknown interval/section/scene.")
                ranked.append({**item, **{k:v[k] for k in fields if k != "interval_id"}})
            if response_manifest is None:
                # Retain only bounded known selection fields, never transport bodies,
                # headers, credentials, exceptions or a prompt. This is not an analysis
                # or an approval; replay always repeats validation against the pool.
                progress('Retaining AI selection evidence')
                response_manifest = self.artifacts.save('analysis_response', {
                    'version': 1, 'analysis_key': analysis_key,
                    'response': {'candidates': [{k:v[k] for k in fields} for v in value['candidates']]},
                    'status': 'UNREVIEWED_SELECTION_EVIDENCE'}, analysis_key=analysis_key)
            selected, discarded = select_excerpts(pool, ranked, count)
            ai_count = sum(p['selection_provenance']['method'] == 'llm' for p in selected)
            origin = (f"{ai_count} AI-selected, {count-ai_count} verified fallback selections. "
                      "Editorial review required.")
        else:
            progress("AI analysis (heuristic; no LLM configured)")
            selected, discarded = select_excerpts(pool, [], count, fallback_method='heuristic')
            origin = "Heuristic section/sentence discovery; no retention or virality prediction"
        if len(selected) != count:
            raise ShortAnalysisError('insufficient_candidates',
                "Not enough distinct valid excerpts found. Lower Candidates or review chapter coverage and verified boundaries in Timeline.")
        if len({p["id"] for p in selected}) != len(selected) or any(overlap(a,b) for i,a in enumerate(selected) for b in selected[i+1:]):
            raise ValueError("Suggestions repeat or overlap; review distinct intervals.")
        check_cancel()
        progress("Checking source before saving")
        source, _, timeline = await asyncio.to_thread(reader.source, kind)
        verify_current()
        if any(p["source_checksum"] != source.checksum or p["source_id"] != source.artifact_id or p["timeline_id"] != timeline.id for p in selected):
            raise ValueError("Source changed during analysis.")
        check_cancel()
        progress("Saving candidates")
        await asyncio.sleep(0)
        check_cancel()
        verify_current()
        return self.artifacts.save("analysis", {"version": 1, "origin": origin, "analysis_key": analysis_key, "requested_count": count,
            "target_seconds": target, "candidates": selected, "discarded_preferences": discarded,
            "response_artifact_id": response_manifest.artifact_id if response_manifest else None, "status": "REVIEW"})

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

    def stage(self, source, *, canceled=lambda: False):
        root = contained_path(self.session.repository.workspace, "work/shorts/" + uuid4().hex); root.mkdir(parents=True)
        cache = contained_path(self.session.repository.workspace, "work/shorts-sources/" + source.checksum + "/source.mp4")
        cache.parent.mkdir(parents=True, exist_ok=True)
        if cache.exists():
            # A staging cache is not evidence that the selected artifact still
            # exists with its declared bytes. Verify both before source reuse.
            self.verify_file(self.store._artifact_path(source.storage_key), source.checksum, source.size_bytes, canceled=canceled)
            self.verify_file(cache, source.checksum, source.size_bytes, canceled=canceled)
            check_canceled(canceled)
            try:
                os.link(cache, root/'source.mp4')
                return root
            except OSError:
                pass  # Filesystems without hard links keep the verified-copy path.
        digest, size = sha256(), 0
        with self.store.open_artifact_id(source.artifact_id) as stream, (root/"source.mp4").open("xb") as target:
            while block := stream.read(1024*1024):
                check_canceled(canceled)
                if target.write(block) != len(block): raise OSError("Incomplete source copy; check free disk space.")
                digest.update(block); size += len(block)
        if (digest.hexdigest(), size) != (source.checksum, source.size_bytes):
            raise ValueError("Source changed while staging; refresh the verified source.")
        check_canceled(canceled)
        try: os.link(root/'source.mp4', cache)
        except OSError: pass  # A concurrent verified preparation may have won.
        return root

    @staticmethod
    def verify_file(path, checksum, size, *, canceled=lambda: False):
        digest, measured = sha256(), 0
        with path.open('rb') as stream:
            while block := stream.read(1024*1024):
                check_canceled(canceled)
                digest.update(block); measured += len(block)
        if (digest.hexdigest(), measured) != (checksum, size):
            raise ValueError("Media checksum/size changed; restore the source or prepare it again.")

    async def stage_async(self, source, *, canceled=lambda: False):
        reader, verify_current = self.media_reader()
        def stage(stop):
            reader.store.canceled=stop
            return reader.stage(source,canceled=stop)
        root = await media_worker(stage, canceled=canceled)
        verify_current()
        return root

    async def preview(self, identity, edit, time=None, *, canceled=lambda: False, progress=lambda *_: None):
        if self.renderer is None: raise ValueError("Configure trusted local FFmpeg/FFprobe for Shorts previews.")
        progress('Checking source')
        source,e,t,a,b,shots,style,segments,_ = await self.read_async(
            lambda r:r.prepare(r.candidate(identity), edit), canceled=canceled)
        relative = Fraction(time) if time is not None else Fraction(0)
        if not 0 <= relative < b-a: raise ValueError("Preview time outside excerpt.")
        progress('Preparing excerpt')
        root = await self.stage_async(source, canceled=canceled)
        progress('Generating vertical preview')
        raw = await self.renderer.preview(root, e, a+relative, shots,
            style, segments, canceled=canceled, relative=relative)
        progress('Verifying')
        current = await self.read_async(lambda r:r.prepare(r.candidate(identity),edit), canceled=canceled)
        if current[:8] != (source,e,t,a,b,shots,style,segments): raise ValueError("Source changed during preview.")
        await media_worker(lambda stop:self.verify_file(root/'source.mp4',source.checksum,source.size_bytes,canceled=stop), canceled=canceled)
        progress('Ready')
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
        if self._rendering: raise ValueError('A Short render is already running; wait or cancel it first.')
        self._rendering.add(identity)
        try:
            return await self._render(identity, canceled=canceled, progress=progress)
        finally:
            self._rendering.discard(identity)

    async def _render(self, identity, *, canceled, progress):
        progress('Checking source')
        def inputs(reader):
            p = reader.candidate(identity)
            if not reader.accepted(identity): raise ValueError("Accept the excerpt after editorial/audio review before rendering.")
            revision = reader.artifacts.latest('edit',identity)
            if revision is None: raise ValueError('Save a Short edit first.')
            values, output_id, fingerprint = reader.identity(p,revision)
            return p,revision,values,output_id,fingerprint,reader.artifacts.cached(fingerprint),reader.publishing.plans.selected().working_title
        p,revision,values,output_id,fingerprint,cached,parent_title = await self.read_async(inputs,canceled=canceled)
        if cached: return cached
        def final_gate(reader):
            latest=reader.artifacts.latest('edit',identity)
            if latest is None or latest.artifact_id!=revision.artifact_id or not reader.accepted(identity) or reader.identity(reader.candidate(identity),revision)[1]!=output_id:
                raise ValueError('Short edit/source/decision changed while rendering; previous selection retained.')
        source,e,t,a,b,shots,style,segments,caption = values
        progress('Preparing excerpt')
        root = await self.stage_async(source,canceled=canceled)
        edit = output_id['edit']
        timing = await self.read_async(lambda r:r.publishing.timing(p['source_kind']),canceled=canceled)
        audio_bounds = speech_interval(Fraction(*edit["original_start"]), Fraction(*edit["original_end"]), timing)
        progress('Encoding')
        report = await self.renderer.render(root,e,a,b,shots,style,segments,canceled=canceled,progress=progress,audio_bounds=audio_bounds)
        if canceled(): raise RenderCanceled("Short canceled before publication.")
        progress('Verifying')
        await media_worker(lambda stop:self.verify_file(root/'source.mp4',source.checksum,source.size_bytes,canceled=stop),canceled=canceled)
        await self.read_async(final_gate,canceled=canceled)
        if (not report.get("fully_decoded") or report.get("status") != "READY" or report.get("source_start") != ratio(a)
                or report.get("source_end") != ratio(b) or (report.get("width"),report.get("height")) != (1080,1920)
                or report.get("video_codec") != "h264" or report.get("audio_codec") != "aac"
                or report.get("sample_rate") != 48000 or Fraction(str(report["fps"])) != Fraction(str(e["fps"]))
                or abs(Fraction(*report["video_duration"])-(b-a)) > Fraction(2,1000000) or report.get("frame_count") != (b-a)*Fraction(str(e["fps"]))
                or abs(Fraction(*report["audio_duration"])-(b-a)) > Fraction(1024,48000)):
            raise ValueError("Incomplete or invalid Short evidence; previous valid selection retained.")
        progress('Saving')
        video = await self.store.import_file_async('short.mp4',root/'short.mp4',
            {"artifact_type":"shorts_video", "module_name":"desktop_shorts", "project_id":self.session.project.id,
             "candidate_id":identity, "fingerprint":fingerprint, "identity":output_id, "shorts":report},
            checksum=report['checksum'],size_bytes=report['size_bytes'],canceled=canceled)
        metadata = {"parent_project_id":self.session.project.id, "source_artifact_id":source.artifact_id, "source_checksum":source.checksum,
            "timeline_id":t.id, "start":ratio(a), "end":ratio(b), "original_start":edit["original_start"], "original_end":edit["original_end"],
            "duration":report["video_duration"], "title":edit["title"], "section_ids":list(dict.fromkeys(s["section_id"] for s in shots)),
            "framing":style.to_payload(), "caption_identity":caption, "renderer":output_id['renderer'], "algorithm":ALGORITHM,
            "output_checksum":video.checksum, "video_artifact_id":video.artifact_id, "edit_revision_id":revision.artifact_id,
            "video_codec":"h264", "audio_codec":"aac", "resolution":[1080,1920], "fps":report["fps"]}
        meta = self.artifacts.save("metadata",metadata,candidate_id=identity,video_id=video.artifact_id)
        srt = self.store.save_artifact("captions.srt", serialize_srt(segments), {"artifact_type":"shorts_srt", "module_name":"desktop_shorts",
            "project_id":self.session.project.id,"video_id":video.artifact_id}) if segments else None
        # A small per-Short archive, not a second publishing package engine.
        archive = root/'short-package.zip'
        video_path = self.store._artifact_path(video.storage_key)
        def package_bytes(stop):
            with zipfile.ZipFile(archive,'x',zipfile.ZIP_STORED) as z:
                with z.open('short-01/short.mp4','w') as target, video_path.open('rb') as stream:
                    digest,size=sha256(),0
                    while block:=stream.read(1024*1024):
                        check_canceled(stop);target.write(block);digest.update(block);size+=len(block)
                    if (digest.hexdigest(),size)!=(video.checksum,video.size_bytes):raise ValueError('Short changed during packaging.')
                z.writestr('short-01/metadata.json',canonical_json(metadata))
                z.writestr('short-01/description.txt',edit['title']+'\n\nExcerpt from: '+parent_title)
                if srt:z.writestr('short-01/captions.srt',serialize_srt(segments))
            digest,size=sha256(),0
            with archive.open('rb') as stream:
                while block:=stream.read(1024*1024):
                    check_canceled(stop);digest.update(block);size+=len(block)
            return digest.hexdigest(),size
        checksum,size = await media_worker(package_bytes,canceled=canceled)
        package = await self.store.import_file_async('short-package.zip',archive,
            {"artifact_type":"shorts_package","module_name":"desktop_shorts","project_id":self.session.project.id,"video_id":video.artifact_id},
            checksum=checksum,size_bytes=size,canceled=canceled)
        # Re-read fresh source bytes and the revision/approval after every await.
        progress('Verifying')
        await self.read_async(final_gate,canceled=canceled)
        self.artifacts.save("selection",{"candidate_id":identity,"video_id":video.artifact_id,"metadata_id":meta.artifact_id,
            "srt_id":srt.artifact_id if srt else None,"package_id":package.artifact_id},candidate_id=identity)
        progress('Ready')
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

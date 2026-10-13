"""Post-render finishing; no script, narration, image or timeline mutations."""

from fractions import Fraction
from hashlib import file_digest
import shutil
from uuid import uuid4

from app.domain.dependencies import content_fingerprint
from app.domain.plan_script import planned_section_identity
from app.domain.video_plan import VIDEO_FORMATS, VideoFormat
from app.runtime.media_process import RenderCanceled
from app.storage.paths import contained_path


def cold_open_boundary(plan, binding, sections, timeline, render_request, sample_rate):
    """Confirm complete ordered group mapping to the original rendered snapshot."""
    if (plan is None or plan.format != VideoFormat.STANDARD or binding is None
            or binding.video_plan_revision_id != plan.id or not plan.groups
            or plan.groups[0].kind not in {"cold_open", "opening"}):
        raise ValueError("After cold open requires a current bound Standard plan.")
    expected = [planned_section_identity(plan.project_id, s.id) for g in plan.groups for s in g.sections]
    if [s.section_id for s in sections] != expected:
        raise ValueError("Cold-open mapping is missing or the script is incomplete/reordered.")
    if render_request.get("timeline") != timeline.to_payload():
        raise ValueError("The source render uses another or stale timeline.")
    revisions = {s.section_id: s.id for s in sections}
    mapped = []
    for clip in timeline.clips:
        if revisions.get(clip.media.section_id) != clip.media.section_revision_id:
            raise ValueError("Cold-open timeline mapping references stale section revisions.")
        if not mapped or mapped[-1] != clip.media.section_id:
            mapped.append(clip.media.section_id)
    if mapped != expected:
        raise ValueError("Cold-open timeline mapping is missing or ambiguous.")
    cold_ids = expected[:len(plan.groups[0].sections)]
    cold = [clip for clip in timeline.clips if clip.media.section_id in cold_ids]
    last = cold[-1]
    samples = (last.audio_offset + last.duration) * sample_rate
    if samples.denominator != 1 or not 0 < last.end_frame < timeline.total_frames:
        raise ValueError("Cold-open boundary is not a confirmed audio/video section boundary.")
    return {"video_frame": last.end_frame, "audio_sample": int(samples), "plan_id": plan.id,
            "binding_id": binding.id, "timeline_id": timeline.id}


class VideoFinishingService:
    def __init__(self, session, branding, renders, renderer, *, video_plans=None, plan_scripts=None, timelines=None):
        self.session, self.branding, self.renders, self.renderer = session, branding, renders, renderer
        self.video_plans, self.plan_scripts, self.timelines = video_plans, plan_scripts, timelines

    def boundary(self, source):
        plan = self.video_plans.selected() if self.video_plans is not None else None
        if self.timelines is None or self.plan_scripts is None:
            raise ValueError("After cold open is unavailable: verified timeline/plan mapping is missing.")
        current = self.timelines.current()
        binding = self.plan_scripts.active_binding()
        if current is None or binding is None or binding.script_id != self.session.active_script.script_id:
            raise ValueError("After cold open is unavailable: current script/timeline binding is missing.")
        self.renders.current(current.timeline)
        request = source.metadata.get("desktop_dependencies", {}).get("request", {}).get("settings", {})
        return cold_open_boundary(plan, binding, self.session.active_script.sections, current.timeline,
                                  request, source.metadata["render"]["sample_rate"])

    def prepare(self, config):
        source = self.renders.selected(verify_bytes=True)
        if source is None:
            raise ValueError("Build and select an original final MP4 first.")
        if not config.enabled:
            return source, None, None
        if not config.channel_name.strip() or not config.logo_id:
            raise ValueError("Enter a channel name and import a PNG logo before finishing.")
        logo, _ = self.branding.logo(config.logo_id)
        evidence = source.metadata["render"]
        plan = self.video_plans.selected() if self.video_plans is not None else None
        if plan is not None and plan.format == VideoFormat.STANDARD:
            duration = Fraction(*evidence["audio_duration"])
            profile = VIDEO_FORMATS[VideoFormat.STANDARD]
            if not profile.min_duration_seconds <= duration <= profile.max_duration_seconds:
                raise ValueError(f"PLAN_DURATION BLOCKED: original narrative is {float(duration):.2f}s; "
                                 "allowed 540–660s. Branding cannot satisfy narration acceptance. Rebuild the original after correcting narration.")
        boundary = None
        if config.intro and config.placement == "after_cold_open":
            boundary = self.boundary(source)
        identity = {"source_id": source.artifact_id, "source_checksum": source.checksum,
                    "settings": config.to_payload(), "logo_checksum": logo.checksum, "boundary": boundary,
                    "renderer": self.renderer.identity()}
        return source, identity, content_fingerprint(identity)

    async def build(self, config, *, canceled=lambda: False, progress=lambda *_: None):
        self.branding.save(config)
        source, identity, fingerprint = self.prepare(config)
        if not config.enabled:
            return source
        cached = self.branding.cached(fingerprint)
        if cached is not None:
            if canceled():
                raise RenderCanceled("Finishing canceled before cache reuse.")
            return cached
        root = contained_path(self.session.repository.workspace, "work/finishing/" + uuid4().hex)
        root.mkdir(parents=True)
        with self.branding.store.open_artifact_id(source.artifact_id) as stream, (root / "source.mp4").open("xb") as target:
            shutil.copyfileobj(stream, target, 1024 * 1024)
        with (root / "source.mp4").open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != source.checksum:
                raise ValueError("Original MP4 changed while staging.")
        _, logo = self.branding.logo(config.logo_id)
        (root / "logo.png").write_bytes(logo)
        evidence = await self.renderer.finish(root, source.metadata["render"], config, identity["boundary"],
                                              canceled=canceled, progress=progress)
        if canceled():
            raise RenderCanceled("Finishing canceled before publication.")
        if self.branding.current() != config or self.prepare(config)[1] != identity:
            raise ValueError("Finishing inputs changed; retry with current settings and source.")
        with (root / "branded.mp4").open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != evidence["checksum"]:
                raise ValueError("Branded output changed after validation.")
            stream.seek(0)
            return self.branding.store.import_stream("branded.mp4", stream,
                {"artifact_type": "branded_video", "module_name": "desktop_branding", "project_id": self.session.project.id,
                 "fingerprint": fingerprint, "identity": identity, "finishing": evidence})

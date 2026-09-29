"""Read-only, provider-free readiness checks for the retained project pipeline."""

from dataclasses import dataclass
import json
import logging

from app.domain.section_audio import SectionAudio
from app.domain.video_plan import VIDEO_FORMATS


logger = logging.getLogger("aics.pipeline")


@dataclass(frozen=True, slots=True)
class StageDiagnostic:
    stage: str
    state: str
    message: str
    section_id: str | None = None
    section_title: str | None = None
    scene_id: str | None = None
    artifact_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PipelineReport:
    diagnostics: tuple[StageDiagnostic, ...]
    section_count: int
    voice_ready: int
    scenes_ready: int
    scene_count: int
    visuals_ready: int
    visual_count: int
    timeline_accepted: int
    timeline_rejected: int
    timeline_expected: int
    export_ready: bool
    ready_for_timeline: bool
    plan_summary: str = "LEGACY / NOT USED"
    script_group_progress: str = "NOT USED"
    narration_duration_review: str | None = None

    @property
    def summary(self):
        return (f"Plan: {self.plan_summary}\nScript groups: {self.script_group_progress}"
                + (f"\nNarration duration: {self.narration_duration_review}" if self.narration_duration_review else "")
                + f"\nScript: {'OK' if self.section_count else 'BLOCKED'}\n"
                f"Voice: {self.voice_ready}/{self.section_count}\n"
                f"Scenes/timing: {self.scenes_ready}/{self.section_count}\n"
                f"Visuals: {self.visuals_ready}/{self.visual_count}\n"
                f"Timeline candidates: {self.timeline_accepted}/{self.timeline_expected}\n"
                f"Export: {'READY' if self.export_ready else 'BLOCKED'}")


def _emit(stage, state, message, *, level=logging.INFO):
    logger.log(level, "[AICS][PIPELINE][%s][%s] %s", stage, state, message)


def configure_pipeline_logging(level="INFO"):
    """Install one console handler for the dedicated pipeline logger."""
    normalized = str(level).upper()
    value = getattr(logging, normalized, None)
    if not isinstance(value, int) or not normalized in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        normalized, value = "INFO", logging.INFO
    if not any(getattr(handler, "_aics_pipeline_handler", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler._aics_pipeline_handler = True
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(value)
    return normalized


class PipelineDiagnostics:
    """Inspect retained state only; never invokes generation, mutation or rendering."""

    def inspect_project(self, session, *, audio=None, scenes=None, timeline=None,
                        unsaved_draft=False, audio_choice=None, audio_variant="original",
                        video_plans=None, plan_script=None):
        if audio_variant not in ("original", "processed"):
            raise ValueError("Choose original or processed audio explicitly.")
        sections = tuple(session.active_script.sections)
        diagnostics = []
        voice_ready = scenes_ready = visuals_ready = visual_count = 0
        scene_count = 0
        snapshot_id = session.active_script.id if sections else None
        if sections:
            message = (f"revision={snapshot_id} sections={len(sections)} unsaved_draft={str(bool(unsaved_draft)).lower()} "
                       f"sections_detail={json.dumps([{'id': s.section_id, 'title': s.title} for s in sections], ensure_ascii=False)}")
            diagnostics.append(StageDiagnostic("SCRIPT", "OK", message))
            _emit("SCRIPT", "OK", message)
        else:
            message = 'reason="No saved script sections."'
            diagnostics.append(StageDiagnostic("SCRIPT", "BLOCKED", message, reason="No saved script sections."))
            _emit("SCRIPT", "BLOCKED", message)

        measured_total_duration, measured_sections = 0.0, 0
        for section in sections:
            audio_value = None
            audio_reason = None
            if audio is None or audio.services is None:
                audio_reason = "Audio services are not configured."
            else:
                try:
                    playback = audio.services.playback(section, audio_variant, audio_choice)
                    if playback.stale:
                        audio_reason = f"Selected {audio_variant} narration is stale for the current section revision or voice."
                    heads = audio.services.index.selected()
                    key = "raw" if audio_variant == "original" else "processed"
                    artifact_id = heads.get(f"section:{section.section_id}:audio:{key}")
                    if artifact_id:
                        manifest = next(item for item in audio.services.index.manifests()
                                        if item.artifact_id == artifact_id)
                        audio_value = SectionAudio.from_manifest(manifest)
                        if audio_value.revision_id != section.id:
                            audio_reason = "Selected narration belongs to an older section revision."
                except (ValueError, OSError, KeyError, StopIteration) as exc:
                    audio_reason = str(exc)
            if audio_value is None and audio_reason is None:
                audio_reason = f"No selected {audio_variant} narration for the current section revision."
            if audio_reason:
                message = f"section={json.dumps(section.title, ensure_ascii=False)} reason={json.dumps(audio_reason, ensure_ascii=False)}"
                diagnostics.append(StageDiagnostic("VOICE", "BLOCKED", message, section.section_id,
                                                   section.title, reason=audio_reason))
                _emit("VOICE", "BLOCKED", message)
            else:
                voice_ready += 1
                measured_total_duration += audio_value.duration_seconds
                measured_sections += 1
                boundary = audio_value.speech_boundary_map
                quality = boundary.quality if boundary else "unavailable"
                message = (f"section={json.dumps(section.title, ensure_ascii=False)} variant={audio_variant} "
                           f"artifact={audio_value.artifact_id} sample_rate={audio_value.sample_rate} "
                           f"frames={audio_value.frame_count} boundary_map={quality}")
                diagnostics.append(StageDiagnostic("VOICE", "OK", message, section.section_id,
                                                   section.title, artifact_id=audio_value.artifact_id))
                _emit("VOICE", "OK", message)

            plan_state = None
            timing = None
            scene_views = ()
            scene_reason = None
            if audio_reason:
                scene_reason = f"Scene timing cannot be validated because narration is not ready: {audio_reason}"
            elif scenes is None:
                scene_reason = "Scene services are not configured."
            else:
                try:
                    plan_state = scenes.plan_state(section)
                    if plan_state.state != "accepted":
                        scene_reason = ("No accepted scene plan for the current section revision."
                                        if plan_state.state == "none" else
                                        "Current scene proposal has not been accepted.")
                    else:
                        accepted = scenes.plans.acceptance(plan_state.acceptance_id)
                        timing = scenes._timing(accepted)
                        if timing is None:
                            scene_reason = "No scene timing set for the current accepted plan."
                        elif audio_value is not None and (
                                timing.audio_artifact_id, timing.audio_checksum, timing.sample_rate, timing.frame_count
                        ) != (audio_value.artifact_id, audio_value.checksum,
                              audio_value.sample_rate, audio_value.frame_count):
                            scene_reason = "Timing references an older narration artifact."
                        scene_views = scenes.scenes(section)
                except (ValueError, OSError, KeyError, StopIteration) as exc:
                    scene_reason = str(exc)
            if scene_reason:
                message = f"section={json.dumps(section.title, ensure_ascii=False)} reason={json.dumps(scene_reason, ensure_ascii=False)}"
                diagnostics.append(StageDiagnostic("SCENES", "BLOCKED", message, section.section_id,
                                                   section.title, reason=scene_reason))
                _emit("SCENES", "BLOCKED", message)
            else:
                scenes_ready += 1
                message = (f"section={json.dumps(section.title, ensure_ascii=False)} accepted={plan_state.acceptance_id} "
                           f"scenes={len(scene_views)} timing={timing.id} quality={timing.quality}")
                diagnostics.append(StageDiagnostic("SCENES", "OK", message, section.section_id, section.title))
                _emit("SCENES", "OK", message)

            if scene_reason or not scene_views:
                continue
            for scene in scene_views:
                scene_count += 1
                visual_count += 1
                prompt_ok, image_value, image_reason = False, None, None
                try:
                    selected_prompt = scenes.prompts.prompts.selected(scene.id)
                    if selected_prompt is not None:
                        revision = scenes.prompts.prompts.revision(selected_prompt.revision_id)
                        expected = scenes.prompts.prompts.snapshot(
                            revision.inputs.acceptance_id, scene.id,
                            revision.inputs.brief_revision_id, revision.inputs.style_revision_id)
                        prompt_ok = expected == revision.inputs
                    choice = scenes.images.selected(scene.id)
                    if choice is not None:
                        image_value = scenes.images.image(choice.artifact_id)
                        if (image_value.acceptance_id, image_value.section_revision_id) != (scene.acceptance_id, section.id):
                            image_reason = "Selected image belongs to a historical accepted scene revision."
                        else:
                            image_reason = None
                    else:
                        image_reason = "No selected image."
                except (ValueError, OSError, KeyError, StopIteration) as exc:
                    image_reason = str(exc)
                reasons = []
                if not prompt_ok:
                    reasons.append("No valid selected visual prompt.")
                if not image_value:
                    reasons.append(image_reason or "No selected image.")
                if not reasons:
                    visuals_ready += 1
                    provider = scenes._image_generation_identity(image_value.artifact_id) or {}
                    if not provider and image_value.source_artifact_id:
                        provider = scenes._image_generation_identity(image_value.source_artifact_id) or {}
                    orientation = "landscape" if image_value.width >= image_value.height else "portrait"
                    message = (f"section={json.dumps(section.title, ensure_ascii=False)} scene={scene.id} "
                               f"prompt={scene.prompt_id} image={image_value.artifact_id} "
                               f"provenance={image_value.provenance} provider={provider.get('provider', 'unknown')} "
                               f"model={provider.get('model', 'unknown')} size={image_value.width}x{image_value.height} "
                               f"orientation={orientation}")
                    diagnostics.append(StageDiagnostic("VISUALS", "OK", message, section.section_id,
                                                       section.title, scene.id, image_value.artifact_id))
                    _emit("VISUALS", "OK", message)
                else:
                    reason = "; ".join(reasons)
                    message = (f"section={json.dumps(section.title, ensure_ascii=False)} scene={scene.id} "
                               f"reason={json.dumps(reason, ensure_ascii=False)}")
                    diagnostics.append(StageDiagnostic("VISUALS", "BLOCKED", message, section.section_id,
                                                       section.title, scene.id, reason=reason))
                    _emit("VISUALS", "BLOCKED", message)

        candidate_diagnostics = ()
        if timeline is not None:
            candidate_diagnostics = timeline.current_candidate_diagnostics(audio_variant=audio_variant)
        timeline_expected = len(candidate_diagnostics)
        timeline_accepted = sum(item.accepted for item in candidate_diagnostics)
        timeline_rejected = timeline_expected - timeline_accepted
        for item in candidate_diagnostics:
            if item.accepted:
                _emit("TIMELINE", "OK", f"section={json.dumps(item.section_title, ensure_ascii=False)} "
                      f"scene={item.scene_id} variant={item.audio_variant} timing={item.timing_id}")
            else:
                message = (f"section={json.dumps(item.section_title, ensure_ascii=False)} scene={item.scene_id} "
                           f"variant={item.audio_variant} reason={json.dumps(item.reason, ensure_ascii=False)}")
                diagnostics.append(StageDiagnostic("TIMELINE", "REJECT", message, item.section_id,
                                                   item.section_title, item.scene_id, reason=item.reason))
                _emit("TIMELINE", "REJECT", message)
        if timeline_expected and not timeline_rejected:
            _emit("TIMELINE", "OK", f"candidates={timeline_accepted}")
        else:
            _emit("TIMELINE", "SUMMARY", f"accepted={timeline_accepted} rejected={timeline_rejected}")

        current = None
        timeline_read_error = None
        if timeline is not None:
            try:
                current = timeline.current()
            except (ValueError, OSError, KeyError) as exc:
                timeline_read_error = str(exc) or type(exc).__name__
        export_ready = bool(current and current.timeline.clips)
        if export_ready:
            _emit("EXPORT", "READY", f"timeline_clips={len(current.timeline.clips)}")
            diagnostics.append(StageDiagnostic("EXPORT", "READY",
                                               f"timeline_clips={len(current.timeline.clips)}"))
        elif timeline_read_error:
            message = f"reason={json.dumps(timeline_read_error, ensure_ascii=False)}"
            _emit("EXPORT", "BLOCKED", message)
            diagnostics.append(StageDiagnostic("EXPORT", "BLOCKED", message, reason=timeline_read_error))
        else:
            _emit("EXPORT", "BLOCKED", 'reason="No saved timeline."')
            diagnostics.append(StageDiagnostic("EXPORT", "BLOCKED", 'reason="No saved timeline."',
                                               reason="No saved timeline."))
        ready_for_timeline = bool(
            sections and voice_ready == len(sections) and scenes_ready == len(sections)
            and scene_count > 0 and visuals_ready == visual_count
            and timeline_expected == scene_count and timeline_rejected == 0
        )
        _emit("SUMMARY", "OK" if ready_for_timeline else "BLOCKED",
              f"script={'OK' if sections else 'BLOCKED'} voice={voice_ready}/{len(sections)} "
              f"timing={scenes_ready}/{len(sections)} visuals={visuals_ready}/{visual_count} "
              f"timeline_candidates={timeline_accepted}/{timeline_expected}")
        plan_summary, group_progress, duration_review = "LEGACY / NOT USED", "NOT USED", None
        selected_plan = None
        if video_plans is not None:
            try:
                selected_plan = video_plans.selected()
                if selected_plan is not None:
                    minutes, seconds = divmod(selected_plan.target_duration_seconds, 60)
                    plan_summary = f"{selected_plan.format.value.upper()} · {minutes}:{seconds:02d} · READY"
                    artifacts = getattr(plan_script, "artifacts", None)
                    binding = artifacts.active_binding() if artifacts is not None else None
                    if binding is not None and binding.video_plan_revision_id != selected_plan.id:
                        group_progress = "BLOCKED · script binding uses another plan"
                    elif binding is not None and binding.script_id != session.active_script.script_id:
                        group_progress = "BLOCKED · active script differs from its binding"
                    elif binding is None and sections:
                        group_progress = "BLOCKED · unbound script"
                    else:
                        states = plan_script.progress(selected_plan) if plan_script is not None else ("pending",) * len(selected_plan.groups)
                        group_progress = f"{sum(value == 'complete' for value in states)}/{len(states)}"
                    _emit("SCRIPT_GROUPS", "OK" if "BLOCKED" not in group_progress else "BLOCKED", group_progress)
                    _emit("PLAN", "OK", f"format={selected_plan.format.value} target={selected_plan.target_duration_seconds} groups={len(states)}")
                    if sections and measured_sections == len(sections):
                        if not VIDEO_FORMATS[selected_plan.format].min_duration_seconds <= measured_total_duration <= VIDEO_FORMATS[selected_plan.format].max_duration_seconds:
                            duration_review = f"REVIEW · measured {measured_total_duration:.1f}s outside {VIDEO_FORMATS[selected_plan.format].min_duration_seconds}–{VIDEO_FORMATS[selected_plan.format].max_duration_seconds}s"
                            _emit("PLAN_DURATION", "REVIEW", f"measured={measured_total_duration:.1f}s")
                        else:
                            duration_review = f"OK · measured {measured_total_duration:.1f}s"
            except (ValueError, OSError, KeyError) as exc:
                plan_summary = "UNAVAILABLE"
                diagnostics.append(StageDiagnostic("PLAN", "REVIEW", str(exc), reason=str(exc)))
        return PipelineReport(tuple(diagnostics), len(sections), voice_ready, scenes_ready,
                              scene_count, visuals_ready, visual_count, timeline_accepted,
                              timeline_rejected, timeline_expected, export_ready, ready_for_timeline,
                              plan_summary, group_progress, duration_review)

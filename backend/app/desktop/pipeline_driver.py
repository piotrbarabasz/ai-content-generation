"""Owner-thread adapter between automatic orchestration and project services."""

import asyncio
import logging

from app.application.automatic_workflow import AutomaticWorkflowCanceled
from app.application.image_presets import final_dimensions, generation_dimensions


logger = logging.getLogger("aics.pipeline")


class DesktopPipelineDriver:
    """Keep repositories on the GUI thread; offload only provider inference calls."""

    def __init__(self, editor, config):
        self.editor, self.config = editor, config
        self.cancel_requested = False
        self.active_image = None
        self.active_upscale = None
        self.active_prompt_provider = None

    def saved_sections(self):
        return tuple(self.editor.session.active_script.sections)

    def unsaved_script_draft(self):
        return self.editor.dirty or self.editor.worker is not None

    def voice_ready(self, section, choice, variant):
        if self.editor.audio.services is None:
            return False
        try:
            audio = self.editor.audio.services.playback(section, variant, choice)
            return not audio.stale
        except (ValueError, OSError):
            return False

    async def generate_voice(self, section, choice):
        if self.cancel_requested:
            raise AutomaticWorkflowCanceled("VOICE")
        if self.editor.audio.services is None:
            raise ValueError("Audio services are not configured.")
        self.editor.audio.select_section(section)
        result = await self.editor.audio.services.generate(section, choice)
        if self.cancel_requested:
            raise AutomaticWorkflowCanceled("VOICE")
        if not str(result).startswith("completed"):
            raise RuntimeError(f"Narration generation {result}.")

    def plan_state(self, section):
        if self.editor.visuals.services is None:
            raise ValueError("Scene services are not configured.")
        return self.editor.visuals.services.plan_state(section)

    def suggest_scene_plan(self, section):
        self.editor.visuals.services.scenes(section)
        return self.editor.visuals.services.suggest_scene_plan(section)

    def accept_scene_plan(self, section, plan_id):
        self.editor.visuals.services.scenes(section)
        return self.editor.visuals.services.accept_scene_plan(section, plan_id)

    def timing_ready(self, section, acceptance_id, variant):
        if variant != "original":
            return False
        services = self.editor.visuals.services
        try:
            timing = services._timing(services.plans.acceptance(acceptance_id))
            audio = services._selected_audio(section)
            return bool(timing and audio and
                        (timing.audio_artifact_id, timing.audio_checksum, timing.sample_rate, timing.frame_count)
                        == (audio.artifact_id, audio.checksum, audio.sample_rate, audio.frame_count))
        except (ValueError, OSError, KeyError):
            return False

    def rebuild_timing(self, section):
        return self.editor.visuals.services.rebuild_scene_timing(section)

    def scenes(self, section):
        return self.editor.visuals.services.scenes(section)

    def prompt_ready(self, section, scene):
        services = self.editor.visuals.services
        try:
            chosen = services.prompts.prompts.selected(scene.id)
            if chosen is None:
                return False
            revision = services.prompts.prompts.revision(chosen.revision_id)
            current = services.prompts.prompts.snapshot(
                revision.inputs.acceptance_id, scene.id,
                revision.inputs.brief_revision_id, revision.inputs.style_revision_id)
            return current == revision.inputs
        except (ValueError, OSError, KeyError):
            return False

    async def generate_prompt(self, section, scene):
        services = self.editor.visuals.services
        services.scenes(section)
        context_ids = services._context_ids(scene.id)
        prepared = services.prompts.prepare_generation(
            services.acceptance.id, scene.id, *context_ids)
        provider = services.prompts.provider
        if provider is None:
            raise ValueError("LLM provider is not configured.")
        self.active_prompt_provider = provider
        try:
            payload = await asyncio.to_thread(services.prompts.generate_payload, prepared)
            if self.cancel_requested:
                raise AutomaticWorkflowCanceled("VISUAL_PROMPT")
            revision = services.prompts.retain_generated(prepared, payload)
            services.prompts.validate_prepared_current(prepared)
            services.prompts.select(revision.id,
                expected_selection_id=prepared[2].id if prepared[2] else None)
        finally:
            self.active_prompt_provider = None

    def image_ready(self, section, scene):
        services = self.editor.visuals.services
        try:
            selected = services.images.selected(scene.id)
            if selected is None:
                return False
            image = services.images.image(selected.artifact_id)
            return (image.acceptance_id, image.section_revision_id) == (scene.acceptance_id, section.id)
        except (ValueError, OSError, KeyError):
            return False

    def image_provider_identity(self, scene):
        selected = self.editor.visuals.services.images.selected(scene.id)
        image = self.editor.visuals.services.images.image(selected.artifact_id)
        identity = self.editor.visuals.services._image_generation_identity(image.artifact_id) or {}
        return identity.get("provider", image.provenance)

    async def generate_image(self, section, scene, config):
        services = self.editor.visuals.services
        services.scenes(section)
        generator_id = config.image_generator_id
        dimensions = services.image_generation_dimensions(generator_id, config.orientation)
        width, height = dimensions or generation_dimensions(config.orientation)
        pending, cached = services.prepare_background_image(
            scene.id, width=width, height=height, seed=0, generator_id=generator_id)
        if cached is not None:
            return
        claim, scene_id, selected_generator, provider, request, selection_id = pending
        self.active_image = (claim, scene_id, selected_generator)
        result, error = None, None
        try:
            result = await asyncio.to_thread(provider.generate, request)
        except Exception as exc:
            error = exc
        if self.cancel_requested:
            try:
                services.finish_background_image(claim, scene_id, result=result, error=error,
                    generator_id=selected_generator, expected_selection_id=selection_id)
            except (RuntimeError, ValueError) as exc:
                logger.warning("[AICS][PIPELINE][AUTO][CANCEL] image_cleanup=%r", str(exc))
            finally:
                self.active_image = None
            raise AutomaticWorkflowCanceled("IMAGE")
        try:
            services.finish_background_image(claim, scene_id, result=result, error=error,
                generator_id=selected_generator, expected_selection_id=selection_id)
        finally:
            self.active_image = None

    def final_image_ready(self, section, scene, config):
        if config.final_resolution == "draft":
            return True
        services = self.editor.visuals.services
        try:
            selected = services.images.selected(scene.id)
            if selected is None:
                return False
            image = services.images.image(selected.artifact_id)
            dimensions = final_dimensions(config.orientation, config.final_resolution)
            return (image.provenance == "final" and image.section_revision_id == section.id
                    and image.target_profile == config.final_resolution
                    and (image.width, image.height) == dimensions)
        except (ValueError, OSError, KeyError):
            return False

    async def create_final_image(self, section, scene, config):
        services = self.editor.visuals.services
        selected = services.images.selected(scene.id)
        if selected is None:
            raise ValueError("No selected source image for final-resolution processing.")
        prepared = services.prepare_final_image(selected.artifact_id, config.orientation, config.final_resolution)
        cached = services.cached_final_image(prepared)
        if cached is not None:
            services.select_cached_final_image(prepared, cached)
            return
        provider = services.upscale.provider if services.upscale else None
        if provider is None:
            raise ValueError("Configure the optional local upscaler first.")
        reset = getattr(provider, "reset_cancel", None)
        if callable(reset):
            reset()
        self.active_upscale = provider
        try:
            result = await asyncio.to_thread(provider.upscale, prepared[2])
            if self.cancel_requested:
                raise AutomaticWorkflowCanceled("FINAL_IMAGE")
            services.finish_final_image(prepared, result)
        finally:
            self.active_upscale = None

    def timeline_candidates(self, sections, variant):
        return self.editor.timeline.services.current_candidate_diagnostics(audio_variant=variant)

    def timeline_matches(self, candidates):
        current = self.editor.timeline.services.current()
        if current is None or len(current.timeline.clips) != len(candidates):
            return False
        return all(clip.media == item.media
                   for clip, item in zip(current.timeline.clips, candidates))

    async def rebuild_timeline(self, sources):
        if self.cancel_requested:
            raise AutomaticWorkflowCanceled("TIMELINE")
        expected = self.editor.timeline.services.current()
        self.editor.timeline.services.rebuild_from_sources(
            sources, expected=expected.id if expected else None)
        self.editor.timeline.refresh()
        self.editor._bind_regeneration()

    def current_timeline(self):
        return self.editor.timeline.services.current()

    def cancel_current(self):
        self.cancel_requested = True
        if self.editor.audio.services and self.editor.audio.services.attempt is not None:
            self.editor.audio.services.cancel()
        if self.active_image:
            claim, _, generator_id = self.active_image
            self.editor.visuals.services.cancel_background_image(claim, generator_id=generator_id)
        if self.active_upscale:
            self.active_upscale.cancel()
        cancel_prompt = getattr(self.active_prompt_provider, "cancel", None)
        if callable(cancel_prompt):
            cancel_prompt()

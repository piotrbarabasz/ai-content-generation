"""D022 presentation adapter over the D013/D015/D016/D017 services."""

from dataclasses import dataclass
import json

from app.application.scene_planning import ScenePlanningService
from app.providers.image_generation import ImageGenerationRequest
from app.storage.section_tempo import SectionTempoArtifacts
from app.tts.scene_sources import sentence_sources


@dataclass(frozen=True)
class PromptVariant:
    id: str
    label: str


@dataclass(frozen=True)
class ImageVariant:
    id: str
    label: str


@dataclass(frozen=True)
class VisualContextView:
    brief: str
    brief_revision_id: str | None
    style: str
    style_revision_id: str | None


@dataclass(frozen=True)
class SceneView:
    id: str
    acceptance_id: str
    index: int
    text: str
    visual_description: str
    time_label: str
    prompt: str
    prompt_id: str | None
    prompt_selection_id: str | None
    prompts: tuple[PromptVariant, ...]
    image_id: str | None
    image_selection_id: str | None
    images: tuple[ImageVariant, ...]


@dataclass(frozen=True)
class PlanSceneView:
    index: int
    text: str
    visual_description: str
    time_label: str


@dataclass(frozen=True)
class ScenePlanView:
    state: str
    plan_id: str | None
    acceptance_id: str | None
    scenes: tuple[PlanSceneView, ...]


class SceneServices:
    """Synchronous coordinator-thread commands; providers remain injected."""

    def __init__(self, *, plans, prompts, intake, generation, images, store, coordinator,
                 context_resolver=None):
        self.plans, self.prompts = plans, prompts
        self.intake, self.generation, self.images = intake, generation, images
        self.store, self.coordinator = store, coordinator
        self.context_resolver = context_resolver
        self.section = self.acceptance = None

    def _current_acceptance(self, section):
        values = [value for value in self.plans.acceptances(section.section_id)
                  if value.plan.revision_id == section.id]
        return values[-1] if values else None

    def _timing(self, acceptance):
        values = []
        for manifest in sorted(self.store.list_artifacts(), key=lambda item: (item.created_at, item.artifact_id)):
            if manifest.artifact_type != "desktop_scene_timing":
                continue
            timing = self.plans.timing(manifest.metadata["value_id"])
            if timing.acceptance_id == acceptance.id:
                values.append(timing)
        return values[-1] if values else None

    def _selected_audio(self, section):
        return SectionTempoArtifacts(self.store._index, self.store).selected(section, "original")

    def plan_state(self, section):
        proposals = tuple(plan for plan in self.plans.proposals(section.section_id)
                          if plan.revision_id == section.id)
        if not proposals:
            return ScenePlanView("none", None, None, ())
        plan = proposals[-1]
        accepted = next((value for value in reversed(self.plans.acceptances(section.section_id))
                         if value.plan.id == plan.id), None)
        timing = self._timing(accepted) if accepted else None
        measured = ({value.scene_id: value for value in timing.scenes} if timing else {})
        scenes = []
        for index, scene in enumerate(plan.scenes, 1):
            value = measured.get(scene.id)
            label = (f"{value.start_frame / timing.sample_rate:.2f}–"
                     f"{value.end_frame / timing.sample_rate:.2f} s") if value else "Timing unavailable"
            scenes.append(PlanSceneView(
                index,
                section.text[scene.source_start:scene.source_end],
                scene.visual_description,
                label,
            ))
        return ScenePlanView("accepted" if accepted else "proposal", plan.id,
                             accepted.id if accepted else None, tuple(scenes))

    def suggest_scene_plan(self, section):
        audio = self._selected_audio(section)
        if audio is not None and audio.speech_boundary_map is None:
            audio = None
        ScenePlanningService(self.plans, sentence_sources).suggest(section, audio)
        return self.plan_state(section)

    def accept_scene_plan(self, section, plan_id):
        current = self.plan_state(section)
        if current.state != "proposal" or current.plan_id != plan_id:
            raise ValueError("Accept the current retained scene proposal.")
        ScenePlanningService(self.plans, sentence_sources).accept(plan_id, reviewer_id="desktop-editor")
        return self.plan_state(section)

    def rebuild_scene_timing(self, section):
        current = self.plan_state(section)
        if current.state != "accepted":
            raise ValueError("Accept the current scene proposal before rebuilding timing.")
        audio = self._selected_audio(section)
        if audio is None:
            raise ValueError("Generate and select narration for this saved section before rebuilding timing.")
        ScenePlanningService(self.plans, sentence_sources).retime(current.acceptance_id, section, audio)
        return self.plan_state(section)

    def _view(self, scene, index, timing):
        prompt_choice = self.prompts.prompts.selected(scene.id)
        selected_prompt = (self.prompts.prompts.revision(prompt_choice.revision_id)
                           if prompt_choice else None)
        prompt_history = self.prompts.prompts.history(scene.id)
        image_choice = self.images.selected(scene.id)
        image_history = self.images.history(scene.id)
        measured = next((value for value in timing.scenes if value.scene_id == scene.id), None) if timing else None
        time_label = "Timing unavailable"
        if measured is not None:
            time_label = f"{measured.start_frame / timing.sample_rate:.2f}–{measured.end_frame / timing.sample_rate:.2f} s"
        return SceneView(
            scene.id, self.acceptance.id, index,
            self.section.text[scene.source_start:scene.source_end], scene.visual_description,
            time_label, selected_prompt.prompt if selected_prompt else "",
            selected_prompt.id if selected_prompt else None, prompt_choice.id if prompt_choice else None,
            tuple(PromptVariant(value.id, f"{value.provenance}: {value.prompt[:60]}") for value in prompt_history),
            image_choice.artifact_id if image_choice else None, image_choice.id if image_choice else None,
            tuple(ImageVariant(value.artifact_id,
                               f"{value.provenance}: {value.source_name} ({value.width}×{value.height})")
                  for value in image_history),
        )

    def scenes(self, section):
        self.section = section
        self.acceptance = self._current_acceptance(section)
        if self.acceptance is None:
            return ()
        timing = self._timing(self.acceptance)
        return tuple(self._view(scene, index, timing)
                     for index, scene in enumerate(self.acceptance.plan.scenes, 1))

    def scene(self, scene_id):
        if self.section is None:
            raise ValueError("Select a section first.")
        return next(view for view in self.scenes(self.section) if view.id == scene_id)

    def visual_context(self):
        brief = self.prompts.prompts.current_context("film_brief")
        style = self.prompts.prompts.current_context("visual_style")
        return VisualContextView(brief.text if brief else "", brief.id if brief else None,
                                 style.text if style else "", style.id if style else None)

    def save_visual_context(self, brief_text, style_text):
        current = self.visual_context()
        if ((current.brief_revision_id is not None and not brief_text.strip())
                or (current.style_revision_id is not None and not style_text.strip())):
            raise ValueError("Visual context cannot be cleared; enter replacement text.")
        for kind, text, old_text, parent in (
            ("film_brief", brief_text, current.brief, current.brief_revision_id),
            ("visual_style", style_text, current.style, current.style_revision_id),
        ):
            if text != old_text:
                if not text.strip():
                    continue
                self.prompts.pin_context(kind, text, parent_revision_id=parent)
        return self.visual_context()

    def _context_ids(self, scene_id):
        if self.context_resolver is None:
            raise ValueError("Save Film Brief and Visual Style before creating the first visual prompt.")
        values = self.context_resolver(scene_id)
        if not isinstance(values, (tuple, list)) or len(values) != 2 or not all(values):
            raise ValueError("Save Film Brief and Visual Style before creating the first visual prompt.")
        return tuple(values)

    def save_prompt(self, scene_id, text):
        if not text.strip():
            raise ValueError("Visual prompt cannot be empty.")
        chosen = self.prompts.prompts.selected(scene_id)
        if chosen is None:
            revision = self.prompts.create_manual(self.acceptance.id, scene_id, *self._context_ids(scene_id), text)
        else:
            revision = self.prompts.edit_manual(chosen.revision_id, text)
        self.prompts.select(revision.id, expected_selection_id=chosen.id if chosen else None)
        return self.scene(scene_id)

    def regenerate_prompt(self, scene_id):
        chosen = self.prompts.prompts.selected(scene_id)
        revision = self.prompts.generate(self.acceptance.id, scene_id, *self._context_ids(scene_id))
        self.prompts.select(revision.id, expected_selection_id=chosen.id if chosen else None)
        return self.scene(scene_id)

    def select_prompt(self, scene_id, revision_id):
        revision = self.prompts.prompts.revision(revision_id)
        if revision.inputs.scene_id != scene_id:
            raise ValueError("Prompt variant belongs to another scene.")
        chosen = self.prompts.prompts.selected(scene_id)
        self.prompts.select(revision_id, expected_selection_id=chosen.id if chosen else None)
        return self.scene(scene_id)

    def import_image(self, scene_id, path):
        image = self.intake.import_file(self.acceptance.id, scene_id, path)
        chosen = self.images.selected(scene_id)
        self.intake.select(image.artifact_id, expected_selection_id=chosen.id if chosen else None)
        return self.scene(scene_id)

    def generate_image(self, scene_id, *, width, height, seed):
        prompt = self.prompts.selected(scene_id)
        if prompt is None:
            raise ValueError("Select a visual prompt before generating an image.")
        jobs = self.coordinator.repository
        if jobs.paused or any(attempt.status in ("queued", "running")
                              for job in jobs.jobs() for attempt in jobs.attempts(job.id)):
            raise ValueError("Resolve pending image jobs before generating another image.")
        submission = self.generation.enqueue(prompt.id, width=width, height=height, seed=seed)
        if submission.cached_artifact_id is not None:
            artifact_id = submission.cached_artifact_id
        else:
            claim = self.coordinator.claim_next("desktop-scene-image")
            if claim is None or claim.id != submission.attempt.id:
                raise ValueError("Image job could not claim its reserved attempt.")
            artifact_id = self.generation.run(claim).artifact_id
        chosen = self.images.selected(scene_id)
        self.generation.select(artifact_id, expected_selection_id=chosen.id if chosen else None)
        return self.scene(scene_id)

    def prepare_background_image(self, scene_id, *, width, height, seed):
        """Keep job and project state on the GUI thread; return pure inference inputs."""
        if not getattr(self.generation.provider, "requires_background", False):
            raise ValueError("Background generation requires the managed local image provider.")
        prompt = self.prompts.selected(scene_id)
        if prompt is None:
            raise ValueError("Select a visual prompt before generating an image.")
        jobs = self.coordinator.repository
        if jobs.paused or any(a.status in ("queued", "running")
                              for job in jobs.jobs() for a in jobs.attempts(job.id)):
            raise ValueError("Resolve pending image jobs before generating another image.")
        submission = self.generation.enqueue(prompt.id, width=width, height=height, seed=seed)
        if submission.cached_artifact_id is not None:
            chosen = self.images.selected(scene_id)
            self.generation.select(submission.cached_artifact_id,
                                   expected_selection_id=chosen.id if chosen else None)
            return None, self.scene(scene_id)
        claim = self.coordinator.claim_next("desktop-scene-image")
        if claim is None or claim.id != submission.attempt.id:
            raise ValueError("Image job could not claim its reserved attempt.")
        job = jobs.get_job(claim.job_id)
        request = ImageGenerationRequest(**json.loads(job.request.settings_json)["request"])
        return (claim, scene_id, self.generation.provider, request), None

    def finish_background_image(self, claim, scene_id, result=None, error=None):
        actual = self.coordinator.repository.get_attempt(claim.id)
        if actual.cancel_requested:
            if actual.status == "running":
                self.coordinator.acknowledge_cancel(claim)
            raise RuntimeError("Local image generation canceled.")
        if error is not None:
            if actual.status == "running":
                self.coordinator.fail(claim, f"image_generation: {type(error).__name__}: {str(error)[:1024]}")
            raise error
        artifact_id = self.generation.run(claim, generated_result=result).artifact_id
        chosen = self.images.selected(scene_id)
        self.generation.select(artifact_id, expected_selection_id=chosen.id if chosen else None)
        return self.scene(scene_id)

    def cancel_background_image(self, claim):
        self.coordinator.cancel(claim.id)
        self.generation.provider.cancel()

    def select_image(self, scene_id, artifact_id):
        image = self.images.image(artifact_id)
        if image.scene_id != scene_id:
            raise ValueError("Image variant belongs to another scene.")
        chosen = self.images.selected(scene_id)
        self.intake.select(artifact_id, expected_selection_id=chosen.id if chosen else None)
        return self.scene(scene_id)

    def image_bytes(self, artifact_id):
        self.images.image(artifact_id)  # Revalidate ownership, checksum and measurements.
        with self.store.open_artifact_id(artifact_id) as source:
            return source.read()

    def image_capabilities(self):
        return self.generation.capabilities() if self.generation.provider is not None else None


__all__ = ["ImageVariant", "PlanSceneView", "PromptVariant", "ScenePlanView", "SceneServices", "SceneView",
           "VisualContextView"]

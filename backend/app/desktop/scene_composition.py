"""Explicit D022 composition over the existing scene/prompt/image services."""

from app.application.image_generation import ImageGenerationService
from app.application.image_intake import ImageIntakeService
from app.application.image_upscale import ImageUpscaleService
from app.application.result_publication import ResultPublicationService
from app.application.visual_prompts import VisualPromptService
from app.desktop.scene_services import SceneServices
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.storage.image_generation import ImageResultIndex, ProjectImageGeneration
from app.storage.local_store import LocalArtifactStore
from app.storage.scene_plans import ProjectScenePlans
from app.storage.video_plans import ProjectVideoPlans
from app.domain.plan_script import planned_section_identity
from app.domain.scene_plan import ScenePacingProfile


def compose_scenes(session, *, prompt_provider=None, prompt_identity=None, image_provider=None, upscale_provider=None,
                   context_resolver=None, image_generators=(), default_generator_id=None):
    jobs = JobRepository(session.repository)
    index = ImageResultIndex(session.repository, jobs)
    store = LocalArtifactStore(index.root, index=index)
    store.recovery_report = store.recover()
    artifacts = ProjectImageGeneration(index, store)
    coordinator = JobCoordinator(jobs)
    prompts = VisualPromptService(index.prompts, prompt_provider, generation_identity=prompt_identity)
    if context_resolver is None:
        def context_resolver(_scene_id):
            brief = index.prompts.current_context("film_brief")
            style = index.prompts.current_context("visual_style")
            return brief.id if brief else None, style.id if style else None
    publication = ResultPublicationService(index, store)
    video_plans = ProjectVideoPlans(session.repository, store)
    def pacing_profile(section):
        selected = video_plans.selected()
        if selected is None:
            return None
        planned_ids = {planned_section_identity(selected.project_id, item.id)
                       for group in selected.groups for item in group.sections}
        return ScenePacingProfile.for_format(selected.format) if section.section_id in planned_ids else None
    generation_services = {
        option.id: ImageGenerationService(publication, coordinator, artifacts, option.provider)
        for option in image_generators
    }
    legacy_generation = ImageGenerationService(publication, coordinator, artifacts, image_provider)
    generation = generation_services.get(default_generator_id, legacy_generation)
    return SceneServices(plans=ProjectScenePlans(session.repository, store), prompts=prompts,
                         intake=ImageIntakeService(artifacts.images), generation=generation,
                         images=artifacts.images, store=store, coordinator=coordinator,
                         context_resolver=context_resolver,
                         upscale=ImageUpscaleService(artifacts.images, store, upscale_provider),
                         image_generators=image_generators, generation_services=generation_services,
                         default_generator_id=default_generator_id,
                         pacing_profile_resolver=pacing_profile)


__all__ = ["compose_scenes"]

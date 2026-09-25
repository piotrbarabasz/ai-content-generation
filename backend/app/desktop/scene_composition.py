"""Explicit D022 composition over the existing scene/prompt/image services."""

from app.application.image_generation import ImageGenerationService
from app.application.image_intake import ImageIntakeService
from app.application.result_publication import ResultPublicationService
from app.application.visual_prompts import VisualPromptService
from app.desktop.scene_services import SceneServices
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.storage.image_generation import ImageResultIndex, ProjectImageGeneration
from app.storage.local_store import LocalArtifactStore
from app.storage.scene_plans import ProjectScenePlans


def compose_scenes(session, *, prompt_provider=None, prompt_identity=None, image_provider=None,
                   context_resolver=None):
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
    generation = ImageGenerationService(ResultPublicationService(index, store), coordinator,
                                        artifacts, image_provider)
    return SceneServices(plans=ProjectScenePlans(session.repository, store), prompts=prompts,
                         intake=ImageIntakeService(artifacts.images), generation=generation,
                         images=artifacts.images, store=store, coordinator=coordinator,
                         context_resolver=context_resolver)


__all__ = ["compose_scenes"]

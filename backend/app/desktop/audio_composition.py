"""Explicit D010/D007 composition, invoked on the project coordinator thread."""

from app.application.result_publication import ResultPublicationService
from app.application.section_audio import SectionAudioService
from app.desktop.audio_services import AudioServices
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.runtime.supervisor import WorkerSupervisor
from app.runtime.section_preview import ManagedSectionPreviewProvider
from app.storage.local_store import LocalArtifactStore
from app.storage.result_publication import ResultArtifactIndex


def compose_audio(session, *, catalog, voices, outputs, launch, preview_root,
                  preview_builder=None, providers=("piper",), settings=None):
    """voices/outputs may be the existing ManagedSectionAudio instance.

    Preview and production are explicitly composed: no desktop model download,
    hidden mock, device/provider fallback or separate selection registry.
    """
    jobs = JobRepository(session.repository)
    coordinator = JobCoordinator(jobs)
    index = ResultArtifactIndex(session.repository, jobs)
    store = LocalArtifactStore(index.root, index=index)
    production = SectionAudioService(ResultPublicationService(index, store), jobs, voices, outputs)
    supervisor = WorkerSupervisor(coordinator, launch, completion_handler=production.complete)
    if preview_builder is None:
        preview_builder = lambda _config, prepared=None: ManagedSectionPreviewProvider(voices, launch, prepared)
    return AudioServices(catalog=catalog, production=production, coordinator=coordinator,
                         supervisor=supervisor, index=index, store=store,
                         preview_root=preview_root, preview_builder=preview_builder,
                         providers=providers, settings=settings)

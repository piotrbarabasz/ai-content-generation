"""Explicit D010/D007 composition, invoked on the project coordinator thread."""

from app.application.result_publication import ResultPublicationService
from app.application.section_audio import SectionAudioService
from app.desktop.audio_services import AudioServices
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.runtime.supervisor import WorkerLimits, WorkerSupervisor
from app.runtime.section_preview import ManagedSectionPreviewProvider
from app.storage.local_store import LocalArtifactStore
from app.storage.result_publication import ResultArtifactIndex


def compose_audio(session, *, catalog, voices, outputs, launch, preview_root,
                  preview_builder=None, providers=("piper",), settings=None,
                  device=None, limits=WorkerLimits(), reference_audio=None):
    """voices/outputs may be the existing ManagedSectionAudio instance.

    Preview and production are explicitly composed: no desktop model download,
    hidden mock, device/provider fallback or separate selection registry.
    """
    jobs = JobRepository(session.repository)
    coordinator = JobCoordinator(jobs)
    index = ResultArtifactIndex(session.repository, jobs)
    store = LocalArtifactStore(index.root, index=index)
    production = SectionAudioService(ResultPublicationService(index, store), jobs, voices, outputs)
    supervisor = WorkerSupervisor(coordinator, launch, completion_handler=production.complete,
                                  device=device, limits=limits)
    if preview_builder is None:
        preview_builder = lambda _config, prepared=None, resolved_reference=None: ManagedSectionPreviewProvider(
            voices, launch, prepared, device=device, limits=limits,
            resolved_reference=resolved_reference)
    return AudioServices(catalog=catalog, production=production, coordinator=coordinator,
                         supervisor=supervisor, index=index, store=store,
                         preview_root=preview_root, preview_builder=preview_builder,
                         providers=providers, settings=settings).configure_reference_audio(reference_audio)


def compose_candidate_chatterbox_audio(session, *, managed, preview_root, reference_audio=None):
    """Explicit candidate integration; installed default stays approved Piper only."""
    from app.providers.tts_catalog import build_tts_catalog
    from app.runtime.chatterbox_audio import CHATTERBOX_LIMITS
    return compose_audio(session, catalog=build_tts_catalog(), voices=managed, outputs=managed,
                         launch=managed.worker_launch(), preview_root=preview_root,
                         providers=("chatterbox_v3",), settings={"chatterbox_v3": {"device": managed.device.effective}},
                         device=managed.device, limits=CHATTERBOX_LIMITS,
                         reference_audio=reference_audio)

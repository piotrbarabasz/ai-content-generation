"""Provider-free opt-in composition for synchronized caption export."""

from app.application.captions import SynchronizedCaptionService
from app.storage.captions import ProjectCaptionTracks
from app.storage.local_store import LocalArtifactStore


def compose_captions(session):
    store = LocalArtifactStore.for_project(session.repository)
    return SynchronizedCaptionService(ProjectCaptionTracks(session.repository, store))


__all__ = ["compose_captions"]

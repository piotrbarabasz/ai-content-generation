"""Explicit opt-in composition for D031 project speech alignment."""

from app.application.speech_alignment import SpeechAlignmentService
from app.providers.whisperx_alignment import WhisperXAlignmentAdapter
from app.storage.local_store import LocalArtifactStore
from app.storage.speech_alignment import ProjectSpeechAlignments


def compose_speech_alignment(session, provider, *, confidence_threshold=0.6):
    store = LocalArtifactStore.for_project(session.repository)
    return SpeechAlignmentService(
        ProjectSpeechAlignments(session.repository, store), provider,
        confidence_threshold=confidence_threshold)


def compose_whisperx_alignment(session, backend, *, model_name="whisperx-align",
                               runtime_version="unreported", confidence_threshold=0.6):
    provider = WhisperXAlignmentAdapter(
        backend, model_name=model_name, runtime_version=runtime_version)
    return compose_speech_alignment(
        session, provider, confidence_threshold=confidence_threshold)


__all__ = ["compose_speech_alignment", "compose_whisperx_alignment"]

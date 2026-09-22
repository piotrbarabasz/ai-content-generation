"""D030 immutable WAV intake, approval history and controlled resolution."""

from __future__ import annotations

import io
import wave

import pytest

from app.application.projects import ProjectSession
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.reference_audio import ProjectReferenceAudio


def wav(*, frames=16_000, sample_rate=16_000, channels=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setparams((channels, 2, sample_rate, 0, "NONE", "not compressed"))
        writer.writeframes(b"\x01\x00" * frames * channels)
    return output.getvalue()


@pytest.fixture
def library(tmp_path):
    with ProjectSession.create(tmp_path / "project", name="Reference", language="pl",
                               repository_factory=ProjectRepository) as session:
        store = LocalArtifactStore.for_project(session.repository)
        yield ProjectReferenceAudio(session.repository, store, tmp_path / "runtime-references"), store


def test_import_is_pending_until_approved_and_resolves_only_controlled_copy(library, tmp_path):
    references, _ = library
    external = tmp_path / "private speaker.wav"
    external.write_bytes(wav())
    source = references.import_file(external)
    assert source.source_name == "private speaker.wav"
    assert references.resolve(source.artifact_id) is None
    decision = references.approve(source.artifact_id, "speaker-consent-2026")
    resolved = references.resolve(source.artifact_id)
    assert resolved is not None and resolved.runtime_path.is_relative_to(references.runtime_root)
    assert resolved.runtime_path != external and resolved.checksum == source.checksum
    assert decision.source_checksum == source.checksum
    assert references.approved()[0].metadata() == {
        "checksum": source.checksum, "approval_label": "speaker-consent-2026", "approved": True}


def test_rejection_and_reapproval_preserve_append_only_history(library, tmp_path):
    references, store = library
    source_path = tmp_path / "speaker.wav"
    source_path.write_bytes(wav())
    source = references.import_file(source_path)
    approved = references.approve(source.artifact_id, "consent-v1")
    rejected = references.reject(source.artifact_id, "consent-withdrawn")
    assert references.resolve(source.artifact_id) is None and references.approved() == ()
    restored = references.approve(source.artifact_id, "consent-v2")
    assert [item.id for item in references.decision_history(source.artifact_id)] == [
        approved.id, rejected.id, restored.id]
    assert references.resolve(source.artifact_id).approval_label == "consent-v2"
    assert len([item for item in store.list_artifacts()
                if item.artifact_type == "reference_audio_decision"]) == 3


def test_changed_source_or_controlled_cache_fails_closed(library, tmp_path):
    references, store = library
    path = tmp_path / "speaker.wav"
    path.write_bytes(wav())
    source = references.import_file(path)
    references.approve(source.artifact_id, "approved")
    resolved = references.resolve(source.artifact_id)
    resolved.runtime_path.write_bytes(wav(frames=8_000))
    with pytest.raises(ValueError, match="cache was modified"):
        references.resolve(source.artifact_id)
    manifest = next(item for item in store.list_artifacts() if item.artifact_id == source.artifact_id)
    (store.root / manifest.storage_key).write_bytes(wav(frames=4_000))
    with pytest.raises(ValueError, match="retained measurements"):
        references.resolve(source.artifact_id)


def test_changed_import_gets_new_opaque_identity_and_checksum(library, tmp_path):
    references, _ = library
    path = tmp_path / "speaker.wav"
    path.write_bytes(wav(frames=16_000))
    first = references.import_file(path)
    path.write_bytes(wav(frames=20_000))
    second = references.import_file(path)
    assert first.artifact_id != second.artifact_id
    assert first.checksum != second.checksum
    assert tuple(item.artifact_id for item in references.sources()) == (first.artifact_id, second.artifact_id)


def test_source_and_approval_history_survive_project_reopen(tmp_path):
    workspace = tmp_path / "project"
    runtime = tmp_path / "runtime-references"
    path = tmp_path / "speaker.wav"
    path.write_bytes(wav())
    with ProjectSession.create(workspace, name="Reference", language="pl",
                               repository_factory=ProjectRepository) as session:
        references = ProjectReferenceAudio(
            session.repository, LocalArtifactStore.for_project(session.repository), runtime)
        source = references.import_file(path)
        decisions = (
            references.approve(source.artifact_id, "consent-v1"),
            references.reject(source.artifact_id, "withdrawn"),
            references.approve(source.artifact_id, "consent-v2"),
        )
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as session:
        references = ProjectReferenceAudio(
            session.repository, LocalArtifactStore.for_project(session.repository), runtime)
        assert [item.id for item in references.decision_history(source.artifact_id)] == [
            item.id for item in decisions]
        resolved = references.resolve(source.artifact_id)
        assert resolved is not None and resolved.artifact_id == source.artifact_id
        assert resolved.approval_label == "consent-v2"


@pytest.mark.parametrize(
    "payload",
    [b"not wav", wav(frames=1), wav(channels=3)],
    ids=("not-wav", "too-short", "too-many-channels"),
)
def test_invalid_or_out_of_policy_audio_is_never_published(library, tmp_path, payload):
    references, store = library
    path = tmp_path / "invalid.wav"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        references.import_file(path)
    assert store.list_artifacts() == ()

"""D005 mappings consume immutable D004 manifests and D003 section selections."""

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.application.invalidation import Freshness, evaluate_freshness
from app.application.projects import ProjectSession
from app.domain.base import DomainValidationError
from app.domain.dependencies import DependencyDeclaration, InputEdge, RequestFingerprint, content_fingerprint
from app.domain.narrative_segment import SectionRevision
from app.storage.artifact_index import ProjectArtifactIndex
from app.storage.dependency_index import ArtifactDependencyIndex
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository


def test_section_edit_and_reopen_preserve_consumed_request_and_old_bytes(tmp_path):
    with ProjectSession.create(tmp_path, name="Dependencies", repository_factory=ProjectRepository) as session:
        draft = session.active_script
        section = SectionRevision.create(project_id=session.project.id, title="B", text="B1", role="body")
        initial = replace(draft, id="script_with_B", parent_revision_id=draft.id, sections=(section,))
        session.save_script(initial, expected_active_revision_id=draft.id)
        key = f"section:{section.section_id}:text"
        request = RequestFingerprint.create("raw-tts", "1", inputs=[InputEdge("text", key, content_fingerprint(section.text))])
        store = LocalArtifactStore.for_project(session.repository)
        published = store.save_artifact("voice.wav", b"retained B1 audio", DependencyDeclaration("B:raw", request).to_metadata())
        records = ArtifactDependencyIndex(ProjectArtifactIndex(session.repository)).records()
        assert records[published.artifact_id].checksum == hashlib.sha256(b"retained B1 audio").hexdigest()
        session.edit_section(section.section_id, title="Retitled B", text="B1")
        unchanged_text = session.active_script.section(section.section_id)
        assert unchanged_text.id != section.id
        assert evaluate_freshness(requests={"B:raw": request}, sources={key: content_fingerprint(unchanged_text.text)},
                                  selected={"B:raw": published.artifact_id}, artifacts=records)["B:raw"].state == Freshness.FRESH
        session.edit_section(section.section_id, text="B2")
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as session:
        store = LocalArtifactStore.for_project(session.repository)
        restored = ArtifactDependencyIndex(ProjectArtifactIndex(session.repository)).records()
        assert restored == records
        source = session.active_script.section(section.section_id)
        result = evaluate_freshness(requests={"B:raw": request}, sources={key: content_fingerprint(source.text)},
                                    selected={"B:raw": published.artifact_id}, artifacts=restored)
        assert result["B:raw"].state == Freshness.STALE
        assert store.read_artifact(published.storage_key) == b"retained B1 audio"
        assert session.repository.get_section(section.id) == section


@pytest.mark.parametrize("bad", ["missing", "checksum", "format"])
def test_index_rejects_invalid_explicit_dependencies_without_mutating_catalog(tmp_path, bad):
    with ProjectSession.create(tmp_path, name="Invalid edges", repository_factory=ProjectRepository) as session:
        store = LocalArtifactStore.for_project(session.repository)
        raw = store.save_artifact("raw.wav", b"raw")  # Legacy/untracked imports are not inferred.
        request = RequestFingerprint.create("tempo", "1", inputs=[InputEdge.artifact(
            "raw", "B:raw", "missing" if bad == "missing" else raw.artifact_id,
            content_fingerprint("wrong") if bad == "checksum" else raw.checksum)])
        metadata = DependencyDeclaration("B:processed", request).to_metadata()
        if bad == "format":
            metadata["desktop_dependencies"]["version"] = 99
        store.save_artifact("processed.wav", b"processed", metadata)
        before = store.list_artifacts()
        with pytest.raises(DomainValidationError, match="dependency metadata"):
            ArtifactDependencyIndex(ProjectArtifactIndex(session.repository)).records()
        assert store.list_artifacts() == before


def test_valid_index_edges_resolve_actual_published_bytes_and_leave_legacy_untracked(tmp_path):
    with ProjectSession.create(tmp_path, name="Valid edges", repository_factory=ProjectRepository) as session:
        store = LocalArtifactStore.for_project(session.repository)
        raw = store.save_artifact("raw.wav", b"raw")
        request = RequestFingerprint.create("tempo", "1", inputs=[InputEdge.artifact("raw", "B:raw", raw.artifact_id, raw.checksum)])
        processed = store.save_artifact("processed.wav", b"processed", DependencyDeclaration("B:processed", request).to_metadata())
        records = ArtifactDependencyIndex(ProjectArtifactIndex(session.repository)).records()
        assert set(records) == {processed.artifact_id}
        assert records[processed.artifact_id].declaration.request == request


def test_index_rejects_crossed_logical_binding_even_when_consumed_bytes_match(tmp_path):
    with ProjectSession.create(tmp_path, name="Crossed binding", repository_factory=ProjectRepository) as session:
        store = LocalArtifactStore.for_project(session.repository)
        raw = store.save_artifact("raw.wav", b"raw", DependencyDeclaration(
            "A:raw", RequestFingerprint.create("tts", "1")).to_metadata())
        request = RequestFingerprint.create("tempo", "1", inputs=[InputEdge.artifact("raw", "B:raw", raw.artifact_id, raw.checksum)])
        store.save_artifact("processed.wav", b"processed", DependencyDeclaration("B:processed", request).to_metadata())
        with pytest.raises(DomainValidationError, match="binding mismatch"):
            ArtifactDependencyIndex(ProjectArtifactIndex(session.repository)).records()


def test_invalidation_imports_no_infrastructure_or_providers():
    code = """
import sys
from app.application.invalidation import evaluate_freshness
for name in ('sqlite3', 'app.storage', 'app.providers', 'app.tts', 'PySide6', 'fastapi', 'torch'):
    assert name not in sys.modules, name
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr

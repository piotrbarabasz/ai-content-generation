"""D039 SQLite persistence, token checks and retained section media."""

from dataclasses import replace
import sqlite3

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


def seed(session):
    draft = session.active_script
    sections = tuple(SectionRevision.create(project_id=session.project.id, title=name, text=f"{name} text", role="body")
                     for name in "ABC")
    script = replace(draft, id="script_abc", parent_revision_id=draft.id, sections=sections)
    session.save_script(script, expected_active_revision_id=draft.id)
    return script


def publish_section_media(session, script):
    store = LocalArtifactStore.for_project(session.repository)
    selected = {}
    for section in script.sections:
        key = f"section:{section.section_id}:text"
        output_key = f"section:{section.section_id}:raw"
        request = RequestFingerprint.create("raw-tts", "1", inputs=[
            InputEdge("text", key, content_fingerprint(section.text))
        ])
        manifest = store.save_artifact(
            f"{section.title}.wav", section.text.encode(), DependencyDeclaration(output_key, request).to_metadata()
        )
        selected[output_key] = manifest.artifact_id
    return store, selected, ArtifactDependencyIndex(ProjectArtifactIndex(session.repository)).records()


def test_reorder_round_trips_exact_revisions_and_existing_media_without_regeneration(tmp_path):
    with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Reorder") as session:
        original = seed(session)
        store, selected, records = publish_section_media(session, original)
        result = session.reorder_sections(
            [original.sections[2].section_id, original.sections[0].section_id, original.sections[1].section_id],
            expected_active_revision_id=original.id,
        )
        assert result.script.sections == (original.sections[2], original.sections[0], original.sections[1])
        assert result.impact.reusable_section_ids == tuple(section.section_id for section in result.script.sections)
        assert set(result.impact.project_outputs) == {"timeline", "video_render"}
        assert ArtifactDependencyIndex(ProjectArtifactIndex(session.repository)).records() == records
        bytes_before = {artifact.storage_key: store.read_artifact(artifact.storage_key) for artifact in store.list_artifacts()}

    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        active = reopened.active_script
        assert active == result.script
        assert reopened.repository.get_script(original.id) == original
        assert tuple(section.id for section in active.sections) == tuple(section.id for section in result.script.sections)
        restored_store = LocalArtifactStore.for_project(reopened.repository)
        restored_records = ArtifactDependencyIndex(ProjectArtifactIndex(reopened.repository)).records()
        sources = {f"section:{section.section_id}:text": content_fingerprint(section.text) for section in active.sections}
        freshness = evaluate_freshness(requests={record.declaration.output_key: record.declaration.request
                                                for record in restored_records.values()}, sources=sources,
                                       selected=selected, artifacts=restored_records)
        assert set(value.state for value in freshness.values()) == {Freshness.FRESH}
        assert {artifact.storage_key: restored_store.read_artifact(artifact.storage_key)
                for artifact in restored_store.list_artifacts()} == bytes_before


@pytest.mark.parametrize("order", [[], ["missing"], ["A", "A", "B"]])
def test_invalid_or_stale_order_never_changes_snapshot_or_history(tmp_path, order):
    with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Invalid reorder") as session:
        original = seed(session)
        before = session.repository.script_history()
        ids = [section.section_id for section in original.sections]
        requested = order if order else order
        if order and order[0] in "ABC":
            requested = [ids[0], ids[0], ids[1]]
        with pytest.raises(DomainValidationError):
            session.reorder_sections(requested, expected_active_revision_id=original.id)
        with pytest.raises(DomainValidationError, match="refresh"):
            session.reorder_sections(ids, expected_active_revision_id="stale")
        assert session.active_script == original
        assert session.repository.script_history() == before


def test_repository_failure_rolls_back_complete_reorder_snapshot_and_active_pointer(tmp_path):
    with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Rollback reorder") as session:
        original = seed(session)
        before = session.repository.script_history()
        session.repository._connection.execute("""CREATE TEMP TRIGGER fail_reorder_selection
            BEFORE UPDATE OF active_script_revision_id ON projects
            BEGIN SELECT RAISE(ABORT, 'injected reorder selection failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="injected reorder"):
            session.reorder_sections(list(reversed([section.section_id for section in original.sections])),
                                     expected_active_revision_id=original.id)
        assert session.active_script == original
        assert session.repository.script_history() == before
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        assert reopened.active_script == original

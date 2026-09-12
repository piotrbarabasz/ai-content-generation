"""SQLite project failures and ownership checks; all databases are temporary."""

from dataclasses import replace
import sqlite3

import pytest

from app.application.projects import ProjectSession
from app.domain.narrative_segment import SectionRevision
from app.domain.script import ScriptRevision
from app.storage.project_repository import (
    APPLICATION_ID, ProjectRepository, ProjectRepositoryError, ProjectWriterBusyError,
    RevisionConflictError, UnsupportedSchemaError,
)


def seed(session):
    initial = session.active_script
    sections = tuple(SectionRevision.create(project_id=session.project.id, title=name,
                                            text=f"{name}1", role="body") for name in "ABC")
    script = replace(initial, id="script_abc", parent_revision_id=initial.id, sections=sections)
    session.save_script(script, expected_active_revision_id=initial.id)
    return script


def test_failed_selection_rolls_back_inserted_revisions(tmp_path):
    with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Rollback") as session:
        before = seed(session)
        b1 = before.sections[1]
        after = before.edit_section(b1.section_id, text="B2")
        connection = session.repository._connection
        connection.execute("""CREATE TEMP TRIGGER fail_selection
            BEFORE UPDATE OF active_script_revision_id ON projects
            BEGIN SELECT RAISE(ABORT, 'injected selection failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            session.save_script(after, expected_active_revision_id=before.id)
        assert session.active_script == before
        assert session.repository.section_history(b1.section_id) == (b1,)
        with pytest.raises(KeyError):
            session.repository.get_script(after.id)
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        assert reopened.active_script == before
        assert reopened.repository.section_history(b1.section_id) == (b1,)


def test_second_writer_is_rejected_between_transactions_and_after_failure(tmp_path):
    with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Owner") as first:
        seed(first)
        for _ in range(2):
            with pytest.raises(ProjectWriterBusyError):
                ProjectSession.open(tmp_path, repository_factory=ProjectRepository)
        with pytest.raises(RevisionConflictError):
            first.save_script(first.active_script, expected_active_revision_id="stale")
        with pytest.raises(ProjectWriterBusyError):
            ProjectSession.open(tmp_path, repository_factory=ProjectRepository)
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as next_owner:
        assert next_owner.project.name == "Owner"


@pytest.mark.parametrize("version, application", [(0, 0), (2, APPLICATION_ID), (999, APPLICATION_ID), (1, 0)])
def test_unsupported_format_stays_untouched(tmp_path, version, application):
    path = tmp_path / "project.sqlite"
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE sentinel (value TEXT)")
        database.execute("INSERT INTO sentinel VALUES ('keep me')")
        database.execute(f"PRAGMA user_version = {version}")
        database.execute(f"PRAGMA application_id = {application}")
    before = path.read_bytes()
    with pytest.raises(UnsupportedSchemaError):
        ProjectSession.open(tmp_path, repository_factory=ProjectRepository)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT value FROM sentinel").fetchone()[0] == "keep me"


def test_open_missing_project_does_not_initialize_and_create_never_overwrites(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        ProjectSession.open(missing, repository_factory=ProjectRepository)
    assert not missing.exists()
    path = tmp_path / "project.sqlite"
    path.write_bytes(b"an existing file")
    with pytest.raises(FileExistsError):
        ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Do not overwrite")
    assert path.read_bytes() == b"an existing file"


def test_stale_selection_is_rejected_without_history_changes(tmp_path):
    with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Stale") as session:
        initial = seed(session)
        edited = session.edit_section(initial.sections[1].section_id, text="B2")
        history = session.repository.script_history()
        stale = initial.edit_section(initial.sections[0].section_id, text="A2")
        with pytest.raises(RevisionConflictError, match="active script"):
            session.save_script(stale, expected_active_revision_id=initial.id)
        assert session.active_script == edited
        assert session.repository.script_history() == history


@pytest.mark.parametrize("kind", ["script-content", "script-order", "section-content"])
def test_immutable_ids_cannot_be_reused_with_different_payloads(tmp_path, kind):
    with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Immutable") as session:
        script = seed(session)
        if kind == "script-content":
            forged = replace(script, language="pl")
        elif kind == "script-order":
            forged = replace(script, sections=tuple(reversed(script.sections)))
        else:
            forged = replace(script, id="new_script_revision", parent_revision_id=script.id,
                             sections=(replace(script.sections[0], text="overwritten"), *script.sections[1:]))
        with pytest.raises(RevisionConflictError):
            session.save_script(forged, expected_active_revision_id=script.id)
        assert session.active_script == script


@pytest.mark.parametrize("kind", ["foreign-project", "missing-script-parent", "foreign-script-parent",
                                     "missing-section-parent", "foreign-section-parent"])
def test_invalid_ownership_or_lineage_does_not_publish(tmp_path, kind):
    with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Lineage") as session:
        script = seed(session)
        if kind == "foreign-project":
            invalid = ScriptRevision.create(project_id="another_project")
        elif kind == "missing-script-parent":
            invalid = replace(script, id="new_script", parent_revision_id="missing")
        elif kind == "foreign-script-parent":
            invalid = replace(script, id="new_script", script_id="different_script", parent_revision_id=script.id)
        else:
            parent_id = "missing" if kind == "missing-section-parent" else script.sections[1].id
            section = replace(script.sections[0], id="new_section", parent_revision_id=parent_id)
            invalid = replace(script, id="new_script", parent_revision_id=script.id,
                              sections=(section, *script.sections[1:]))
        history = session.repository.script_history()
        with pytest.raises((ProjectRepositoryError, KeyError)):
            session.save_script(invalid, expected_active_revision_id=script.id)
        assert session.active_script == script
        assert session.repository.script_history() == history


def test_reselecting_stored_snapshots_and_sections_preserves_history(tmp_path):
    with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Select") as session:
        script = seed(session)
        b1 = script.sections[1]
        b2_script = session.edit_section(b1.section_id, text="B2")
        session.repository.select_script(script.id, expected_active_revision_id=b2_script.id)
        assert session.active_script == script
        selected = session.select_section_revision(b2_script.sections[1].id)
        assert selected.section(b1.section_id) == b2_script.sections[1]
        assert session.repository.get_script(script.id) == script
        assert session.repository.get_script(b2_script.id) == b2_script


def test_invalid_initial_lineage_does_not_leave_partial_schema(tmp_path):
    with ProjectSession.create(tmp_path / "source", repository_factory=ProjectRepository, name="Source") as session:
        project = session.project
        invalid = replace(session.active_script, parent_revision_id="missing")
    with pytest.raises(KeyError):
        ProjectRepository.create(tmp_path / "failed", project, invalid)
    path = tmp_path / "failed/project.sqlite"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_context_exception_releases_writer_and_returned_metadata_is_detached(tmp_path):
    with pytest.raises(RuntimeError):
        with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Original") as session:
            session.project.name = "not persisted"
            raise RuntimeError("leave session")
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        assert reopened.project.name == "Original"

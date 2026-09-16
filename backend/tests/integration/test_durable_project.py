"""Durable editing, workspace relocation and real process locking/crash recovery."""

from dataclasses import replace
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from app.application.projects import ProjectSession
from app.domain.narrative_segment import SectionRevision
from app.storage.project_repository import ProjectRepository


def run_child(code, workspace):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    return subprocess.run([sys.executable, "-c", code, str(workspace)], capture_output=True,
                          text=True, env=env, timeout=15)


def test_application_import_does_not_load_infrastructure(tmp_path):
    result = run_child("""
import sys
from app.application.projects import ProjectSession
for name in ('sqlite3', 'app.storage', 'PySide6', 'fastapi', 'torch'):
    assert name not in sys.modules, name
""", tmp_path)
    assert result.returncode == 0, result.stderr


def test_create_edit_close_reopen_and_move_workspace(tmp_path):
    workspace = tmp_path / "Original project"
    with ProjectSession.create(workspace, repository_factory=ProjectRepository, name="Zażółć gęślą jaźń", language="pl") as session:
        project = session.project
        draft = session.active_script
        abc = replace(draft, id="abc", parent_revision_id=draft.id, sections=tuple(
            SectionRevision.create(project_id=project.id, title=name, text=f"{name}1", role="body") for name in "ABC"))
        session.save_script(abc, expected_active_revision_id=draft.id)
        a, b1, c = abc.sections
        edited = session.edit_section(b1.section_id, text="B2: tekst po zmianie", title="B updated")
        reordered = session.reorder_sections(
            [c.section_id, b1.section_id, a.section_id], expected_active_revision_id=edited.id
        ).script
        history = session.repository.script_history()
    # Relocate only a closed workspace; there are no absolute media/root paths in its state.
    moved = tmp_path / "Przeniesiony żółty projekt"
    shutil.move(str(workspace), str(moved))
    with sqlite3.connect(moved / "project.sqlite") as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 1
        assert database.execute("SELECT workspace_ref FROM projects").fetchone()[0] == "."
        assert str(workspace) not in "\n".join(database.iterdump())
    with ProjectSession.open(moved, repository_factory=ProjectRepository) as reopened:
        assert reopened.project == project
        assert reopened.active_script == reordered
        assert reopened.repository.script_history() == history
        assert reopened.repository.get_script(draft.id) == draft
        assert reopened.repository.get_script(abc.id) == abc
        assert reopened.repository.get_script(edited.id) == edited
        assert reopened.repository.section_history(b1.section_id) == (b1, edited.section(b1.section_id))
        assert reopened.active_script.sections == (c, edited.section(b1.section_id), a)
        assert reopened.repository.workspace == moved.resolve()


def test_writer_is_exclusive_across_processes_until_closed(tmp_path):
    code = """
import sys
from app.application.projects import ProjectSession
from app.storage.project_repository import ProjectRepository, ProjectWriterBusyError
try:
    with ProjectSession.open(sys.argv[1], repository_factory=ProjectRepository):
        pass
except ProjectWriterBusyError:
    sys.exit(23)
"""
    with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Writer"):
        result = run_child(code, tmp_path)
        assert result.returncode == 23, result.stderr
    result = run_child(code, tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("commit", [False, True])
def test_process_exit_releases_writer_and_keeps_only_committed_selection(tmp_path, commit):
    with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Crash") as session:
        draft = session.active_script
        section = SectionRevision.create(project_id=session.project.id, title="B", text="B1", role="body")
        initial = replace(draft, id="B1-script", parent_revision_id=draft.id, sections=(section,))
        session.save_script(initial, expected_active_revision_id=draft.id)
    code = """
import os, sys
from app.application.projects import ProjectSession
from app.storage.project_repository import ProjectRepository
session = ProjectSession.open(sys.argv[1], repository_factory=ProjectRepository)
original = session.repository._set_active
def crash_after_update(revision_id):
    original(revision_id)
    os._exit(19)
COMMIT_OR_CRASH
session.edit_section(session.active_script.sections[0].section_id, text='B2')
os._exit(19)
""".replace("COMMIT_OR_CRASH", "" if commit else "session.repository._set_active = crash_after_update")
    result = run_child(code, tmp_path)
    assert result.returncode == 19, result.stderr
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        active = reopened.active_script
        assert active.sections[0].text == ("B2" if commit else "B1")
        assert reopened.repository.get_script(initial.id) == initial
        assert len(reopened.repository.section_history(section.section_id)) == (2 if commit else 1)

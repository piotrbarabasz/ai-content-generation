"""Project index commits and recovery across actual process termination."""

import hashlib
from contextlib import closing
import io
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from app.application.projects import ProjectSession
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository, UnsupportedSchemaError


def create(workspace):
    return ProjectSession.create(workspace, name="Publication", repository_factory=ProjectRepository)


def reopen(workspace):
    return ProjectSession.open(workspace, repository_factory=ProjectRepository)


def test_project_index_survives_reopen_and_relocation_without_changing_d003_schema(tmp_path):
    workspace = tmp_path / "Original project"
    with create(workspace) as session:
        project, script = session.project, session.active_script
    before = (workspace / "project.sqlite").read_bytes()
    with reopen(workspace) as session:
        store = LocalArtifactStore.for_project(session.repository)
        first = store.import_stream("voice.wav", io.BytesIO(b"B1"))
        second = store.import_stream("voice.wav", io.BytesIO(b"B2"))
        assert first.storage_key != second.storage_key
        index_path = store._index.path
        with closing(sqlite3.connect(index_path)) as database:
            assert database.execute("SELECT project_id, root_ref FROM owner").fetchone() == (project.id, "artifacts")
            assert database.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 2
            assert database.execute("PRAGMA user_version").fetchone()[0] == 1
    assert (workspace / "project.sqlite").read_bytes() == before
    moved = tmp_path / "Zażółć relocated"
    shutil.move(str(workspace), moved)
    with reopen(moved) as session:
        store = LocalArtifactStore.for_project(session.repository)
        assert store.recovery_report == ()
        assert session.project == project and session.active_script == script
        assert store.read_artifact(first.storage_key) == b"B1"
        assert store.read_artifact(second.storage_key) == b"B2"
        assert store.list_artifacts() == tuple(sorted((first, second), key=lambda item: item.storage_key))


def test_index_transaction_failure_preserves_previous_records(tmp_path, monkeypatch):
    with create(tmp_path) as session:
        store = LocalArtifactStore.for_project(session.repository)
        previous = store.save_artifact("voice.wav", b"previous")
        insert = store._index._insert

        def fail_after_insert(connection, manifest, payload):
            insert(connection, manifest, payload)
            raise RuntimeError("injected failure before index commit")

        monkeypatch.setattr(store._index, "_insert", fail_after_insert)
        with pytest.raises(RuntimeError, match="before index commit"):
            store.save_artifact("voice.wav", b"complete but unindexed")
        assert store.list_artifacts() == (previous,)
        assert store.read_artifact(previous.storage_key) == b"previous"
    with reopen(tmp_path) as session:
        store = LocalArtifactStore.for_project(session.repository)
        assert store.recovery_report[0].state == "orphan"
        assert store.list_artifacts() == (previous,)
        with pytest.raises(FileNotFoundError):
            store.open_artifact(store.recovery_report[0].storage_key)


@pytest.mark.parametrize("phase", ["during-transfer", "before-move", "after-move", "index-insert", "after-commit"])
def test_process_crash_publication_recovery(tmp_path, phase):
    with create(tmp_path):
        pass
    code = r'''
import io, os, sys
from app.application.projects import ProjectSession
from app.storage.project_repository import ProjectRepository
from app.storage.local_store import LocalArtifactStore, CHUNK_SIZE
from app.storage import local_store
session = ProjectSession.open(sys.argv[1], repository_factory=ProjectRepository)
store = LocalArtifactStore.for_project(session.repository)
phase = sys.argv[2]
publish = local_store._publish_file
insert = store._index._insert
register = store._index.register
def move(source, destination):
    if phase == 'before-move': os._exit(17)
    publish(source, destination)
    if phase == 'after-move': os._exit(17)
def inserting(connection, manifest, payload):
    insert(connection, manifest, payload)
    if phase == 'index-insert': os._exit(17)
def registering(manifest):
    register(manifest)
    if phase == 'after-commit': os._exit(17)
class Stream(io.BytesIO):
    def read(self, size=-1):
        assert 0 < size <= CHUNK_SIZE
        if self.tell() and phase == 'during-transfer': os._exit(17)
        return super().read(size)
local_store._publish_file = move
store._index._insert = inserting
store._index.register = registering
store.import_stream('movie.mp4', Stream(b'x' * (CHUNK_SIZE + 99)))
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    child = subprocess.run([sys.executable, "-c", code, str(tmp_path), phase], capture_output=True,
                           text=True, env=env, timeout=20)
    assert child.returncode == 17, child.stderr
    with reopen(tmp_path) as session:
        store = LocalArtifactStore.for_project(session.repository)
        report = store.recovery_report
        expected = "discarded" if phase in ("during-transfer", "before-move") else "committed" if phase == "after-commit" else "orphan"
        assert len(report) == 1 and report[0].state == expected
        registered = store.list_artifacts()
        assert len(registered) == (1 if phase == "after-commit" else 0)
        if registered:
            with store.open_artifact(registered[0].storage_key) as file:
                assert hashlib.file_digest(file, "sha256").hexdigest() == registered[0].checksum
        else:
            with pytest.raises(FileNotFoundError):
                store.open_artifact(report[0].storage_key)
        assert store.recover() == (report if expected == "orphan" else ())


@pytest.mark.parametrize("kind", ["version", "owner"])
def test_index_refuses_foreign_owner_or_unknown_format_without_modification(tmp_path, kind):
    with create(tmp_path) as session:
        store = LocalArtifactStore.for_project(session.repository)
        path = store._index.path
        with closing(sqlite3.connect(path)) as database, database:
            if kind == "version":
                database.execute("PRAGMA user_version = 99")
            else:
                database.execute("UPDATE owner SET project_id = 'foreign'")
        before = path.read_bytes()
        with pytest.raises(UnsupportedSchemaError if kind == "version" else ValueError):
            LocalArtifactStore.for_project(session.repository)
        assert path.read_bytes() == before


def test_closed_project_session_cannot_publish_or_recover(tmp_path):
    with create(tmp_path) as session:
        store = LocalArtifactStore.for_project(session.repository)
    with pytest.raises(sqlite3.ProgrammingError):
        store.save_artifact("late.wav", b"late")
    with pytest.raises(sqlite3.ProgrammingError):
        store.recover()
    assert list(store._staging.iterdir()) == []


def test_project_index_cannot_silently_replace_legacy_sidecars(tmp_path):
    with create(tmp_path) as session:
        legacy = LocalArtifactStore(tmp_path / "artifacts")
        existing = legacy.save_artifact("keep.txt", b"keep")
        with pytest.raises(ValueError, match="adoption"):
            LocalArtifactStore.for_project(session.repository)
        assert legacy.read_artifact(existing.storage_key) == b"keep"

"""D042 real versioned SQLite/media fixtures and injected upgrade failures."""

from contextlib import closing
import sqlite3
import json
import subprocess
import sys

import pytest

from app.application.projects import ProjectSession
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository, UnsupportedSchemaError
from app.storage.project_migration import migrate_project, restore_backup
from tests.unit.test_project_repository import seed
from tests.integration.test_image_intake import project as image_project, selected


@pytest.fixture
def old_project(tmp_path):
    root = tmp_path / "old"
    with ProjectSession.create(root, name="Old fixture", repository_factory=ProjectRepository) as session:
        script = seed(session)
        project = session.project
        store = LocalArtifactStore.for_project(session.repository)
        selected = store.save_artifact("selected.bin", b"selected bytes", {"artifact_type": "fixture", "selected": True})
        history = store.save_artifact("history.bin", b"history bytes", {"artifact_type": "fixture"})
    # This is exactly the frozen version-1 schema, not just a changed version flag.
    with sqlite3.connect(root / "project.sqlite") as connection:
        connection.execute("DROP TABLE artifact_pins")
        connection.execute("DROP TABLE cleanup_items")
        connection.execute("PRAGMA user_version=1")
    return root, project, script, selected, history


def test_old_fixture_upgrade_preserves_all_ids_and_references(old_project, tmp_path):
    root, project, script, selected, history = old_project
    with ProjectSession.open(root, repository_factory=ProjectRepository) as session:
        assert session.project == project and session.active_script == script
        store = LocalArtifactStore.for_project(session.repository)
        assert {m.artifact_id for m in store.list_artifacts()} == {selected.artifact_id, history.artifact_id}
        assert store.read_artifact(selected.storage_key) == b"selected bytes"
        assert session.repository._connection.execute("PRAGMA user_version").fetchone()[0] == 2
    backups = tuple((root / ".backups").glob("schema-1-*"))
    assert len(backups) == 1
    restored = restore_backup(backups[0], tmp_path / "restored")
    with sqlite3.connect(restored / "project.sqlite") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT active_script_revision_id FROM projects").fetchone()[0] == script.id
    with ProjectSession.open(restored, repository_factory=ProjectRepository) as session:
        assert session.project == project and session.active_script == script
    with pytest.raises(FileExistsError):
        restore_backup(backups[0], root)


@pytest.mark.parametrize("stage", ["backup_files", "backup_ready", "tables_created", "before_commit"])
def test_injected_failure_keeps_original_and_complete_backup_restorable(old_project, tmp_path, stage):
    root, project, script, *_ = old_project
    def fail(checkpoint):
        if checkpoint == stage:
            raise RuntimeError("injected interruption")
    with pytest.raises(RuntimeError, match="interruption"):
        migrate_project(root, checkpoint=fail)
    with sqlite3.connect(root / "project.sqlite") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT active_script_revision_id FROM projects").fetchone()[0] == script.id
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='artifact_pins'").fetchall()
    backups = tuple((root / ".backups").glob("schema-1-*"))
    if stage != "backup_files":
        assert len(backups) == 1
        restore_backup(backups[0], tmp_path / "recovery")
    with ProjectSession.open(root, repository_factory=ProjectRepository) as session:
        assert session.project == project and session.active_script == script


def test_wal_committed_state_is_in_backup(old_project, tmp_path):
    root, project, script, *_ = old_project
    with closing(sqlite3.connect(root / "project.sqlite")) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("CREATE TABLE wal_sentinel (value TEXT)")
        writer.execute("INSERT INTO wal_sentinel VALUES ('committed only in WAL')")
        writer.commit()
        assert (root / "project.sqlite-wal").stat().st_size > 0
        # A live WAL reader prevents DELETE conversion; the complete backup is
        # still available and the original remains readable after this refusal.
        try:
            backup = migrate_project(root)
        except sqlite3.OperationalError:
            backup = next((root / ".backups").glob("schema-1-*"))
        restore_backup(backup, tmp_path / "wal-restored")
        with sqlite3.connect(tmp_path / "wal-restored" / "project.sqlite") as recovered:
            assert recovered.execute("SELECT value FROM wal_sentinel").fetchone()[0] == "committed only in WAL"
            assert recovered.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with ProjectSession.open(root, repository_factory=ProjectRepository) as session:
        assert session.active_script == script


def test_unknown_newer_schema_is_byte_identical_and_no_backup(old_project):
    root, *_ = old_project
    with sqlite3.connect(root / "project.sqlite") as db:
        db.execute("PRAGMA user_version=999")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(UnsupportedSchemaError):
        ProjectRepository.open(root)
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after


def test_actual_image_selection_chain_preserved_through_upgrade_and_backup(image_project, tmp_path):
    session, store, images, intake, *_ = image_project
    first, initial = selected(image_project)
    second, current = selected(image_project, expected=initial.id)
    root = session.repository.workspace
    owner = session.project.id
    session.close()
    with sqlite3.connect(root / "project.sqlite") as db:
        db.execute("DROP TABLE artifact_pins")
        db.execute("DROP TABLE cleanup_items")
        db.execute("PRAGMA user_version=1")
    with ProjectSession.open(root, repository_factory=ProjectRepository) as upgraded:
        from app.storage.scene_images import ProjectSceneImages
        retained = ProjectSceneImages(upgraded.repository, LocalArtifactStore.for_project(upgraded.repository))
        assert upgraded.project.id == owner
        assert retained.selected(first.scene_id) == current
        assert retained.history(first.scene_id) == (first, second)
        assert retained.selection_history(first.scene_id) == (initial, current)
    backup = next((root / ".backups").glob("schema-1-*"))
    restored_root = restore_backup(backup, tmp_path / "restored-selection")
    with ProjectSession.open(restored_root, repository_factory=ProjectRepository) as reopened:
        retained = ProjectSceneImages(reopened.repository, LocalArtifactStore.for_project(reopened.repository))
        assert retained.selected(first.scene_id) == current
        assert retained.image(first.artifact_id) == first


def test_abrupt_process_exit_mid_upgrade_rolls_back_and_backup_restores(old_project, tmp_path):
    root, _, script, *_ = old_project
    code = "import os, sys; from app.storage.project_migration import migrate_project; migrate_project(sys.argv[1], checkpoint=lambda stage: os._exit(42) if stage == 'before_commit' else None)"
    result = subprocess.run([sys.executable, "-c", code, str(root)], capture_output=True, timeout=30)
    assert result.returncode == 42, result.stderr
    with sqlite3.connect(root / "project.sqlite") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT active_script_revision_id FROM projects").fetchone()[0] == script.id
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    restore_backup(next((root / ".backups").glob("schema-1-*")), tmp_path / "process-recovery")


def test_corrupt_backup_refuses_restore_before_creating_destination(old_project, tmp_path):
    backup = migrate_project(old_project[0])
    metadata = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
    (backup / metadata["files"]["project.sqlite"]["file"]).write_bytes(b"corruption")
    destination = tmp_path / "should-not-exist"
    with pytest.raises(ValueError, match="checksum"):
        restore_backup(backup, destination)
    assert not destination.exists()

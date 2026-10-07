"""D047 real project graph/files: preview, protection and interrupted cleanup."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import os

import pytest

from app.application.projects import ProjectSession
from app.domain.dependencies import RequestFingerprint
from app.jobs.coordinator import JobCoordinator
from app.storage.project_repository import ProjectRepository
from app.storage.project_storage import ProjectStorage, ACTIVE, HISTORY, PINNED, JOBS, CACHE, TEMP, ORPHAN
from tests.integration.test_result_publication import composition


NOW = datetime(2030, 1, 1, tzinfo=UTC)


def old(path, days=10):
    timestamp = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (timestamp, timestamp))


def put(root, key, data=b"bytes", *, days=10):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    old(path, days)
    return path


@pytest.fixture
def storage(tmp_path):
    session = ProjectSession.create(tmp_path / "project", name="Storage", repository_factory=ProjectRepository)
    jobs, index, store, publication = composition(session)
    service = ProjectStorage(session.repository, clock=lambda: NOW)
    active = store.save_artifact("active.bin", b"active", {"disposable": True})
    historical = store.save_artifact("historical.bin", b"historical", {"disposable": True})
    retained = store.save_artifact("history.json", '{"source": "' + historical.artifact_id + '"}', {"artifact_type": "desktop_history"})
    pinned = store.save_artifact("pin.bin", b"pinned", {"disposable": True})
    orphan = store.save_artifact("orphan.bin", b"orphan", {"disposable": True})
    input_artifact = store.save_artifact("input.bin", b"input", {"disposable": True})
    for manifest in store.list_artifacts():
        old(store._artifact_path(manifest.storage_key))
    with index._connection() as connection, connection:
        connection.execute("INSERT INTO d040_heads VALUES ('active', 'generation', ?)", (active.artifact_id,))
    service.pin(pinned.artifact_id)
    coordinator = JobCoordinator(jobs)
    completed = coordinator.enqueue("finished", RequestFingerprint.create("fixture", "1"), {})
    coordinator.complete(coordinator.claim_next("worker"))
    interrupted = coordinator.enqueue("interrupted", RequestFingerprint.create("fixture", "1"),
                                      {"input": input_artifact.artifact_id})
    coordinator.claim_next("worker")
    root = session.repository.workspace
    paths = {
        "orphan": put(root, "artifacts/unregistered.bin"),
        "recent": put(root, "artifacts/recent.bin", days=1),
        "cache": put(root, "cache/previews/test.mp4"),
        "complete": put(root, "work/audio/" + sha256(completed.job_id.encode()).hexdigest() + "/chunks.wav"),
        "interrupted": put(root, "work/audio/" + sha256(interrupted.job_id.encode()).hexdigest() + "/chunks.wav"),
        "model": put(root, "cache/models/weights.bin"),
        "runtime": put(root, "runtimes/python.exe"),
        "backup": put(root, ".backups/old/media.bin"),
    }
    session.close()
    session = ProjectSession.open(root, repository_factory=ProjectRepository)
    jobs, index, store, _ = composition(session)  # Actual interrupted queue recovery.
    service = ProjectStorage(session.repository, clock=lambda: NOW)
    yield session, service, store, (active, historical, retained, pinned, orphan, input_artifact), paths
    session.close()


def test_dry_run_graph_categories_grace_and_no_mutation(storage):
    session, service, store, manifests, paths = storage
    root = session.repository.workspace
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = service.analyze()
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after
    by_artifact = {item.artifact_id: item for item in report.files if item.artifact_id}
    active, historical, retained, pinned, orphan, job_input = manifests
    assert by_artifact[active.artifact_id].category == ACTIVE
    assert by_artifact[historical.artifact_id].category == HISTORY
    assert by_artifact[pinned.artifact_id].category == PINNED
    assert by_artifact[job_input.artifact_id].category == JOBS
    assert by_artifact[orphan.artifact_id].eligible
    by_path = {item.path: item for item in report.files}
    assert by_path[paths["complete"].relative_to(root).as_posix()].category == TEMP
    assert by_path[paths["interrupted"].relative_to(root).as_posix()].category == JOBS
    assert by_path[paths["cache"].relative_to(root).as_posix()].category == CACHE
    # Cache is protected while an interrupted job could still consume it.
    assert not by_path[paths["cache"].relative_to(root).as_posix()].eligible
    assert not by_path[paths["recent"].relative_to(root).as_posix()].eligible
    assert report.total_bytes == sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    assert not service.pending_runs()


def test_actual_cleanup_keeps_all_references_and_accounts_actual_disk(storage):
    session, service, store, manifests, paths = storage
    active, historical, retained, pinned, orphan, job_input = manifests
    report = service.cleanup(service.analyze())
    assert not paths["orphan"].exists() and not paths["complete"].exists()
    assert not store._artifact_path(orphan.storage_key).exists()
    assert orphan.artifact_id not in {m.artifact_id for m in store.list_artifacts()}
    for manifest in (active, historical, retained, pinned, job_input):
        assert store._artifact_path(manifest.storage_key).is_file()
    for name in ("recent", "cache", "interrupted", "model", "runtime", "backup"):
        assert paths[name].is_file()
    root = session.repository.workspace
    assert report.total_bytes == sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    assert not report.candidates
    assert not service.pending_runs()
    assert not service.cleanup(report).candidates  # Second cleanup is idempotent.


@pytest.mark.parametrize("stage", ["intent_committed", "file_removed", "catalog_committed", "item_committed"])
def test_interrupted_cleanup_restart_and_explicit_resume(storage, stage):
    session, service, store, manifests, paths = storage
    root = session.repository.workspace
    triggered = False
    def fail(point):
        nonlocal triggered
        if point == stage and not triggered:
            triggered = True
            raise RuntimeError("injected interruption")
    service.checkpoint = fail
    with pytest.raises(RuntimeError, match="interruption"):
        service.cleanup(service.analyze())
    assert service.pending_runs()
    session.close()
    with ProjectSession.open(root, repository_factory=ProjectRepository) as reopened:
        recovered = ProjectStorage(reopened.repository, clock=lambda: NOW)
        pending = recovered.pending_runs()
        assert pending  # Opening a project does not automatically delete anything.
        for run in pending:
            recovered.resume_cleanup(run)
        report = recovered.analyze()
        assert not recovered.pending_runs() and not report.candidates
        assert paths["interrupted"].exists()
        assert report.total_bytes == sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def test_changed_preview_and_new_pin_reject_deletion(storage):
    _, service, _, manifests, paths = storage
    preview = service.analyze()
    service.pin(manifests[4].artifact_id)
    with pytest.raises(ValueError, match="Storage changed"):
        service.cleanup(preview)
    assert paths["orphan"].exists()
    preview = service.analyze()
    paths["orphan"].write_bytes(b"changed since dry run")
    with pytest.raises(ValueError, match="Storage changed"):
        service.cleanup(preview)
    assert not service.pending_runs()


def test_new_pin_after_quarantine_restores_bytes_instead_of_deleting(storage):
    session, service, store, manifests, _ = storage
    orphan = manifests[4]
    def fail(stage):
        if stage == "file_removed" and not store._artifact_path(orphan.storage_key).exists():
            raise RuntimeError("interrupted before catalog commit")
    service.checkpoint = fail
    with pytest.raises(RuntimeError):
        service.cleanup(service.analyze())
    service.checkpoint = lambda stage: None
    service.pin(orphan.artifact_id)
    for run in service.pending_runs():
        service.resume_cleanup(run)
    assert store.read_artifact(orphan.storage_key) == b"orphan"
    assert not service.pending_runs()


def test_preview_cache_can_be_removed_without_active_jobs(tmp_path):
    with ProjectSession.create(tmp_path, name="Cache", repository_factory=ProjectRepository) as session:
        path = put(tmp_path, "cache/previews/proxy.mp4", days=0)
        shared = put(tmp_path, "cache/models/shared.bin")
        service = ProjectStorage(session.repository, clock=lambda: NOW)
        report = service.analyze()
        assert [item.path for item in report.candidates] == ["cache/previews/proxy.mp4"]
        service.cleanup(report)
        assert not path.exists() and shared.exists()


def test_retained_variant_never_collectable_even_with_disposable_hint(storage):
    _, service, store, _, _ = storage
    variant = store.save_artifact("retained.bin", b"image variant", {"artifact_type": "scene_image", "disposable": True})
    old(store._artifact_path(variant.storage_key))
    report = service.analyze()
    assert next(item for item in report.files if item.artifact_id == variant.artifact_id).category == HISTORY
    assert variant.artifact_id not in {item.artifact_id for item in report.candidates}


def test_storage_dialog_is_dry_until_explicit_click(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from app.desktop.storage_dialog import StorageDialog
    qt = QApplication.instance() or QApplication([])
    with ProjectSession.create(tmp_path, name="UI", repository_factory=ProjectRepository) as session:
        path = put(tmp_path, "cache/previews/proxy.mp4")
        service = ProjectStorage(session.repository, clock=lambda: NOW)
        dialog = StorageDialog(service)
        assert path.exists() and "Will remove" in dialog.preview.toPlainText()
        dialog.delete_button.click()
        assert not path.exists() and "Cleanup complete" in dialog.status.text()
        dialog.close()

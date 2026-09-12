"""Offline queue state, ownership, persistence and crash behavior under D003."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from app.application.invalidation import Freshness, evaluate_freshness
from app.application.projects import ProjectSession
from app.domain.base import DomainValidationError
from app.domain.dependencies import DependencyDeclaration, InputEdge, RequestFingerprint, content_fingerprint
from app.domain.generation_job import AttemptStatus, JobProgress
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobConflictError, JobQueueBusyError, JobRepository
from app.storage.artifact_index import ProjectArtifactIndex
from app.storage.dependency_index import ArtifactDependencyIndex
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository, UnsupportedSchemaError


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 12, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self):
        self.now += timedelta(seconds=1)
        return self.now


@contextmanager
def open_queue(path, clock, *, create=False):
    session = (ProjectSession.create(path, repository_factory=ProjectRepository, name="Queue") if create
               else ProjectSession.open(path, repository_factory=ProjectRepository))
    with session:
        repository = JobRepository(session.repository, clock=clock)
        yield session, repository, JobCoordinator(repository, clock=clock)


@pytest.fixture
def queue(tmp_path):
    clock = Clock()
    with open_queue(tmp_path, clock, create=True) as (session, repository, coordinator):
        yield session, repository, coordinator, clock


def enqueue(coordinator, text="B1"):
    request = RequestFingerprint.create("raw-tts", "1", inputs=[InputEdge("text", "B:text", content_fingerprint(text))])
    return coordinator.enqueue("B:raw", request, {"text": text, "section_revision_id": "B1"})


def test_fake_executor_receives_frozen_inputs_and_persists_progress_and_result(queue):
    session, repository, coordinator, clock = queue
    inputs = {"text": "B1", "nested": {"revision": session.active_script.id}}
    request = RequestFingerprint.create("fake", "1")
    queued = coordinator.enqueue("B:raw", request, inputs)
    inputs["text"], inputs["nested"]["revision"] = "B2", "new"
    clock.advance()
    claim = coordinator.claim_next("fake-executor")
    assert claim.id == queued.id and claim.number == 1
    assert claim.status == AttemptStatus.RUNNING and claim.claim_token
    captured = repository.get_job(claim.job_id)
    assert json.loads(captured.input_snapshot_json) == {"text": "B1", "nested": {"revision": session.active_script.id}}
    assert captured.request == request
    clock.advance()
    loading = coordinator.progress(claim, JobProgress("loading"))
    assert loading.progress.completed is None
    clock.advance()
    progress = coordinator.progress(claim, JobProgress("chunks", 1, 2))
    assert progress.updated_at == clock.now
    with pytest.raises(JobConflictError, match="regress"):
        coordinator.progress(claim, JobProgress("chunks", 0, 2))
    clock.advance()
    completed = coordinator.complete(claim, ["reported-result"])
    assert completed.status == AttemptStatus.COMPLETED
    assert completed.output_artifact_ids == ("reported-result",)
    assert completed.finished_at == clock.now
    assert completed.started_at == claim.started_at
    assert repository.get_attempt(claim.id) == completed
    assert coordinator.claim_next("other") is None


def test_two_adapters_and_sqlite_write_contention_cannot_claim_same_attempt(queue):
    session, repository, coordinator, clock = queue
    queued = enqueue(coordinator)
    other = JobCoordinator(JobRepository(session.repository, clock=clock), clock=clock)
    # Real independent SQLite writer holds RESERVED lock while a contender claims.
    connection = sqlite3.connect(repository.path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(JobQueueBusyError):
            other.claim_next("contender")
        assert repository.get_attempt(queued.id).status == AttemptStatus.QUEUED
    finally:
        connection.rollback()
        connection.close()
    first = coordinator.claim_next("first")
    assert first.id == queued.id
    assert other.claim_next("second") is None
    assert repository.get_attempt(queued.id).owner == "first"
    # Constructing an adapter is not a restart of the owning project session.
    third = JobRepository(session.repository, clock=clock)
    assert third.recovered_attempt_ids == ()
    assert third.get_attempt(first.id) == first


def test_pause_blocks_only_new_claims_and_cancel_requires_safe_acknowledgement(queue):
    _, repository, coordinator, clock = queue
    a, b, c = [enqueue(coordinator, text) for text in ("A", "B", "C")]
    active = coordinator.claim_next("worker")
    coordinator.pause()
    assert repository.paused and coordinator.claim_next("second") is None
    assert coordinator.cancel(b.id).status == AttemptStatus.CANCELED
    assert coordinator.progress(active, JobProgress("chunks", 1, 2)).status == AttemptStatus.RUNNING
    clock.advance()
    pending_cancel = coordinator.cancel(a.id)
    assert pending_cancel.status == AttemptStatus.RUNNING and pending_cancel.cancel_requested
    assert pending_cancel.finished_at is None
    with pytest.raises(JobConflictError, match="completion"):
        coordinator.complete(active, ["late-result"])
    clock.advance()
    canceled = coordinator.acknowledge_cancel(active)
    assert canceled.status == AttemptStatus.CANCELED and canceled.finished_at == clock.now
    assert coordinator.cancel(a.id) == canceled  # Idempotent; history is not retimestamped.
    coordinator.resume()
    assert coordinator.claim_next("worker").id == c.id


@pytest.mark.parametrize("finish", ["completed", "failed", "canceled"])
def test_terminal_attempts_reject_duplicate_or_late_events(queue, finish):
    _, repository, coordinator, _ = queue
    queued = enqueue(coordinator)
    with pytest.raises(JobConflictError):
        coordinator.complete(queued)
    claim = coordinator.claim_next("worker")
    with pytest.raises(JobConflictError):
        coordinator.acknowledge_cancel(claim)
    impostor = replace(claim, claim_token="wrong-token")
    for action in (lambda: coordinator.progress(impostor, JobProgress("load")),
                   lambda: coordinator.fail(impostor, "fake"), lambda: coordinator.complete(impostor)):
        with pytest.raises(JobConflictError):
            action()
    if finish == "completed":
        outcome = coordinator.complete(claim)
    elif finish == "failed":
        outcome = coordinator.fail(claim, "offline fake failure")
    else:
        coordinator.cancel(claim.id)
        outcome = coordinator.acknowledge_cancel(claim)
    for action in (lambda: coordinator.progress(claim, JobProgress("late")),
                   lambda: coordinator.fail(claim, "late"), lambda: coordinator.complete(claim)):
        with pytest.raises(JobConflictError):
            action()
    assert repository.get_attempt(claim.id) == outcome


def test_explicit_retry_creates_new_attempt_and_keeps_failure_and_exact_request(queue):
    _, repository, coordinator, clock = queue
    queued = enqueue(coordinator)
    original = repository.get_job(queued.job_id)
    with pytest.raises(JobConflictError):
        coordinator.retry(queued.job_id)
    first = coordinator.claim_next("worker")
    with pytest.raises(JobConflictError):
        coordinator.retry(queued.job_id)
    failure = coordinator.fail(first, "OOM fixture")
    clock.advance()
    retry = coordinator.retry(first.job_id)
    assert retry.id != first.id and retry.number == 2 and retry.status == AttemptStatus.QUEUED
    assert repository.get_job(first.job_id) == original
    assert repository.attempts(first.job_id) == (failure, retry)
    with pytest.raises(JobConflictError):
        coordinator.retry(first.job_id)
    claimed_retry = coordinator.claim_next("worker")
    assert claimed_retry.claim_token != first.claim_token
    with pytest.raises(JobConflictError):
        coordinator.complete(first, ["late"])
    coordinator.complete(claimed_retry)
    with pytest.raises(JobConflictError):
        coordinator.retry(first.job_id)


def test_enqueue_rolls_back_request_and_attempt_together(queue, monkeypatch):
    _, repository, coordinator, _ = queue
    original = enqueue(coordinator)
    job = repository.get_job(original.job_id)
    with pytest.raises(JobConflictError):
        repository.enqueue(job)
    def fail_insert(*args):
        raise sqlite3.IntegrityError("injected attempt insert failure")
    with monkeypatch.context() as patch:
        patch.setattr(repository, "_enqueue_attempt", fail_insert)
        with pytest.raises(sqlite3.IntegrityError):
            enqueue(coordinator, "B2")
    assert repository.jobs() == (job,)
    assert repository.attempts(job.id) == (original,)
    # Same effective request can intentionally create a distinct job/variant.
    variant = enqueue(coordinator)
    assert variant.job_id != original.job_id
    assert repository.get_job(variant.job_id).request == job.request


def test_invalid_command_values_leave_running_attempt_unchanged(queue):
    _, repository, coordinator, clock = queue
    enqueue(coordinator)
    claim = coordinator.claim_next("worker")
    for action in (lambda: coordinator.fail(claim, ""),
                   lambda: coordinator.complete(claim, ["same", "same"]),
                   lambda: coordinator.complete(claim, "not-a-list"),
                   lambda: repository.finish(claim.id, claim.claim_token, AttemptStatus.FAILED, clock.now,
                                             output_artifact_ids=["result"], error="failed"),
                   lambda: repository.set_paused("false"),
                   lambda: repository.claim_next("worker", datetime(2026, 1, 1))):
        with pytest.raises(DomainValidationError):
            action()
    assert repository.get_attempt(claim.id) == claim
    assert not repository.paused


def test_restart_retains_fifo_pause_inputs_and_interrupts_only_abandoned_attempts(tmp_path):
    clock = Clock()
    with open_queue(tmp_path, clock, create=True) as (_, repository, coordinator):
        a, b, c = [enqueue(coordinator, text) for text in ("A", "B", "C")]
        active = coordinator.claim_next("worker")
        coordinator.progress(active, JobProgress("chunks", 1, 3))
        coordinator.cancel(active.id)  # Request is not proof that computation stopped.
        coordinator.pause()
        requests = repository.jobs()
        old_coordinator = coordinator
    clock.advance()
    with open_queue(tmp_path, clock) as (_, repository, coordinator):
        assert repository.recovered_attempt_ids == (a.id,)
        abandoned = repository.get_attempt(a.id)
        assert abandoned.status == AttemptStatus.INTERRUPTED
        assert abandoned.progress == JobProgress("chunks", 1, 3)
        assert abandoned.cancel_requested and abandoned.finished_at == clock.now
        assert abandoned.output_artifact_ids == ()
        assert repository.jobs() == requests
        assert repository.paused and coordinator.claim_next("worker") is None
        with pytest.raises(sqlite3.ProgrammingError):
            old_coordinator.complete(active)
        with pytest.raises(JobConflictError):
            coordinator.complete(active)
        coordinator.resume()
        assert coordinator.claim_next("worker").id == b.id
        assert coordinator.claim_next("worker").id == c.id
        assert coordinator.retry(a.job_id).number == 2


def test_failed_attempt_preserves_actual_prior_artifact_and_d005_freshness_after_reopen(tmp_path):
    clock = Clock()
    with open_queue(tmp_path, clock, create=True) as (session, repository, coordinator):
        request = RequestFingerprint.create("raw-tts", "1", inputs=[InputEdge("text", "B:text", content_fingerprint("B1"))])
        store = LocalArtifactStore.for_project(session.repository)
        artifact = store.save_artifact("B.wav", b"old validated fixture", DependencyDeclaration("B:raw", request).to_metadata())
        queued = coordinator.enqueue("B:raw", request, {"text": "B1"}, prior_artifact_ids=[artifact.artifact_id])
        claim = coordinator.claim_next("fake-executor")
        failure = coordinator.fail(claim, "fake synthesis failed")
        failed_request = repository.get_job(queued.job_id)
    with open_queue(tmp_path, clock) as (session, repository, _):
        assert repository.get_attempt(failure.id) == failure
        assert repository.get_job(queued.job_id) == failed_request
        assert failed_request.prior_artifact_ids == (artifact.artifact_id,)
        result = evaluate_freshness(requests={"B:raw": request}, sources={"B:text": content_fingerprint("B1")},
                                    selected={"B:raw": artifact.artifact_id},
                                    artifacts=ArtifactDependencyIndex(ProjectArtifactIndex(session.repository)).records(),
                                    failed_attempts=[failure.failure(failed_request)])
        assert result["B:raw"].state == Freshness.FRESH
        assert result["B:raw"].failed_attempts[0].error == "fake synthesis failed"
        assert LocalArtifactStore.for_project(session.repository).read_artifact(artifact.storage_key) == b"old validated fixture"


def run_child(code, *args):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    return subprocess.run([sys.executable, "-c", code, *map(str, args)], capture_output=True, text=True, env=env, timeout=20)


@pytest.mark.parametrize("boundary, expected", [
    ("before-claim-commit", AttemptStatus.QUEUED),
    ("after-claim-commit", AttemptStatus.INTERRUPTED),
    ("before-complete-commit", AttemptStatus.INTERRUPTED),
    ("after-complete-commit", AttemptStatus.COMPLETED),
])
def test_process_crash_recovers_only_committed_transitions(tmp_path, boundary, expected):
    clock = Clock()
    with open_queue(tmp_path, clock, create=True) as (_, _, coordinator):
        queued = enqueue(coordinator)
    child = run_child("""
from contextlib import contextmanager
from datetime import UTC, datetime
import os, sys
from app.application.projects import ProjectSession
from app.storage.project_repository import ProjectRepository
from app.jobs.repository import JobRepository
from app.jobs.coordinator import JobCoordinator
class CrashQueue(JobRepository):
    crash = False
    @contextmanager
    def _transaction(self):
        with super()._transaction() as connection:
            yield connection
            if self.crash:
                os._exit(31)
clock = lambda: datetime(2026, 9, 12, 0, 0, 1, tzinfo=UTC)
with ProjectSession.open(sys.argv[1], repository_factory=ProjectRepository) as session:
    queue = CrashQueue(session.repository, clock=clock)
    coordinator = JobCoordinator(queue, clock=clock)
    queue.crash = sys.argv[2] == 'before-claim-commit'
    claim = coordinator.claim_next('child')
    if sys.argv[2] == 'after-claim-commit':
        os._exit(31)
    queue.crash = sys.argv[2] == 'before-complete-commit'
    coordinator.complete(claim, ['reported-result'])
    os._exit(31)
""", tmp_path, boundary)
    assert child.returncode == 31, child.stderr
    clock.advance()
    with open_queue(tmp_path, clock) as (_, repository, coordinator):
        recovered = repository.get_attempt(queued.id)
        assert recovered.status == expected
        assert recovered.output_artifact_ids == (("reported-result",) if expected == AttemptStatus.COMPLETED else ())
        if expected == AttemptStatus.QUEUED:
            assert coordinator.claim_next("next-session").id == queued.id
    # Recovery is durable, not a fresh interruption timestamp on every open.
    if expected != AttemptStatus.QUEUED:
        clock.advance()
        with open_queue(tmp_path, clock) as (_, repository, _):
            assert repository.get_attempt(queued.id) == recovered
            assert repository.recovered_attempt_ids == ()


@pytest.mark.parametrize("corruption", ["version", "application", "owner", "empty"])
def test_queue_refuses_unknown_or_foreign_format_without_repair(tmp_path, corruption):
    clock = Clock()
    with open_queue(tmp_path, clock, create=True) as (_, repository, _):
        path = repository.path
    if corruption == "empty":
        path.write_bytes(b"")
    else:
        with sqlite3.connect(path) as connection:
            connection.execute({"version": "PRAGMA user_version = 99", "application": "PRAGMA application_id = 0",
                                "owner": "UPDATE owner SET project_id = 'foreign'"}[corruption])
    before = path.read_bytes()
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as session:
        with pytest.raises((UnsupportedSchemaError, JobConflictError)):
            JobRepository(session.repository, clock=clock)
    assert path.read_bytes() == before


def test_queue_moves_with_closed_project_and_keeps_d003_schema(tmp_path):
    clock = Clock()
    origin, moved = tmp_path / "original", tmp_path / "Żółty projekt"
    with open_queue(origin, clock, create=True) as (_, repository, coordinator):
        queued = enqueue(coordinator)
        original = repository.get_job(queued.job_id)
    shutil.move(str(origin), str(moved))
    with open_queue(moved, clock) as (_, repository, coordinator):
        assert repository.get_job(queued.job_id) == original
        assert coordinator.claim_next("worker").id == queued.id
    with sqlite3.connect(moved / "project.sqlite") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    with sqlite3.connect(moved / "jobs.sqlite") as connection:
        assert str(origin) not in "\n".join(connection.iterdump())


@pytest.mark.parametrize("progress", [("", None, None), ("chunks", True, 2), ("chunks", -1, 2),
                                     ("chunks", 3, 2), ("chunks", None, 2)])
def test_invalid_progress_is_rejected(progress):
    with pytest.raises(DomainValidationError):
        JobProgress(*progress)


def test_core_import_has_no_storage_gui_or_provider_dependencies():
    child = run_child("""
import sys
from app.jobs.coordinator import JobCoordinator
for name in ('sqlite3', 'app.storage', 'PySide6', 'fastapi', 'torch', 'app.providers'):
    assert name not in sys.modules, name
""")
    assert child.returncode == 0, child.stderr

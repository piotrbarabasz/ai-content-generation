import asyncio
from dataclasses import replace
import os
from pathlib import Path
import sys
import subprocess

import pytest

from app.application.projects import ProjectSession
from app.domain.dependencies import RequestFingerprint
from app.domain.generation_job import AttemptStatus
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.runtime.supervisor import WorkerLaunch, WorkerLimits, WorkerSupervisor
from app.storage.project_repository import ProjectRepository


BACKEND = Path(__file__).resolve().parents[2]
PYTHON = Path(getattr(sys, "_base_executable", sys.executable))
FIXTURE = BACKEND / "tests/fixtures/worker_process.py"


@pytest.fixture
def setup(tmp_path):
    with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Worker") as session:
        repository = JobRepository(session.repository)
        coordinator = JobCoordinator(repository)
        queued = coordinator.enqueue("B:raw", RequestFingerprint.create("diagnostic.echo", "1"), {"text": "B1"},
                                     prior_artifact_ids=["retained_artifact"])
        yield coordinator, repository, queued


def supervisor(coordinator, case, **limits):
    policy = replace(WorkerLimits(handshake=3, execution=6, exit_grace=0.3, cancel_grace=0.2, reap=3), **limits)
    return WorkerSupervisor(coordinator, WorkerLaunch(PYTHON, FIXTURE, {"WORKER_CASE": case}), limits=policy)


@pytest.mark.parametrize("case,expected", [
    ("success", AttemptStatus.COMPLETED), ("fragmented", AttemptStatus.COMPLETED),
    ("failure", AttemptStatus.FAILED), ("version", AttemptStatus.FAILED),
    ("malformed", AttemptStatus.FAILED), ("truncated", AttemptStatus.FAILED),
    ("oversized", AttemptStatus.FAILED), ("wrong-id", AttemptStatus.FAILED),
    ("wrong-pid", AttemptStatus.FAILED), ("stdout-log", AttemptStatus.FAILED),
    ("nonzero", AttemptStatus.FAILED), ("extra", AttemptStatus.FAILED),
    ("no-outcome", AttemptStatus.FAILED), ("terminal-hang", AttemptStatus.FAILED),
    ("regression", AttemptStatus.FAILED), ("unsolicited-cancel", AttemptStatus.FAILED),
])
def test_real_worker_outcomes_preserve_truthful_queue_state_and_reap_child(setup, case, expected):
    coordinator, repository, queued = setup
    worker = supervisor(coordinator, case)
    result = asyncio.run(worker.run_next())
    assert result.attempt.status == expected, result
    assert result.pid and result.returncode is not None
    assert worker.process.returncode is not None
    assert repository.get_attempt(queued.id) == result.attempt
    assert repository.get_job(queued.job_id).prior_artifact_ids == ("retained_artifact",)
    assert result.attempt.output_artifact_ids == (("reported_artifact",) if expected == AttemptStatus.COMPLETED else ())
    if expected == AttemptStatus.FAILED:
        assert result.attempt.error


@pytest.mark.parametrize("case", ["hang", "no-handshake", "flood"])
def test_timeout_keeps_event_loop_responsive_and_stops_worker(setup, case):
    coordinator, _, _ = setup
    worker = supervisor(coordinator, case, execution=0.8, handshake=0.5)
    async def run():
        ticks = 0
        task = asyncio.create_task(worker.run_next())
        while not task.done():
            ticks += 1
            await asyncio.sleep(0.01)
        return await task, ticks
    result, ticks = asyncio.run(run())
    assert result.attempt.status == AttemptStatus.FAILED
    assert ticks >= 5
    assert result.returncode is not None


@pytest.mark.parametrize("case", ["cancel", "ignore-cancel", "late-complete"])
def test_cooperative_and_forced_cancel_never_select_late_success(setup, case):
    coordinator, repository, queued = setup
    worker = supervisor(coordinator, case)
    async def run():
        task = asyncio.create_task(worker.run_next())
        while repository.get_attempt(queued.id).progress is None:
            assert not task.done()
            await asyncio.sleep(0.01)
        # Also supports cancellations issued directly through the D006 coordinator.
        coordinator.cancel(queued.id)
        assert repository.get_attempt(queued.id).status == AttemptStatus.RUNNING
        return await task
    result = asyncio.run(run())
    assert result.attempt.status == AttemptStatus.CANCELED
    assert result.attempt.output_artifact_ids == () and result.returncode is not None
    if case in ("cancel", "late-complete"):
        assert result.returncode == 0


def test_stderr_is_drained_and_retained_as_a_bounded_tail(setup):
    coordinator, _, _ = setup
    worker = supervisor(coordinator, "stderr-flood", stderr_bytes=1024)
    result = asyncio.run(worker.run_next())
    assert result.attempt.status == AttemptStatus.COMPLETED
    assert len(result.stderr_tail) == 1024 and result.stderr_tail.endswith("LOG-END")


@pytest.mark.parametrize("case,code,phase", [
    ("no-handshake", "WORKER_HANDSHAKE_TIMEOUT", "HANDSHAKE"),
    ("initialization-hang", "WORKER_HANDSHAKE_TIMEOUT", "RUNTIME_INITIALIZATION"),
    ("hang", "WORKER_EXECUTION_TIMEOUT", "SYNTHESIS"),
    ("terminal-hang", "WORKER_TERMINAL_EOF_TIMEOUT", "STDOUT_EOF"),
    ("exit-hang", "WORKER_EXIT_TIMEOUT", "PROCESS_EXIT"),
])
def test_deadline_identifies_exact_wait_and_reaps_before_retry(setup, case, code, phase, caplog):
    caplog.set_level("INFO", logger="aics.pipeline")
    coordinator, repository, queued = setup
    worker = supervisor(coordinator, case, handshake=.5, execution=.5, exit_grace=.15)
    result = asyncio.run(worker.run_next())
    assert result.attempt.error.startswith(code + ":")
    assert "phase=" + phase in result.attempt.error
    assert result.retryable and result.cleanup_confirmed and result.returncode is not None
    assert phase in result.phase_durations and "CLEANUP" in result.phase_durations
    assert "B1" not in caplog.text
    original = repository.get_job(queued.job_id)
    retry = worker.retry_transient(result)
    assert retry.job_id == original.id and retry.number == 2
    worker.launch = replace(worker.launch, environment={"WORKER_CASE": "success"})
    success = asyncio.run(worker.run_next())
    assert success.attempt.status == AttemptStatus.COMPLETED
    assert not success.retryable and success.pid != result.pid
    assert repository.get_job(queued.job_id) == original


@pytest.mark.parametrize("case", ["delayed-eof", "delayed-exit"])
def test_terminal_waits_within_grace_do_not_lose_valid_completion(setup, case):
    coordinator, _, _ = setup
    result = asyncio.run(supervisor(coordinator, case).run_next())
    assert result.attempt.status == AttemptStatus.COMPLETED
    assert result.cleanup_confirmed and result.returncode == 0
    assert {"TERMINAL_MESSAGE", "STDOUT_EOF", "PROCESS_EXIT", "CLEANUP", "PUBLICATION"} <= result.phase_durations.keys()


def test_production_quiet_worker_closes_protocol_before_interpreter_teardown(setup):
    coordinator, _, _ = setup
    worker = WorkerSupervisor(coordinator, WorkerLaunch(PYTHON, BACKEND / "tests/fixtures/quiet_exit_worker.py"),
                              limits=WorkerLimits(handshake=3, execution=3, exit_grace=.15, reap=3))
    result = asyncio.run(worker.run_next())
    assert result.attempt.error.startswith("WORKER_EXIT_TIMEOUT:")
    assert result.phase_durations["STDOUT_EOF"] < .15
    assert "RUNTIME_INITIALIZATION" in result.phase_durations


def test_permanent_terminal_failure_never_becomes_retryable_due_to_slow_shutdown(setup):
    coordinator, _, _ = setup
    result = asyncio.run(supervisor(coordinator, "failure-terminal-hang").run_next())
    assert result.attempt.error.startswith("WORKER_TERMINAL_EOF_TIMEOUT:")
    assert result.terminal_outcome == "failed" and not result.retryable


@pytest.mark.parametrize("method", ["close", "task-cancel"])
def test_application_shutdown_awaits_child_cleanup(setup, method):
    coordinator, repository, queued = setup
    worker = supervisor(coordinator, "ignore-cancel")
    async def run():
        task = asyncio.create_task(worker.run_next())
        while repository.get_attempt(queued.id).progress is None:
            assert not task.done()
            await asyncio.sleep(0.01)
        if method == "close":
            await worker.close()
            await task
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert worker.process.returncode is not None
    asyncio.run(run())
    assert repository.get_attempt(queued.id).status == AttemptStatus.CANCELED


def test_paused_queue_never_launches_and_missing_executable_fails_claim(setup, tmp_path):
    coordinator, repository, queued = setup
    worker = WorkerSupervisor(coordinator, WorkerLaunch(tmp_path / "missing.exe"))
    coordinator.pause()
    assert asyncio.run(worker.run_next()) is None
    assert worker.process is None and repository.get_attempt(queued.id).status == AttemptStatus.QUEUED
    coordinator.resume()
    result = asyncio.run(worker.run_next())
    assert result.attempt.status == AttemptStatus.FAILED and result.pid is None


def test_repeated_task_cancellation_cannot_interrupt_process_cleanup(setup, monkeypatch):
    coordinator, repository, queued = setup
    worker = supervisor(coordinator, "ignore-cancel")
    original_stop = worker._stop
    async def run():
        entered_cleanup = asyncio.Event()
        async def delayed_stop():
            entered_cleanup.set()
            await asyncio.sleep(0.03)
            await original_stop()
        monkeypatch.setattr(worker, "_stop", delayed_stop)
        task = asyncio.create_task(worker.run_next())
        while repository.get_attempt(queued.id).progress is None:
            assert not task.done()
            await asyncio.sleep(0.01)
        task.cancel()
        await entered_cleanup.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert worker.process.returncode is not None
    asyncio.run(run())
    assert repository.get_attempt(queued.id).status == AttemptStatus.CANCELED


def test_real_diagnostic_entry_imports_no_models_and_exits(setup):
    coordinator, _, _ = setup
    worker = WorkerSupervisor(coordinator, WorkerLaunch(PYTHON, BACKEND / "app/runtime/entry.py"))
    result = asyncio.run(worker.run_next())
    assert result.attempt.status == AttemptStatus.COMPLETED, result
    assert result.returncode == 0 and result.attempt.output_artifact_ids == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows process containment")
def test_windows_job_reaps_inherited_child_process(setup):
    import ctypes
    from ctypes import wintypes
    coordinator, _, _ = setup
    worker = supervisor(coordinator, "tree", execution=1)
    result = asyncio.run(worker.run_next())
    assert result.attempt.status == AttemptStatus.FAILED
    pid = int(result.stderr_tail.split("grandchild=")[1].splitlines()[0])
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only.
    if handle:
        try:
            assert api.WaitForSingleObject(handle, 3000) == 0
        finally:
            api.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows kill-on-parent-exit containment")
@pytest.mark.parametrize("managed_gpu", [False, True])
def test_parent_process_crash_kills_worker_and_queue_recovers_interrupted(tmp_path, managed_gpu):
    from app.runtime.resources import DeviceDecision
    decision = DeviceDecision("fake-profile", "cuda:0", "cuda:0", ("cuda:0",))
    with ProjectSession.create(tmp_path, repository_factory=ProjectRepository, name="Parent crash") as session:
        coordinator = JobCoordinator(JobRepository(session.repository))
        request = RequestFingerprint.create("diagnostic.echo", "1")
        queued = coordinator.enqueue("B:raw", decision.bind(request) if managed_gpu else request, {"text": "B1"})
    code = """
import asyncio, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app.application.projects import ProjectSession
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.runtime.supervisor import WorkerLaunch, WorkerSupervisor
from app.runtime.resources import DeviceDecision, APPLICATION_GPU_RESOURCES
from app.storage.project_repository import ProjectRepository
with ProjectSession.open(sys.argv[2], repository_factory=ProjectRepository) as session:
    repository = JobRepository(session.repository)
    decision = DeviceDecision('fake-profile', 'cuda:0', 'cuda:0', ('cuda:0',)) if sys.argv[5] == 'gpu' else None
    worker = WorkerSupervisor(JobCoordinator(repository), WorkerLaunch(Path(sys.executable), Path(sys.argv[3]), {'WORKER_CASE': 'hang'}), device=decision)
    async def run():
        task = asyncio.create_task(worker.run_next())
        while repository.get_attempt(sys.argv[4]).progress is None:
            assert not task.done()
            await asyncio.sleep(0.01)
        print(worker.process.pid, flush=True)
        if decision is not None:
            assert not APPLICATION_GPU_RESOURCES.availability().available
        os._exit(23)
    asyncio.run(run())
"""
    result = subprocess.run([str(PYTHON), "-I", "-c", code, str(BACKEND), str(tmp_path), str(FIXTURE), queued.id,
                             "gpu" if managed_gpu else "cpu"],
                            capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 23, result.stderr
    pid = int(result.stdout.strip())
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x00100000, False, pid)
    if handle:
        try:
            assert api.WaitForSingleObject(handle, 3000) == 0
        finally:
            api.CloseHandle(handle)
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as session:
        assert JobRepository(session.repository).get_attempt(queued.id).status == AttemptStatus.INTERRUPTED
        if managed_gpu:
            coordinator = JobCoordinator(JobRepository(session.repository))
            coordinator.retry(queued.job_id)
            restarted = WorkerSupervisor(coordinator, WorkerLaunch(PYTHON, FIXTURE, {"WORKER_CASE": "success"}),
                                         device=decision)
            assert asyncio.run(restarted.run_next()).attempt.status == AttemptStatus.COMPLETED

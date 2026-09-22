"""Fake GPU decisions around real D007 children and durable project queues."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

from app.application.projects import ProjectSession
from app.domain.base import new_id, utc_now
from app.domain.dependencies import RequestFingerprint, canonical_json
from app.domain.generation_job import AttemptStatus, JobRequest
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.runtime.resources import APPLICATION_GPU_RESOURCES, DeviceDecision, GPUResourceManager
from app.runtime.section_preview import _OneJobCoordinator
from app.runtime.supervisor import WorkerLaunch, WorkerLimits, WorkerSupervisor
from app.storage.project_repository import ProjectRepository


PYTHON = Path(getattr(sys, "_base_executable", sys.executable))
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/worker_process.py"
DEVICE = DeviceDecision("tested-fake-profile", "cuda:0", "cuda:0", ("cuda:0", "cpu"))
LIMITS = WorkerLimits(handshake=3, execution=5, cancel_grace=.2, exit_grace=.3, reap=3)


def worker(coordinator, case, resources=None, device=DEVICE, **kwargs):
    return WorkerSupervisor(coordinator, WorkerLaunch(PYTHON, FIXTURE, {"WORKER_CASE": case}),
                            limits=LIMITS, device=device, resources=resources, **kwargs)


def enqueue(coordinator, device=DEVICE):
    request = RequestFingerprint.create("diagnostic.echo", "1")
    if device:
        request = device.bind(request)
    return coordinator.enqueue("scene:raw", request, {"text": "B1"})


@pytest.fixture
def project(tmp_path):
    with ProjectSession.create(tmp_path / "one", name="GPU", repository_factory=ProjectRepository) as session:
        coordinator = JobCoordinator(JobRepository(session.repository))
        yield coordinator


async def running(supervisor, task):
    for _ in range(500):
        assert not task.done(), task.result() if task.done() else None
        if supervisor._started is not None and supervisor._started.is_set():
            return
        await asyncio.sleep(.01)
    raise AssertionError("Worker did not start")


def test_two_projects_and_preview_share_default_manager_without_claiming_busy_work(project, tmp_path):
    first = worker(project, "ignore-cancel")
    first_job = enqueue(project)
    with ProjectSession.create(tmp_path / "two", name="Other", repository_factory=ProjectRepository) as other:
        second_coordinator = JobCoordinator(JobRepository(other.repository))
        second_job = enqueue(second_coordinator)
        second = worker(second_coordinator, "success")
        preview_request = JobRequest(new_id("job"), "preview:audio",
                                    DEVICE.bind(RequestFingerprint.create("diagnostic.echo", "1")),
                                    canonical_json({"text": "B1"}), utc_now())
        preview = worker(_OneJobCoordinator(preview_request), "preview-success")

        async def run():
            task = asyncio.create_task(first.run_next("project-one"))
            try:
                await running(first, task)
                state = APPLICATION_GPU_RESOURCES.availability()
                assert not state.available and state.worker_pid == first.process.pid
                assert await second.run_next("project-two") is None
                assert await preview.run_next("voice-preview") is None
                assert second_coordinator.repository.get_attempt(second_job.id).status == AttemptStatus.QUEUED
                assert preview.coordinator.attempt.status == AttemptStatus.QUEUED
            finally:
                await first.unload()
                await task
            assert first.process.returncode is not None
            assert project.repository.get_attempt(first_job.id).status == AttemptStatus.CANCELED
            assert (await second.run_next()).attempt.status == AttemptStatus.COMPLETED
            assert (await preview.run_next()).attempt.status == AttemptStatus.COMPLETED
            assert APPLICATION_GPU_RESOURCES.availability().available

        asyncio.run(run())


@pytest.mark.parametrize("case", ["success", "failure", "nonzero", "malformed", "gpu-oom"])
def test_child_exit_unloads_and_releases_ownership(project, case):
    resources = GPUResourceManager()
    enqueue(project)
    supervisor = worker(project, case, resources)
    result = asyncio.run(supervisor.run_next())
    assert result.returncode is not None and resources.availability().available
    assert result.device_identity == DEVICE.to_payload()
    if case == "gpu-oom":
        assert result.attempt.error.startswith("gpu_oom:")


def test_oom_retry_is_explicit_bounded_and_uses_a_new_worker(project):
    resources = GPUResourceManager()
    queued = enqueue(project)
    supervisor = worker(project, "gpu-oom", resources)
    first = asyncio.run(supervisor.run_next())
    original = project.repository.get_job(queued.job_id)
    assert len(project.repository.attempts(original.id)) == 1
    retry = supervisor.retry_oom(first)
    assert retry.job_id == original.id and retry.number == 2
    second = asyncio.run(supervisor.run_next())
    assert second.pid != first.pid and resources.availability().available
    with pytest.raises(ValueError, match="first"):
        supervisor.retry_oom(second)
    assert project.repository.get_job(original.id) == original
    assert len(project.repository.attempts(original.id)) == 2


def test_success_after_oom_retains_the_pinned_identity(project):
    enqueue(project)
    supervisor = worker(project, "gpu-oom", GPUResourceManager())
    failure = asyncio.run(supervisor.run_next())
    supervisor.retry_oom(failure)
    supervisor.launch = replace(supervisor.launch, environment={"WORKER_CASE": "success"})
    success = asyncio.run(supervisor.run_next())
    assert success.attempt.status == AttemptStatus.COMPLETED
    assert success.device_identity == failure.device_identity
    with pytest.raises(ValueError):
        supervisor.retry_oom(success)


def test_cpu_fallback_runs_without_gpu_lease_but_requires_new_pinned_request(project):
    resources = GPUResourceManager()
    held = resources.acquire("another-project", "cuda:0")
    cpu = replace(DEVICE, effective="cpu", fallback_approved=True)
    enqueue(project, cpu)
    result = asyncio.run(worker(project, "success", resources, device=cpu).run_next())
    assert result.attempt.status == AttemptStatus.COMPLETED
    assert result.device_identity["effective"] == "cpu"
    assert resources.availability().owner == "another-project"
    resources.release(held)


@pytest.mark.parametrize("device", [None, replace(DEVICE, effective="cpu", fallback_approved=True)])
def test_mismatched_or_unmanaged_device_cannot_execute_a_pinned_gpu_job(project, device):
    resources = GPUResourceManager()
    enqueue(project)
    supervisor = worker(project, "success", resources, device=device)
    result = asyncio.run(supervisor.run_next())
    assert result.attempt.status == AttemptStatus.FAILED and result.pid is None
    assert resources.availability().available


def test_cleanup_failure_quarantines_until_explicit_unload(project, monkeypatch):
    resources = GPUResourceManager()
    enqueue(project)
    supervisor = worker(project, "success", resources)
    cleanup = supervisor._cleanup

    async def fail_cleanup(tasks, logs):
        await cleanup(tasks, logs)
        raise OSError("cleanup confirmation failed")

    monkeypatch.setattr(supervisor, "_cleanup", fail_cleanup)
    with pytest.raises(OSError):
        asyncio.run(supervisor.run_next())
    assert resources.availability().quarantined
    assert resources.acquire("other", "cuda:0") is None
    monkeypatch.setattr(supervisor, "_cleanup", cleanup)
    asyncio.run(supervisor.unload())
    assert resources.availability().available
    assert project.repository.attempts(project.repository.jobs()[0].id)[-1].status == AttemptStatus.FAILED


@pytest.mark.parametrize("kind", ["timeout", "task-cancel"])
def test_deadline_and_task_cancellation_reap_before_releasing_gpu(project, kind):
    resources = GPUResourceManager()
    queued = enqueue(project)
    supervisor = worker(project, "ignore-cancel", resources)
    if kind == "timeout":
        supervisor.limits = replace(LIMITS, execution=.4)
        result = asyncio.run(supervisor.run_next())
        assert result.attempt.status == AttemptStatus.FAILED
    else:
        async def cancel():
            task = asyncio.create_task(supervisor.run_next())
            await running(supervisor, task)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        asyncio.run(cancel())
        assert project.repository.get_attempt(queued.id).status == AttemptStatus.CANCELED
    assert supervisor.process.returncode is not None
    assert resources.availability().available


def test_empty_paused_and_failed_launch_release_reservation(project, tmp_path):
    resources = GPUResourceManager()
    supervisor = worker(project, "success", resources)
    assert asyncio.run(supervisor.run_next()) is None
    enqueue(project)
    project.pause()
    assert asyncio.run(supervisor.run_next()) is None
    assert resources.availability().available
    project.resume()
    supervisor.launch = WorkerLaunch(tmp_path / "missing.exe")
    result = asyncio.run(supervisor.run_next())
    assert result.attempt.status == AttemptStatus.FAILED and result.pid is None
    assert resources.availability().available


def test_device_identity_survives_project_reopen(project):
    queued = enqueue(project)
    repository = project.repository.project
    root = repository.workspace
    job = project.repository.get_job(queued.job_id)
    repository.close()
    with ProjectSession.open(root, repository_factory=ProjectRepository) as reopened:
        restored = JobRepository(reopened.repository).get_job(job.id)
        DEVICE.validate(restored.request)
        assert json.loads(restored.request.effective_identity_json)["runtime_device"] == DEVICE.to_payload()

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

from app.tooling.local_autopilot.controller import AutopilotController, ControllerEvent, ControllerSnapshot, ScopeChoices
from app.tooling.local_autopilot.epic_pipeline import EpicPipelineResult
from app.tooling.local_autopilot.milestone_pipeline import MilestonePipelineResult
from app.tooling.local_autopilot.models import AutopilotRequest, AutopilotRun, PullRequestInfo, RunMode, RunStatus, ScopeType
from app.tooling.local_autopilot.process_runner import ProcessResult
from app.tooling.local_autopilot.state_store import load_run_state, save_run_state


def _process_result(command: tuple[str, ...]) -> ProcessResult:
    return ProcessResult(
        command=command,
        status="PASS",
        exit_code=0,
        duration_ms=5,
        timed_out=False,
        cancelled=False,
        stdout_lines=("ok",),
        stderr_lines=(),
        output_truncated=False,
        process_tree_killed=False,
        pid=1234,
    )


def _wait_until_idle(controller: AutopilotController, *, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while controller.is_running():
        if time.monotonic() > deadline:
            raise TimeoutError("controller did not stop in time")
        time.sleep(0.01)


def test_controller_start_emits_logs_and_updates_snapshot(tmp_path):
    calls: list[tuple[str, ...]] = []

    def process_runner(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        return _process_result(command)

    def epic_runner(run, **kwargs):
        runner = kwargs["process_runner_fn"]
        runner(["git", "status"], cwd=tmp_path, timeout_seconds=1, heartbeat_seconds=0)
        pull_request = PullRequestInfo(
            number=7,
            url="https://example.invalid/pr/7",
            title="E001: launch",
            base_branch="master",
            head_branch="feat/local-autopilot-ui",
        )
        finished = replace(
            run,
            status=RunStatus.COMPLETED,
            updated_at="2026-07-23T12:01:00Z",
            branch_name="feat/local-autopilot-ui",
            pull_request=pull_request,
            last_error=None,
        )
        save_run_state(finished, root=tmp_path)
        return EpicPipelineResult(
            status=RunStatus.COMPLETED,
            run=finished,
            epic_id="E001",
            branch_name="feat/local-autopilot-ui",
            task_ids=(),
            task_results=(),
            command_results=(),
            pull_request=pull_request,
            reason=None,
        )

    controller = AutopilotController(
        root=tmp_path,
        process_runner_fn=process_runner,
        epic_pipeline_runner=epic_runner,
        milestone_pipeline_runner=lambda *args, **kwargs: None,  # pragma: no cover
        run_id_factory=lambda: "run-123",
    )

    run = controller.start_run(
        repo_path=tmp_path,
        scope_type=ScopeType.EPIC,
        scope_id="E001",
        run_mode=RunMode.FULL,
        create_draft_pr=True,
    )
    assert run.run_id == "run-123"
    _wait_until_idle(controller)

    events = controller.poll_events()
    assert any(event.kind == "log" and event.message == "$ git status" for event in events)
    assert any(event.kind == "finished" for event in events)

    snapshot = controller.snapshot()
    assert snapshot.status == RunStatus.COMPLETED
    assert snapshot.branch_name == "feat/local-autopilot-ui"
    assert snapshot.pull_request_url == "https://example.invalid/pr/7"
    assert controller.latest_pr_url() == "https://example.invalid/pr/7"
    assert calls[0] == ("git", "status")


def test_controller_resume_uses_latest_milestone_state(tmp_path):
    received: dict[str, AutopilotRun] = {}

    def milestone_runner(run, **kwargs):
        received["run"] = run
        finished = replace(
            run,
            status=RunStatus.COMPLETED,
            updated_at="2026-07-23T12:04:00Z",
            last_error=None,
        )
        save_run_state(finished, root=tmp_path)
        return MilestonePipelineResult(
            status=RunStatus.COMPLETED,
            run=finished,
            milestone_id="M001",
            epic_id="E001",
            branch_name="bookkeeping/M001/E001",
            pull_request=run.pull_request,
            epic_result=None,
            reason=None,
        )

    request = AutopilotRequest(
        scope_type=ScopeType.MILESTONE,
        scope_id="M001",
        run_mode=RunMode.FULL,
        repo_path=str(tmp_path),
    )
    waiting = AutopilotRun(
        run_id="run-999",
        request=request,
        status=RunStatus.WAITING_FOR_MERGE,
        created_at="2026-07-23T12:00:00Z",
        updated_at="2026-07-23T12:03:00Z",
        milestone_id="M001",
        epic_id="E001",
        branch_name="bookkeeping/M001/E001",
        pull_request=PullRequestInfo(
            number=33,
            url="https://example.invalid/pr/33",
            title="M001: close epic",
            base_branch="master",
            head_branch="bookkeeping/M001/E001",
        ),
    )
    save_run_state(waiting, root=tmp_path)

    controller = AutopilotController(
        root=tmp_path,
        process_runner_fn=_process_result,
        epic_pipeline_runner=lambda *args, **kwargs: None,  # pragma: no cover
        milestone_pipeline_runner=milestone_runner,
        run_id_factory=lambda: "unused",
    )

    resumed = controller.resume_run(repo_path=tmp_path, scope_type=ScopeType.MILESTONE, scope_id="M001", create_draft_pr=True)
    assert resumed.run_id == "run-999"
    _wait_until_idle(controller)

    assert received["run"].run_id == "run-999"
    assert controller.snapshot().status == RunStatus.COMPLETED


def test_controller_available_scope_choices_reads_manifests(tmp_path):
    workstreams = tmp_path / ".specify" / "workstreams"
    workstreams.mkdir(parents=True, exist_ok=True)
    (workstreams / "epic.yml").write_text("id: E001\nstatus: planned\nmilestone: M001\ntasks: [T001]\n", encoding="utf-8")
    (workstreams / "milestone.yml").write_text("id: M001\nstatus: planned\ncompletion_criteria: [done]\n", encoding="utf-8")

    controller = AutopilotController(
        root=tmp_path,
        process_runner_fn=_process_result,
        epic_pipeline_runner=lambda *args, **kwargs: None,  # pragma: no cover
        milestone_pipeline_runner=lambda *args, **kwargs: None,  # pragma: no cover
    )

    choices = controller.available_scope_choices(tmp_path)
    assert choices == ScopeChoices(epic_ids=("E001",), milestone_ids=("M001",))

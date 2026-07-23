from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.tooling.local_autopilot.models import (
    AutopilotRequest,
    AutopilotRun,
    CommandResult,
    PullRequestInfo,
    RunMode,
    RunStatus,
    ScopeType,
    TaskResult,
)
from app.tooling.local_autopilot.state_store import load_run_state, run_state_path, save_run_state


def _run() -> AutopilotRun:
    command = CommandResult(
        command=("python", "-m", "pytest"),
        status="PASS",
        exit_code=0,
        duration_ms=500,
        timed_out=False,
        stdout_lines=("ok",),
        stderr_lines=(),
    )
    request = AutopilotRequest(
        scope_type=ScopeType.EPIC,
        scope_id="E123",
        run_mode=RunMode.FULL,
        repo_path="D:/Projects/ai-content-generation",
    )
    return AutopilotRun(
        run_id="run-123",
        request=request,
        status=RunStatus.TASK_RUNNING,
        created_at="2026-07-23T12:00:00Z",
        updated_at="2026-07-23T12:02:00Z",
        epic_id="E123",
        milestone_id="M001",
        branch_name="feat/local-autopilot-ui",
        current_task_id="T001",
        task_results=(TaskResult(task_id="T001", status=RunStatus.COMPLETED, command_results=(command,), commit_sha="a" * 40),),
        command_results=(command,),
        pull_request=PullRequestInfo(
            number=17,
            url="https://example.invalid/pull/17",
            title="feat: autopilot",
            base_branch="master",
            head_branch="feat/local-autopilot-ui",
        ),
        last_error=None,
    )


def test_state_path_is_namespaced_under_runtime(tmp_path) -> None:
    path = run_state_path("run-123", root=tmp_path)
    assert path == tmp_path / ".specify" / "runtime" / "autopilot" / "run-123.json"


def test_save_and_load_state_round_trip(tmp_path) -> None:
    run = _run()

    path = save_run_state(run, root=tmp_path)

    assert path.is_file()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["run_id"] == "run-123"
    assert "request" in loaded
    assert "pull_request" in loaded
    assert "env" not in json.dumps(loaded)

    restored = load_run_state("run-123", root=tmp_path)
    assert restored == run


def test_save_is_atomic_and_rewrites_existing_state(tmp_path) -> None:
    run = _run()
    path = save_run_state(run, root=tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("TASK_RUNNING", "FAILED"), encoding="utf-8")

    updated = AutopilotRun(
        run_id="run-123",
        request=run.request,
        status=RunStatus.FAILED,
        created_at=run.created_at,
        updated_at="2026-07-23T12:03:00Z",
        epic_id=run.epic_id,
        milestone_id=run.milestone_id,
        branch_name=run.branch_name,
        current_task_id=None,
        task_results=run.task_results,
        command_results=run.command_results,
        pull_request=run.pull_request,
        last_error="validation failed",
    )

    save_run_state(updated, root=tmp_path)
    restored = load_run_state("run-123", root=tmp_path)
    assert restored == updated
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_state_loader_rejects_invalid_run_id(tmp_path) -> None:
    with pytest.raises(ValueError):
        run_state_path("../run-123", root=tmp_path)

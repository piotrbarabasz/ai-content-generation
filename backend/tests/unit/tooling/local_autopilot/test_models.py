from __future__ import annotations

from pathlib import Path

import pytest

from app.tooling.local_autopilot.config import AutopilotConfig, DEFAULT_AUTOPILOT_CONFIG_PATH, load_autopilot_config
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


def test_enum_values_are_stable() -> None:
    assert ScopeType.EPIC.value == "epic"
    assert ScopeType.MILESTONE.value == "milestone"
    assert RunMode.FULL.value == "full"
    assert RunMode.STOP_BEFORE_PUSH.value == "stop_before_push"
    assert RunStatus.WAITING_FOR_MERGE.value == "waiting_for_merge"
    assert RunStatus.CANCELLED.value == "cancelled"


def test_request_validates_scope_identifiers() -> None:
    request = AutopilotRequest(
        scope_type=ScopeType.EPIC,
        scope_id="E001",
        run_mode=RunMode.FULL,
        repo_path="D:/Projects/ai-content-generation",
    )
    assert request.scope_id == "E001"

    with pytest.raises(ValueError):
        AutopilotRequest(
            scope_type=ScopeType.EPIC,
            scope_id="M001",
            run_mode=RunMode.FULL,
            repo_path="repo",
        )

    with pytest.raises(ValueError):
        AutopilotRequest(
            scope_type=ScopeType.MILESTONE,
            scope_id="E001",
            run_mode=RunMode.FULL,
            repo_path="repo",
        )


def test_models_support_nested_runtime_state() -> None:
    command = CommandResult(
        command=("git", "status"),
        status="PASS",
        exit_code=0,
        duration_ms=12,
        timed_out=False,
        stdout_lines=("ok",),
        stderr_lines=(),
    )
    task = TaskResult(task_id="T001", status=RunStatus.TASK_RUNNING, command_results=(command,), commit_sha="a" * 40)
    request = AutopilotRequest(
        scope_type=ScopeType.MILESTONE,
        scope_id="M001",
        run_mode=RunMode.STOP_BEFORE_PUSH,
        repo_path="D:/Projects/ai-content-generation",
    )
    run = AutopilotRun(
        run_id="run-001",
        request=request,
        status=RunStatus.PREFLIGHT,
        created_at="2026-07-23T12:00:00Z",
        updated_at="2026-07-23T12:01:00Z",
        milestone_id="M001",
        task_results=(task,),
        command_results=(command,),
        pull_request=PullRequestInfo(
            number=42,
            url="https://example.invalid/pull/42",
            title="feat: autopilot",
            base_branch="master",
            head_branch="feat/local-autopilot-ui",
        ),
    )

    assert run.task_results[0].command_results[0].stdout_lines == ("ok",)
    assert run.pull_request is not None
    assert run.pull_request.draft is True


def test_config_loader_reads_repo_defaults() -> None:
    config = load_autopilot_config()
    assert isinstance(config, AutopilotConfig)
    assert config.auto_commit is True
    assert config.auto_push is True
    assert config.create_draft_pr is True
    assert config.auto_merge is False
    assert config.deploy is False
    assert config.max_repair_cycles == 2
    assert config.max_tasks_per_run == 20
    assert config.command_timeout_seconds == 180
    assert config.codex_timeout_seconds == 3600
    assert config.closure_mode == "pull_request"
    assert DEFAULT_AUTOPILOT_CONFIG_PATH.as_posix().endswith(".specify/autopilot.yml")


def test_config_loader_rejects_wrong_closure_mode(tmp_path) -> None:
    path = tmp_path / "autopilot.yml"
    path.write_text(
        "\n".join(
            [
                "auto_commit: true",
                "auto_push: true",
                "create_draft_pr: true",
                "auto_merge: false",
                "deploy: false",
                "max_repair_cycles: 2",
                "max_tasks_per_run: 20",
                "command_timeout_seconds: 180",
                "codex_timeout_seconds: 3600",
                "closure_mode: direct_merge",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="closure_mode must be 'pull_request'"):
        load_autopilot_config(path)

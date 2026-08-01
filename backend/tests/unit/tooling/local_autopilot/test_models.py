from __future__ import annotations

import json
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
    ScopeExpansionProposal,
    TaskResult,
)
from app.tooling.local_autopilot.scope_proposal import (
    build_suggested_metadata_change,
    load_scope_expansion_proposal,
    save_scope_expansion_proposal,
)


def test_enum_values_are_stable() -> None:
    assert ScopeType.EPIC.value == "epic"
    assert ScopeType.MILESTONE.value == "milestone"
    assert RunMode.FULL.value == "full"
    assert RunMode.STOP_BEFORE_PUSH.value == "stop_before_push"
    assert RunStatus.WAITING_FOR_MERGE.value == "waiting_for_merge"
    assert RunStatus.PAUSED.value == "paused"
    assert RunStatus.BLOCKED.value == "blocked"
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


def test_scope_expansion_proposal_serializes_and_deduplicates_metadata(tmp_path: Path) -> None:
    proposal = ScopeExpansionProposal(
        schema_version=1,
        proposal_id="T045-run-001",
        run_id="run-001",
        task_id="T045",
        epic_id="E001",
        branch="feat/local-autopilot-ui",
        head_sha="a" * 40,
        baseline_head_sha="b" * 40,
        current_allowlist=("backend/app/tooling/local_autopilot/task_pipeline.py",),
        files_touched=("backend/app/tooling/local_autopilot/task_pipeline.py", "backend/tests/unit/tooling/local_autopilot/test_task_pipeline.py"),
        unexpected_paths=("backend/tests/unit/tooling/local_autopilot/test_task_pipeline.py",),
        codex_summary="Implementation complete.",
        codex_notes=("Touched file", "Touched file"),
        created_at="2026-07-29T12:00:00Z",
        status="pending",
    )

    path = save_scope_expansion_proposal(proposal, root=tmp_path)
    loaded = load_scope_expansion_proposal("T045", root=tmp_path)

    assert path == tmp_path / ".specify" / "runtime" / "scope-proposals" / "T045.json"
    assert loaded == proposal
    suggested = build_suggested_metadata_change(
        proposal.current_allowlist,
        ("backend/tests/unit/tooling/local_autopilot/test_task_pipeline.py", "backend/app/tooling/local_autopilot/task_pipeline.py"),
    )
    assert suggested.count("backend/app/tooling/local_autopilot/task_pipeline.py") == 1
    assert suggested.count("backend/tests/unit/tooling/local_autopilot/test_task_pipeline.py") == 1


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
    assert config.push_timeout_seconds == 1200
    assert config.pre_push_pytest_timeout_seconds == 900
    assert config.ci_pytest_timeout_seconds == 900
    assert config.hook_timeout_buffer_seconds == 120
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
                "push_timeout_seconds: 1200",
                "pre_push_pytest_timeout_seconds: 900",
                "ci_pytest_timeout_seconds: 900",
                "hook_timeout_buffer_seconds: 120",
                "codex_timeout_seconds: 3600",
                "closure_mode: direct_merge",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="closure_mode must be 'pull_request'"):
        load_autopilot_config(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("auto_merge", True, "auto_merge must be false"),
        ("deploy", True, "deploy must be false"),
    ],
)
def test_config_loader_rejects_unsupported_runtime_flags(tmp_path, field, value, message) -> None:
    path = tmp_path / "autopilot.yml"
    values = {
        "auto_commit": True,
        "auto_push": True,
        "create_draft_pr": True,
        "auto_merge": False,
        "deploy": False,
        "max_repair_cycles": 2,
        "max_tasks_per_run": 20,
        "command_timeout_seconds": 180,
        "push_timeout_seconds": 1200,
        "pre_push_pytest_timeout_seconds": 900,
        "ci_pytest_timeout_seconds": 900,
        "hook_timeout_buffer_seconds": 120,
        "codex_timeout_seconds": 3600,
        "closure_mode": "pull_request",
    }
    values[field] = value
    path.write_text(
        "\n".join(
            f"{key}: {json.dumps(val) if isinstance(val, str) else str(val).lower() if isinstance(val, bool) else val}"
            for key, val in values.items()
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_autopilot_config(path)


def test_config_loader_requires_new_timeout_fields(tmp_path) -> None:
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
                "closure_mode: pull_request",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as excinfo:
        load_autopilot_config(path)

    message = str(excinfo.value)
    assert "push_timeout_seconds" in message
    assert "pre_push_pytest_timeout_seconds" in message
    assert "ci_pytest_timeout_seconds" in message
    assert "hook_timeout_buffer_seconds" in message


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("push_timeout_seconds", 0),
        ("pre_push_pytest_timeout_seconds", -1),
        ("ci_pytest_timeout_seconds", 0),
        ("hook_timeout_buffer_seconds", -5),
    ],
)
def test_config_loader_rejects_non_positive_timeout_values(tmp_path, field, value) -> None:
    path = tmp_path / "autopilot.yml"
    values = {
        "auto_commit": True,
        "auto_push": True,
        "create_draft_pr": True,
        "auto_merge": False,
        "deploy": False,
        "max_repair_cycles": 2,
        "max_tasks_per_run": 20,
        "command_timeout_seconds": 180,
        "push_timeout_seconds": 1200,
        "pre_push_pytest_timeout_seconds": 900,
        "ci_pytest_timeout_seconds": 900,
        "hook_timeout_buffer_seconds": 120,
        "codex_timeout_seconds": 3600,
        "closure_mode": "pull_request",
    }
    values[field] = value
    path.write_text(
        "\n".join([f"{key}: {json.dumps(val) if isinstance(val, str) else str(val).lower() if isinstance(val, bool) else val}" for key, val in values.items()]) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=f"{field} must be positive"):
        load_autopilot_config(path)

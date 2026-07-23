from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.tooling import agent_task_finalize as finalize


@dataclass
class FakeProcessResult:
    command: tuple[str, ...]
    status: str = "PASS"
    exit_code: int | None = 0
    duration_ms: int = 7
    timed_out: bool = False
    stdout_lines: tuple[str, ...] = ()
    stderr_lines: tuple[str, ...] = ()
    output_truncated: bool = False
    process_tree_killed: bool = False
    pid: int | None = 4321


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _setup_repo(
    tmp_path: Path,
    *,
    validation_commands: str = "python -m pytest backend/tests/unit/test_t045_domain_primitives.py",
) -> None:
    task_runs = tmp_path / ".specify" / "runtime" / "task-runs" / "T045"
    tasks = tmp_path / "specs" / "001-ai-content-studio"
    task_runs.mkdir(parents=True, exist_ok=True)
    tasks.mkdir(parents=True, exist_ok=True)

    _write(
        tasks / "tasks.md",
        "\n".join(
            [
                "- [ ] T045 Add direct tests for shared domain primitives",
                "Milestone: M001",
                "Epic: E001",
                "Risk: medium",
                "Implementation files: none",
                "Test files: `backend/tests/unit/test_t045_domain_primitives.py`",
                f"Validation commands: {validation_commands}",
                "Acceptance criteria: direct behavioral coverage",
                "Dependencies: T004",
                "",
            ]
        ),
    )

    _write(
        task_runs / "baseline.json",
        json.dumps(
            {
                "schema_version": 1,
                "task": "T045",
                "epic": "E001",
                "branch": "epic/E001-execution-domain",
                "head_sha": "a" * 40,
                "tracked": [],
                "staged": [],
                "untracked": [],
                "deleted": [],
                "renamed": [],
            },
            indent=2,
        ),
    )


def _patch_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(finalize, "ROOT", tmp_path)
    monkeypatch.setattr(finalize, "TASKS_FILE", tmp_path / "specs" / "001-ai-content-studio" / "tasks.md")
    monkeypatch.setattr(finalize, "TASK_RUNS_DIR", tmp_path / ".specify" / "runtime" / "task-runs")


def _patch_snapshot(
    monkeypatch,
    *,
    status: str = "PASS",
    head_sha: str = "a" * 40,
    branch: str = "epic/E001-execution-domain",
    tracked: list[str] | None = None,
    staged: list[str] | None = None,
    untracked: list[str] | None = None,
    deleted: list[str] | None = None,
    renamed: list[dict[str, str]] | None = None,
) -> None:
    snapshot = {
        "status": status,
        "branch": branch,
        "head_sha": head_sha,
        "tracked": tracked or [],
        "staged": staged or [],
        "untracked": untracked or [],
        "deleted": deleted or [],
        "renamed": renamed or [],
        "duration_ms": 9,
        "reason": "" if status == "PASS" else "snapshot failed",
    }
    monkeypatch.setattr(finalize.repository_checks, "capture_git_snapshot", lambda **kwargs: snapshot)


def _metadata_command(task_id: str = "T045") -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "backend.app.tooling.repository_checks",
        "--mode",
        "task-metadata",
        "--tasks",
        task_id,
        "--json",
    )


def _diff_command() -> tuple[str, ...]:
    return ("git", "--no-pager", "diff", "--check")


def _task_command() -> tuple[str, ...]:
    return (sys.executable, "-m", "pytest", "backend/tests/unit/test_t045_domain_primitives.py")


def _patch_process_runner(monkeypatch, responses: dict[tuple[str, ...], FakeProcessResult]) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def fake_run_process(argv, **kwargs):
        key = tuple(argv)
        calls.append(key)
        if key not in responses:
            raise AssertionError(f"unexpected command: {key}")
        return responses[key]

    monkeypatch.setattr(finalize.process_runner, "run_process", fake_run_process)
    return calls


def test_finalize_runs_mandatory_checks_before_task_commands_and_reports_all_sections(tmp_path, monkeypatch, capsys):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_snapshot(monkeypatch)
    calls = _patch_process_runner(
        monkeypatch,
        {
            _diff_command(): FakeProcessResult(command=_diff_command(), status="PASS", exit_code=0),
            _task_command(): FakeProcessResult(command=_task_command(), status="PASS", exit_code=0),
        },
    )

    exit_code = finalize.main(["--task", "T045", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["exit_code"] == 0
    assert [check["name"] for check in payload["checks"]] == [
        "baseline",
        "head",
        "branch",
        "baseline_conflicts",
        "allowlist",
        "scope_drift",
        "task_metadata_validation",
        "git_diff_check",
        "task_validation_commands",
    ]
    assert calls == [_diff_command(), _task_command()]
    assert payload["checks"][6]["details"]["exit_code"] == 0
    assert payload["checks"][7]["details"]["exit_code"] == 0
    assert payload["checks"][8]["details"]["exit_code"] == 0
    assert payload["checks"][8]["details"]["commands"]
    assert payload["checks"][8]["details"]["broad_validation"] is True


def test_metadata_check_runs_even_when_task_metadata_omits_git_diff_check(tmp_path, monkeypatch):
    _setup_repo(tmp_path, validation_commands="python -m pytest backend/tests/unit/test_t045_domain_primitives.py")
    _patch_context(monkeypatch, tmp_path)
    _patch_snapshot(monkeypatch)
    calls = _patch_process_runner(
        monkeypatch,
        {
            _diff_command(): FakeProcessResult(command=_diff_command(), status="PASS", exit_code=0),
            _task_command(): FakeProcessResult(command=_task_command(), status="PASS", exit_code=0),
        },
    )

    result = finalize.run_finalize("T045")

    assert result.exit_code == 0
    assert result.status == "PASS"
    assert calls == [_diff_command(), _task_command()]
    assert any(check.name == "task_metadata_validation" for check in result.checks)
    assert any(check.name == "git_diff_check" for check in result.checks)


def test_metadata_fail_blocks_pass_and_skips_task_commands(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_snapshot(monkeypatch)
    calls = _patch_process_runner(
        monkeypatch,
        {
            _diff_command(): FakeProcessResult(command=_diff_command(), status="PASS", exit_code=0),
        },
    )
    monkeypatch.setattr(finalize.repository_checks, "task_metadata", lambda tasks: [{"task": "T045", "reason": "unknown dependency task T004"}])

    result = finalize.run_finalize("T045")

    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert calls == [_diff_command()]
    assert any(check.name == "task_metadata_validation" and check.status == "FAIL" for check in result.checks)
    assert any(check.name == "task_validation_commands" and check.details.get("skipped") is True for check in result.checks)
    assert any("blocking check failed: task_metadata_validation" in reason for reason in result.reasons)


def test_diff_fail_blocks_pass_and_skips_task_commands(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_snapshot(monkeypatch)
    calls = _patch_process_runner(
        monkeypatch,
        {
            _diff_command(): FakeProcessResult(command=_diff_command(), status="FAIL", exit_code=1, stdout_lines=("diff failed",), stderr_lines=()),
        },
    )

    result = finalize.run_finalize("T045")

    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert calls == [_diff_command()]
    assert any(check.name == "git_diff_check" and check.status == "FAIL" for check in result.checks)
    assert any(check.name == "task_validation_commands" and check.details.get("blocked_by") == "git_diff_check" for check in result.checks)


def test_diff_timeout_returns_exit_code_three(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_snapshot(monkeypatch)
    calls = _patch_process_runner(
        monkeypatch,
        {
            _diff_command(): FakeProcessResult(command=_diff_command(), status="TIMEOUT", exit_code=None, timed_out=True),
        },
    )

    result = finalize.run_finalize("T045")

    assert result.exit_code == 3
    assert result.status == "TIMEOUT"
    assert calls == [_diff_command()]
    assert any(check.name == "git_diff_check" and check.status == "TIMEOUT" for check in result.checks)


def test_task_commands_run_after_mandatory_checks(tmp_path, monkeypatch):
    _setup_repo(tmp_path, validation_commands="python -m pytest backend/tests/unit/test_t045_domain_primitives.py; git --no-pager diff --check")
    _patch_context(monkeypatch, tmp_path)
    _patch_snapshot(monkeypatch)
    calls = _patch_process_runner(
        monkeypatch,
        {
            _diff_command(): FakeProcessResult(command=_diff_command(), status="PASS", exit_code=0),
            _task_command(): FakeProcessResult(command=_task_command(), status="PASS", exit_code=0),
        },
    )

    result = finalize.run_finalize("T045")

    assert result.exit_code == 0
    assert calls == [_diff_command(), _task_command()]
    assert any(check.name == "task_validation_commands" and check.status == "PASS" for check in result.checks)
    task_check = next(check for check in result.checks if check.name == "task_validation_commands")
    assert task_check.details["skipped_duplicate"] is True
    assert task_check.details["broad_validation"] is True
    assert len(task_check.details["commands"]) == 1
    assert task_check.details["commands"][0]["command"][0] == sys.executable


def test_unsafe_shell_operators_are_rejected_without_running_task_command(tmp_path, monkeypatch):
    _setup_repo(tmp_path, validation_commands="python -m pytest && echo nope")
    _patch_context(monkeypatch, tmp_path)
    _patch_snapshot(monkeypatch)
    calls = _patch_process_runner(
        monkeypatch,
        {
            _diff_command(): FakeProcessResult(command=_diff_command(), status="PASS", exit_code=0),
        },
    )

    result = finalize.run_finalize("T045")

    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert calls == [_diff_command()]
    assert any(check.name == "task_validation_commands" and check.details.get("blocked_by") == "unsafe_validation_command" for check in result.checks)
    assert any("forbidden shell operator" in reason for reason in result.reasons)


def test_finalize_fails_on_scope_drift(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_snapshot(monkeypatch, tracked=["backend/tests/unit/test_t045_domain_primitives.py", "backend/app/tooling/agent_task_finalize.py"])
    _patch_process_runner(
        monkeypatch,
        {
            _diff_command(): FakeProcessResult(command=_diff_command(), status="PASS", exit_code=0),
        },
    )

    result = finalize.run_finalize("T045")

    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert any(check.name == "scope_drift" and check.status == "FAIL" for check in result.checks)
    assert any("unexpected added paths" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    ("baseline_key", "snapshot_kwargs"),
    [
        ("tracked", {"tracked": ["backend/tests/unit/test_t045_domain_primitives.py"]}),
        ("staged", {"staged": ["backend/tests/unit/test_t045_domain_primitives.py"]}),
        ("untracked", {"untracked": ["backend/tests/unit/test_t045_domain_primitives.py"]}),
    ],
)
def test_finalize_fails_when_baseline_conflicts_with_allowlist(tmp_path, monkeypatch, baseline_key, snapshot_kwargs):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    baseline_path = tmp_path / ".specify" / "runtime" / "task-runs" / "T045" / "baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline[baseline_key] = ["backend/tests/unit/test_t045_domain_primitives.py"]
    baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    _patch_snapshot(monkeypatch)
    _patch_process_runner(
        monkeypatch,
        {
            _diff_command(): FakeProcessResult(command=_diff_command(), status="PASS", exit_code=0),
        },
    )

    result = finalize.run_finalize("T045")

    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert any(check.name == "baseline_conflicts" and check.status == "FAIL" for check in result.checks)
    assert any("baseline conflicts with pre-existing dirty paths" in reason for reason in result.reasons)


def test_finalize_passes_when_baseline_exists_on_disk_but_not_in_git_snapshot(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_snapshot(monkeypatch)
    calls = _patch_process_runner(
        monkeypatch,
        {
            _diff_command(): FakeProcessResult(command=_diff_command(), status="PASS", exit_code=0),
            _task_command(): FakeProcessResult(command=_task_command(), status="PASS", exit_code=0),
        },
    )

    result = finalize.run_finalize("T045")

    assert result.exit_code == 0
    assert result.status == "PASS"
    assert calls == [_diff_command(), _task_command()]
    assert any(check.name == "scope_drift" and check.status == "PASS" for check in result.checks)
    assert any(check.name == "baseline_conflicts" and check.status == "PASS" for check in result.checks)


def test_finalize_missing_baseline_fails(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    (tmp_path / ".specify" / "runtime" / "task-runs" / "T045" / "baseline.json").unlink()

    exit_code = finalize.main(["--task", "T045"])

    assert exit_code == 1


def test_finalize_invalid_task_selector_returns_usage_error() -> None:
    exit_code = finalize.main(["--task", "bad"])

    assert exit_code == 2


def test_finalize_json_output_contains_sections(tmp_path, monkeypatch, capsys):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_snapshot(monkeypatch)
    _patch_process_runner(
        monkeypatch,
        {
            _diff_command(): FakeProcessResult(command=_diff_command(), status="PASS", exit_code=0),
            _task_command(): FakeProcessResult(command=_task_command(), status="PASS", exit_code=0),
        },
    )

    exit_code = finalize.main(["--task", "T045", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["checks"][6]["name"] == "task_metadata_validation"
    assert payload["checks"][7]["name"] == "git_diff_check"
    assert payload["checks"][8]["name"] == "task_validation_commands"

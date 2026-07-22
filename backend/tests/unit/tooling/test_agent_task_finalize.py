from __future__ import annotations

import json
from pathlib import Path

from app.tooling import agent_task_finalize as finalize


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _setup_repo(tmp_path: Path) -> None:
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
                "Validation commands: python -m pytest backend/tests/unit/test_t045_domain_primitives.py; git --no-pager diff --check",
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
                "task": "T045",
                "epic": "E001",
                "branch": "epic/E001-execution-domain",
                "head_sha": "a" * 40,
                "tracked": [],
                "staged": [],
                "untracked": [".specify/runtime/task-runs/T045/baseline.json"],
            },
            indent=2,
        ),
    )


def _patch_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(finalize, "ROOT", tmp_path)
    monkeypatch.setattr(finalize, "TASKS_FILE", tmp_path / "specs" / "001-ai-content-studio" / "tasks.md")
    monkeypatch.setattr(finalize, "TASK_RUNS_DIR", tmp_path / ".specify" / "runtime" / "task-runs")


def _patch_snapshot(monkeypatch, *, head_sha: str = "a" * 40, branch: str = "epic/E001-execution-domain", tracked: str = "backend/tests/unit/test_t045_domain_primitives.py", staged: str = "", untracked: str = ".specify/runtime/task-runs/T045/baseline.json") -> None:
    def fake_git_stdout(command):
        mapping = {
            ("git", "rev-parse", "HEAD"): head_sha,
            ("git", "branch", "--show-current"): branch,
            ("git", "diff", "--name-only"): tracked,
            ("git", "diff", "--cached", "--name-only"): staged,
            ("git", "ls-files", "--others", "--exclude-standard"): untracked,
        }
        return mapping[tuple(command)]

    monkeypatch.setattr(finalize, "_git_stdout", fake_git_stdout)


def _patch_validation_commands(monkeypatch, statuses: list[str] | None = None) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    statuses = statuses or ["PASS", "PASS"]

    def fake_run_process(command):
        calls.append(tuple(command))
        index = len(calls) - 1
        status = statuses[index] if index < len(statuses) else "PASS"
        return {
            "status": status,
            "command": list(command),
            "exit_code": 0 if status == "PASS" else 1 if status == "FAIL" else None,
            "output_lines": [],
            "truncated": False,
        }

    monkeypatch.setattr(finalize.repository_checks, "run_process", fake_run_process)
    return calls


def test_finalize_pass_returns_json_and_runs_validation_commands(tmp_path, monkeypatch, capsys):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    calls = _patch_validation_commands(monkeypatch)
    _patch_snapshot(monkeypatch)

    exit_code = finalize.main(["--task", "T045", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["exit_code"] == 0
    assert payload["task"] == "T045"
    assert payload["epic"] == "E001"
    assert payload["branch"] == "epic/E001-execution-domain"
    assert payload["baseline_path"].endswith("T045/baseline.json")
    assert payload["allowlist"] == ["backend/tests/unit/test_t045_domain_primitives.py"]
    assert payload["validation_commands"] == [
        "python -m pytest backend/tests/unit/test_t045_domain_primitives.py",
        "git --no-pager diff --check",
    ]
    assert [check["name"] for check in payload["checks"]] == [
        "baseline",
        "head",
        "branch",
        "allowlist",
        "scope_drift",
        "validation_commands",
    ]
    assert len(calls) == 2


def test_finalize_fail_on_scope_drift(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_validation_commands(monkeypatch)
    monkeypatch.setattr(
        finalize,
        "_git_stdout",
        lambda command: {
            ("git", "rev-parse", "HEAD"): "a" * 40,
            ("git", "branch", "--show-current"): "epic/E001-execution-domain",
            ("git", "diff", "--name-only"): "backend/tests/unit/test_t045_domain_primitives.py\nbackend/app/tooling/agent_task_finalize.py",
            ("git", "diff", "--cached", "--name-only"): "",
            ("git", "ls-files", "--others", "--exclude-standard"): ".specify/runtime/task-runs/T045/baseline.json",
        }[tuple(command)],
    )

    result = finalize.run_finalize("T045")

    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert any("unexpected added paths" in reason for reason in result.reasons)


def test_finalize_timeout_from_validation_commands(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_snapshot(monkeypatch)
    monkeypatch.setattr(
        finalize.repository_checks,
        "run_process",
        lambda command: {
            "status": "TIMEOUT",
            "command": list(command),
            "exit_code": None,
            "output_lines": ["command timed out after 20 seconds"],
            "truncated": False,
        },
    )

    result = finalize.run_finalize("T045")

    assert result.exit_code == 3
    assert result.status == "TIMEOUT"
    assert any(check.name == "validation_commands" and check.status == "TIMEOUT" for check in result.checks)


def test_finalize_stale_head_fails(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_validation_commands(monkeypatch)
    _patch_snapshot(monkeypatch, head_sha="b" * 40)

    result = finalize.run_finalize("T045")

    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert any("baseline" in reason and "HEAD" in reason for reason in result.reasons)


def test_finalize_missing_baseline_fails(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_snapshot(monkeypatch)
    _patch_validation_commands(monkeypatch)
    (tmp_path / ".specify" / "runtime" / "task-runs" / "T045" / "baseline.json").unlink()

    exit_code = finalize.main(["--task", "T045"])

    assert exit_code == 1


def test_finalize_invalid_task_selector_returns_usage_error() -> None:
    exit_code = finalize.main(["--task", "bad"])

    assert exit_code == 2

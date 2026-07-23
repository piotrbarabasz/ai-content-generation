from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.tooling import agent_task_preflight as preflight


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _setup_repo(tmp_path: Path) -> None:
    workstreams = tmp_path / ".specify" / "workstreams"
    runtime = tmp_path / ".specify" / "runtime"
    tasks = tmp_path / "specs" / "001-ai-content-studio"
    workstreams.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    tasks.mkdir(parents=True, exist_ok=True)

    _write(
        workstreams / "M001.yml",
        "\n".join(
            [
                "id: M001",
                "title: Milestone M001",
                "status: active",
                "goal: goal",
                "epics:",
                "  - E001",
                "completion_criteria:",
                "  - Tests pass",
                "",
            ]
        ),
    )
    _write(
        workstreams / "E001.yml",
        "\n".join(
            [
                "id: E001",
                "title: Epic E001",
                "milestone: M001",
                "feature: specs/001-ai-content-studio",
                "base_branch: master",
                "branch: epic/E001-execution-domain",
                "status: active",
                "risk: medium",
                "depends_on: []",
                "tasks:",
                "  - T001",
                "  - T045",
                "required_checks:",
                "  - python -m pytest",
                "  - git --no-pager diff --check",
                "pr_policy:",
                "  one_pr_per_epic: true",
                "  merge_requires_human: true",
                "  auto_merge: false",
                "commit_policy:",
                "  one_commit_per_task: true",
                "  commit_requires_human: true",
                "  auto_commit: false",
                "",
            ]
        ),
    )
    _write(
        tasks / "tasks.md",
        "\n".join(
            [
                "- [X] T001 Prepare domain primitives",
                "Milestone: M001",
                "Epic: E001",
                "Risk: medium",
                "Implementation files: none",
                "Test files: none",
                "Validation commands: python -m pytest",
                "Acceptance criteria: done",
                "Dependencies: none",
                "",
                "- [ ] T045 Add direct tests for shared domain primitives",
                "Milestone: M001",
                "Epic: E001",
                "Risk: medium",
                "Implementation files: none",
                "Test files: backend/tests/unit/test_t045_domain_primitives.py",
                "Validation commands: python -m pytest backend/tests/unit/test_t045_domain_primitives.py",
                "Acceptance criteria: direct behavioral coverage",
                "Dependencies: T001",
                "",
            ]
        ),
    )
    (runtime / "active-epic").write_text("E001\n", encoding="utf-8")


def _patch_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    monkeypatch.setattr(preflight, "ACTIVE_EPIC_FILE", tmp_path / ".specify" / "runtime" / "active-epic")
    monkeypatch.setattr(preflight, "WORKSTREAMS_DIR", tmp_path / ".specify" / "workstreams")
    monkeypatch.setattr(preflight, "TASKS_FILE", tmp_path / "specs" / "001-ai-content-studio" / "tasks.md")
    monkeypatch.setattr(preflight, "TASK_RUNS_DIR", tmp_path / ".specify" / "runtime" / "task-runs")


def _patch_successful_runtime(monkeypatch):
    calls: list[tuple[str, object]] = []

    def fake_git_stdout(command):
        calls.append(("git", tuple(command)))
        mapping = {
            ("git", "branch", "--show-current"): "epic/E001-execution-domain",
            ("git", "rev-parse", "HEAD"): "a" * 40,
            ("git", "diff", "--name-only"): "backend/app/tooling/agent_task_preflight.py",
            ("git", "diff", "--cached", "--name-only"): "backend/tests/unit/tooling/test_agent_task_preflight.py",
            ("git", "ls-files", "--others", "--exclude-standard"): "",
        }
        return mapping[tuple(command)]

    def fake_validate_guard(selector):
        calls.append(("guard", selector))
        return []

    def fake_task_metadata(tasks):
        calls.append(("task_metadata", tuple(tasks)))
        if tasks == ["T045"]:
            return []
        return [{"reason": "unknown dependency task T004"}]

    def fake_checks(mode, tasks=None):
        calls.append(("checks", mode, tuple(tasks or [])))
        return {
            "status": "PASS",
            "checks": [
                {"name": "task_metadata", "status": "PASS", "exit_code": 0, "findings": []},
                {"name": "git_diff", "status": "PASS", "exit_code": 0, "output_lines": []},
            ],
        }

    monkeypatch.setattr(preflight, "_git_stdout", fake_git_stdout)
    monkeypatch.setattr(preflight.workstream_validation, "validate_guard", fake_validate_guard)
    monkeypatch.setattr(preflight.repository_checks, "task_metadata", fake_task_metadata)
    monkeypatch.setattr(preflight.repository_checks, "checks", fake_checks)
    return calls


def test_next_selector_runs_full_preflight_and_writes_baseline_json(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    calls = _patch_successful_runtime(monkeypatch)

    result = preflight.run_preflight("next", version_info=(3, 11, 0))

    assert result.exit_code == 0
    assert result.task_id == "T045"
    assert result.epic_id == "E001"
    assert result.branch == "epic/E001-execution-domain"
    baseline_path = tmp_path / ".specify" / "runtime" / "task-runs" / "T045" / "baseline.json"
    assert result.baseline_path == str(baseline_path)
    assert baseline_path.is_file()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert baseline == {
        "schema_version": 1,
        "task": "T045",
        "epic": "E001",
        "branch": "epic/E001-execution-domain",
        "head_sha": "a" * 40,
        "tracked": ["backend/app/tooling/agent_task_preflight.py"],
        "staged": ["backend/tests/unit/tooling/test_agent_task_preflight.py"],
        "untracked": [],
    }
    assert [check.name for check in result.checks] == [
        "python_version",
        "active_epic",
        "branch",
        "guard",
        "dependency_validation",
        "task_ownership",
        "repository_preflight",
        "baseline_capture",
    ]
    assert ("guard", "next") in calls
    assert ("task_metadata", ("T045",)) in calls
    assert ("checks", "preflight", ("T045",)) in calls


def test_json_cli_outputs_task_and_baseline(tmp_path, monkeypatch, capsys):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)
    monkeypatch.setattr(preflight.sys, "version_info", (3, 11, 0))

    exit_code = preflight.main(["--selector", "T045", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task"] == "T045"
    assert payload["epic"] == "E001"
    assert payload["branch"] == "epic/E001-execution-domain"
    assert Path(payload["baseline_path"]).as_posix().endswith("T045/baseline.json")


def test_invalid_selector_returns_usage_error(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)
    monkeypatch.setattr(preflight.sys, "version_info", (3, 11, 0))

    exit_code = preflight.main(["--selector", "bad"])

    assert exit_code == 2


def test_guard_failure_blocks_before_baseline_capture(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    calls = []

    monkeypatch.setattr(preflight, "_git_stdout", lambda command: "epic/E001-execution-domain" if command == ["git", "branch", "--show-current"] else "a" * 40)
    monkeypatch.setattr(preflight.workstream_validation, "validate_guard", lambda selector: calls.append(selector) or ["active epic does not exist"])
    monkeypatch.setattr(preflight.repository_checks, "task_metadata", lambda tasks: [])
    monkeypatch.setattr(preflight.repository_checks, "checks", lambda mode, tasks=None: {"status": "PASS", "checks": []})

    result = preflight.run_preflight("T045", version_info=(3, 11, 0))

    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert result.baseline_path is None
    assert not (tmp_path / ".specify" / "runtime" / "task-runs" / "T045" / "baseline.json").exists()
    assert calls == ["T045"]


def test_branch_mismatch_returns_validation_failure(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)

    monkeypatch.setattr(preflight, "_git_stdout", lambda command: "master" if command == ["git", "branch", "--show-current"] else "a" * 40)

    result = preflight.run_preflight("T045", version_info=(3, 11, 0))

    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert result.baseline_path is None


def test_repository_preflight_timeout_maps_to_exit_code_three(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    monkeypatch.setattr(preflight, "_git_stdout", lambda command: "epic/E001-execution-domain" if command == ["git", "branch", "--show-current"] else "a" * 40)
    monkeypatch.setattr(preflight.workstream_validation, "validate_guard", lambda selector: [])
    monkeypatch.setattr(preflight.repository_checks, "task_metadata", lambda tasks: [])
    monkeypatch.setattr(
        preflight.repository_checks,
        "checks",
        lambda mode, tasks=None: {"status": "TIMEOUT", "checks": [{"name": "task_metadata", "status": "TIMEOUT", "exit_code": None, "findings": []}]},
    )

    result = preflight.run_preflight("T045", version_info=(3, 11, 0))

    assert result.exit_code == 3
    assert result.status == "TIMEOUT"
    assert result.baseline_path is None


def test_python_version_failure_returns_validation_failure(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)

    result = preflight.run_preflight("T045", version_info=(3, 9, 12))

    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert result.task_id == ""


def _git(tmp_path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)


def test_preflight_snapshot_excludes_ignored_runtime_baseline_in_real_git_repo(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)

    _write(tmp_path / ".gitignore", ".specify/runtime/\n")
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "add", ".gitignore")
    _git(tmp_path, "add", ".specify/workstreams/M001.yml")
    _git(tmp_path, "add", ".specify/workstreams/E001.yml")
    _git(tmp_path, "add", "specs/001-ai-content-studio/tasks.md")
    _git(tmp_path, "commit", "-m", "initial state")
    _git(tmp_path, "checkout", "-b", "epic/E001-execution-domain")

    monkeypatch.setattr(preflight.workstream_validation, "validate_guard", lambda selector: [])
    monkeypatch.setattr(preflight.repository_checks, "task_metadata", lambda tasks: [])
    monkeypatch.setattr(
        preflight.repository_checks,
        "checks",
        lambda mode, tasks=None: {"status": "PASS", "checks": []},
    )

    result = preflight.run_preflight("T045", version_info=(3, 11, 0))

    assert result.exit_code == 0
    baseline_path = tmp_path / ".specify" / "runtime" / "task-runs" / "T045" / "baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert baseline["schema_version"] == 1
    assert baseline["untracked"] == []

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.tooling import agent_task_preflight as preflight


@dataclass
class FakeProcessResult:
    command: tuple[str, ...]
    status: str = "PASS"
    exit_code: int | None = 0
    duration_ms: int = 5
    timed_out: bool = False
    stdout_lines: tuple[str, ...] = ()
    stderr_lines: tuple[str, ...] = ()
    output_truncated: bool = False
    process_tree_killed: bool = False
    pid: int | None = 1234


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

    def fake_validate_manifests(directory):
        calls.append(("manifests", directory))
        return []

    def fake_validate_task_epic_consistency(tasks_file, directory):
        calls.append(("consistency", tasks_file, directory))
        return []

    def fake_validate_active_epic(task_selector="next", runtime_file=None, directory=None, tasks_file=None):
        calls.append(("guard", task_selector))
        return []

    def fake_task_metadata(tasks):
        calls.append(("task_metadata", tuple(tasks)))
        return []

    def fake_snapshot(*, timeout_seconds, total_deadline):
        calls.append(("snapshot", timeout_seconds))
        return {
            "status": "PASS",
            "branch": "epic/E001-execution-domain",
            "head_sha": "a" * 40,
            "tracked": ["backend/app/tooling/agent_task_preflight.py"],
            "staged": ["backend/tests/unit/tooling/test_agent_task_preflight.py"],
            "untracked": [],
            "deleted": [],
            "renamed": [],
            "duration_ms": 11,
            "reason": "",
        }

    def fake_run_process(argv, **kwargs):
        calls.append(("process", tuple(argv), kwargs.get("timeout_seconds")))
        if tuple(argv) != ("git", "--no-pager", "diff", "--check"):
            raise AssertionError(f"unexpected command: {tuple(argv)}")
        return FakeProcessResult(command=tuple(argv), status="PASS", exit_code=0)

    monkeypatch.setattr(preflight.workstream_validation, "validate_manifests", fake_validate_manifests)
    monkeypatch.setattr(preflight.workstream_validation, "validate_task_epic_consistency", fake_validate_task_epic_consistency)
    monkeypatch.setattr(preflight.workstream_validation, "validate_active_epic", fake_validate_active_epic)
    monkeypatch.setattr(preflight.repository_checks, "task_metadata", fake_task_metadata)
    monkeypatch.setattr(preflight.repository_checks, "capture_git_snapshot", fake_snapshot)
    monkeypatch.setattr(preflight.repository_checks.process_runner, "run_process", fake_run_process)
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
    assert result.duration_ms >= 0
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
        "deleted": [],
        "renamed": [],
    }
    assert [check.name for check in result.checks] == [
        "python_version",
        "active_epic",
        "manifest_validation",
        "task_epic_consistency",
        "active_epic_guard",
        "selected_task_metadata",
        "git_snapshot",
        "git_diff_check",
        "baseline_capture",
    ]
    assert ("guard", "next") in calls
    assert ("task_metadata", ("T001", "T045")) in calls
    assert ("snapshot", preflight.SNAPSHOT_TIMEOUT_SECONDS) in calls


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
    assert payload["duration_ms"] >= 0
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
    calls: list[str] = []

    monkeypatch.setattr(preflight.workstream_validation, "validate_manifests", lambda directory: [])
    monkeypatch.setattr(preflight.workstream_validation, "validate_task_epic_consistency", lambda tasks_file, directory: [])
    monkeypatch.setattr(preflight.workstream_validation, "validate_active_epic", lambda **kwargs: ["active epic does not exist"])
    monkeypatch.setattr(preflight.repository_checks, "task_metadata", lambda tasks: calls.append("task_metadata") or [])
    monkeypatch.setattr(preflight.repository_checks, "capture_git_snapshot", lambda **kwargs: calls.append("snapshot") or {})

    result = preflight.run_preflight("T045", version_info=(3, 11, 0))

    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert result.baseline_path is None
    assert not (tmp_path / ".specify" / "runtime" / "task-runs" / "T045" / "baseline.json").exists()
    assert calls == []


def test_branch_mismatch_returns_validation_failure(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    monkeypatch.setattr(preflight.workstream_validation, "validate_manifests", lambda directory: [])
    monkeypatch.setattr(preflight.workstream_validation, "validate_task_epic_consistency", lambda tasks_file, directory: [])
    monkeypatch.setattr(preflight.workstream_validation, "validate_active_epic", lambda **kwargs: ["current branch does not match the epic manifest"])
    monkeypatch.setattr(preflight.repository_checks, "task_metadata", lambda tasks: [])

    result = preflight.run_preflight("T045", version_info=(3, 11, 0))

    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert result.baseline_path is None


def test_repository_preflight_timeout_maps_to_exit_code_three(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    monkeypatch.setattr(preflight.workstream_validation, "validate_manifests", lambda directory: [])
    monkeypatch.setattr(preflight.workstream_validation, "validate_task_epic_consistency", lambda tasks_file, directory: [])
    monkeypatch.setattr(preflight.workstream_validation, "validate_active_epic", lambda **kwargs: [])
    monkeypatch.setattr(preflight.repository_checks, "task_metadata", lambda tasks: [])
    monkeypatch.setattr(
        preflight.repository_checks,
        "capture_git_snapshot",
        lambda **kwargs: {
            "status": "TIMEOUT",
            "reason": "command timed out after 20 seconds",
            "branch": "",
            "head_sha": "",
            "tracked": [],
            "staged": [],
            "untracked": [],
            "deleted": [],
            "renamed": [],
            "duration_ms": 20,
        },
    )

    result = preflight.run_preflight("T045", version_info=(3, 11, 0))

    assert result.exit_code == 3
    assert result.status == "TIMEOUT"
    assert result.baseline_path is None


def _git(tmp_path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True, timeout=10)


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
    _git(tmp_path, "commit", "--no-gpg-sign", "-m", "initial state")
    _git(tmp_path, "checkout", "-b", "epic/E001-execution-domain")

    monkeypatch.setattr(preflight.workstream_validation, "validate_manifests", lambda directory: [])
    monkeypatch.setattr(preflight.workstream_validation, "validate_task_epic_consistency", lambda tasks_file, directory: [])
    monkeypatch.setattr(preflight.workstream_validation, "validate_active_epic", lambda **kwargs: [])
    monkeypatch.setattr(preflight.repository_checks, "task_metadata", lambda tasks: [])

    result = preflight.run_preflight("T045", version_info=(3, 11, 0))

    assert result.exit_code == 0
    baseline_path = tmp_path / ".specify" / "runtime" / "task-runs" / "T045" / "baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert baseline["schema_version"] == 1
    assert baseline["untracked"] == []
    assert baseline["deleted"] == []
    assert baseline["renamed"] == []
    assert all(".specify/runtime/task-runs/T045/baseline.json" not in item for item in baseline["tracked"])


def test_preflight_uses_one_git_status_one_rev_parse_and_one_diff_check(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    monkeypatch.setattr(preflight.workstream_validation, "validate_manifests", lambda directory: [])
    monkeypatch.setattr(preflight.workstream_validation, "validate_task_epic_consistency", lambda tasks_file, directory: [])
    monkeypatch.setattr(preflight.workstream_validation, "validate_active_epic", lambda **kwargs: [])
    monkeypatch.setattr(preflight.repository_checks, "task_metadata", lambda tasks: [])

    calls: list[tuple[str, ...]] = []

    def fake_run_process(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command == (
            "git",
            "status",
            "--porcelain=v1",
            "-z",
            "--branch",
            "--untracked-files=all",
        ):
            return FakeProcessResult(
                command=command,
                status="PASS",
                stdout_lines=("## epic/E001-execution-domain\0",),
            )
        if command == ("git", "rev-parse", "HEAD"):
            return FakeProcessResult(command=command, status="PASS", stdout_lines=("a" * 40 + "\n",))
        if command == ("git", "--no-pager", "diff", "--check"):
            return FakeProcessResult(command=command, status="PASS")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(preflight.repository_checks.process_runner, "run_process", fake_run_process)

    result = preflight.run_preflight("T045", version_info=(3, 11, 0))

    assert result.exit_code == 0
    assert calls == [
        ("git", "status", "--porcelain=v1", "-z", "--branch", "--untracked-files=all"),
        ("git", "rev-parse", "HEAD"),
        ("git", "--no-pager", "diff", "--check"),
    ]

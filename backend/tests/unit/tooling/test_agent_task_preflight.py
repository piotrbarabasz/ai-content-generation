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


def _setup_repo(
    tmp_path: Path,
    *,
    feature: str = "specs/001-ai-content-studio",
    include_spec: bool = True,
    include_plan: bool = True,
    include_tasks: bool = True,
    include_data_model: bool = True,
    include_research: bool = False,
    include_quickstart: bool = True,
    include_contracts: bool = True,
    completed_tasks: set[str] | None = None,
    dependency_overrides: dict[str, str] | None = None,
) -> None:
    workstreams = tmp_path / ".specify" / "workstreams"
    runtime = tmp_path / ".specify" / "runtime"
    feature_dir = (tmp_path / feature).resolve()
    completed_tasks = set({"T001", "T002", "T003", "T004", "T005"} if completed_tasks is None else completed_tasks)
    dependency_overrides = dict({} if dependency_overrides is None else dependency_overrides)
    workstreams.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    if feature_dir.is_relative_to(tmp_path.resolve()):
        feature_dir.mkdir(parents=True, exist_ok=True)

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
                f"feature: {feature}",
                "base_branch: master",
                "branch: epic/E001-execution-domain",
                "status: active",
                "risk: medium",
                "depends_on: []",
                "tasks:",
                "  - T001",
                "  - T002",
                "  - T003",
                "  - T004",
                "  - T005",
                "  - T006",
                "  - T045",
                "  - T046",
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
    if feature_dir.is_relative_to(tmp_path.resolve()):
        if include_spec:
            _write(feature_dir / "spec.md", "# Spec\n")
        if include_plan:
            _write(feature_dir / "plan.md", "# Plan\n")
        if include_tasks:
            task_definitions = [
                ("T001", "Prepare domain primitives", "None"),
                ("T002", "Add unit tests", "none"),
                ("T003", "Prepare docs", "N/A"),
                ("T004", "Update fixtures", "[]"),
                ("T005", "Wire integration helpers", "`T001`, `T002`"),
                ("T006", "Add gated downstream coverage", "`T045`, `T046`"),
                ("T045", "Add direct tests for shared domain primitives", "T001, T002"),
                ("T046", "Add integration coverage for shared domain primitives", "T001, T002"),
            ]
            _write(
                feature_dir / "tasks.md",
                "\n".join(
                    [
                        "\n".join(
                            [
                                f"- [{'X' if task_id in completed_tasks else ' '}] {task_id} {summary}",
                                "Milestone: M001",
                                "Epic: E001",
                                "Risk: medium",
                                "Implementation files: none",
                                "Test files: none"
                                if task_id in {"T001", "T002", "T003", "T004", "T005"}
                                else f"Test files: backend/tests/unit/{task_id.lower()}_coverage.py",
                                "Validation commands: python -m pytest"
                                if task_id in {"T001", "T002", "T003", "T004", "T005"}
                                else f"Validation commands: python -m pytest backend/tests/unit/{task_id.lower()}_coverage.py",
                                "Acceptance criteria: done",
                                f"Dependencies: {dependency_overrides.get(task_id, dependencies)}",
                                "",
                            ]
                        )
                        for task_id, summary, dependencies in task_definitions
                    ]
                ),
            )
        if include_data_model:
            _write(feature_dir / "data-model.md", "# Data model\n")
        if include_research:
            _write(feature_dir / "research.md", "# Research\n")
        if include_quickstart:
            _write(feature_dir / "quickstart.md", "# Quickstart\n")
        if include_contracts:
            (feature_dir / "contracts").mkdir(parents=True, exist_ok=True)
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

    def fake_validate_active_epic(task_selector="next", runtime_file=None, directory=None):
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


def _dependency_check(result):
    return next(check for check in result.checks if check.name == "dependency_readiness")


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
    feature_dir = tmp_path / "specs" / "001-ai-content-studio"
    assert result.feature_dir == str(feature_dir.resolve())
    assert result.spec_path == str((feature_dir / "spec.md").resolve())
    assert result.plan_path == str((feature_dir / "plan.md").resolve())
    assert result.tasks_path == str((feature_dir / "tasks.md").resolve())
    assert result.data_model_path == str((feature_dir / "data-model.md").resolve())
    assert result.research_path is None
    assert result.quickstart_path == str((feature_dir / "quickstart.md").resolve())
    assert result.contracts_dir == str((feature_dir / "contracts").resolve())
    assert result.available_docs == (
        str((feature_dir / "data-model.md").resolve()),
        str((feature_dir / "quickstart.md").resolve()),
        str((feature_dir / "contracts").resolve()),
    )
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
        "feature_context",
        "manifest_validation",
        "task_epic_consistency",
        "active_epic_guard",
        "dependency_readiness",
        "selected_task_metadata",
        "git_snapshot",
        "git_diff_check",
        "baseline_capture",
    ]
    assert ("guard", "next") in calls
    assert ("task_metadata", ("T001", "T002", "T003", "T004", "T005", "T006", "T045", "T046")) in calls
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
    assert payload["feature_dir"] == str((tmp_path / "specs" / "001-ai-content-studio").resolve())
    assert payload["spec_path"] == str((tmp_path / "specs" / "001-ai-content-studio" / "spec.md").resolve())
    assert payload["plan_path"] == str((tmp_path / "specs" / "001-ai-content-studio" / "plan.md").resolve())
    assert payload["tasks_path"] == str((tmp_path / "specs" / "001-ai-content-studio" / "tasks.md").resolve())
    assert payload["available_docs"] == [
        str((tmp_path / "specs" / "001-ai-content-studio" / "data-model.md").resolve()),
        str((tmp_path / "specs" / "001-ai-content-studio" / "quickstart.md").resolve()),
        str((tmp_path / "specs" / "001-ai-content-studio" / "contracts").resolve()),
    ]
    assert "FEATURE_DIR" not in payload
    assert "AVAILABLE_DOCS" not in payload
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
    monkeypatch.setattr(
        preflight.workstream_validation,
        "validate_active_epic",
        lambda task_selector="next", runtime_file=None, directory=None: ["active epic does not exist"],
    )
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
    monkeypatch.setattr(
        preflight.workstream_validation,
        "validate_active_epic",
        lambda task_selector="next", runtime_file=None, directory=None: ["current branch does not match the epic manifest"],
    )
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
    monkeypatch.setattr(
        preflight.workstream_validation,
        "validate_active_epic",
        lambda task_selector="next", runtime_file=None, directory=None: [],
    )
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


def test_feature_context_missing_required_files_fails_without_baseline(tmp_path, monkeypatch):
    for missing_name in ("spec.md", "plan.md", "tasks.md"):
        case_root = tmp_path / missing_name.replace(".", "_")
        _setup_repo(case_root)
        _patch_context(monkeypatch, case_root)
        calls = _patch_successful_runtime(monkeypatch)
        (case_root / "specs" / "001-ai-content-studio" / missing_name).unlink()

        result = preflight.run_preflight("T045", version_info=(3, 11, 0))

        baseline_path = case_root / ".specify" / "runtime" / "task-runs" / "T045" / "baseline.json"
        assert result.exit_code == 1
        assert result.status == "FAIL"
        assert result.baseline_path is None
        assert baseline_path.exists() is False
        assert calls == []
        assert any(check.name == "feature_context" and check.status == "FAIL" for check in result.checks)


def test_feature_context_outside_repo_is_rejected(tmp_path, monkeypatch):
    _setup_repo(tmp_path, feature="../outside")
    _patch_context(monkeypatch, tmp_path)
    calls = _patch_successful_runtime(monkeypatch)

    result = preflight.run_preflight("T045", version_info=(3, 11, 0))

    baseline_path = tmp_path / ".specify" / "runtime" / "task-runs" / "T045" / "baseline.json"
    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert result.baseline_path is None
    assert result.feature_dir is None
    assert baseline_path.exists() is False
    assert calls == []
    assert any(check.name == "feature_context" and check.status == "FAIL" for check in result.checks)


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
    _git(tmp_path, "add", "specs/001-ai-content-studio/spec.md")
    _git(tmp_path, "add", "specs/001-ai-content-studio/plan.md")
    _git(tmp_path, "add", "specs/001-ai-content-studio/tasks.md")
    _git(tmp_path, "add", "specs/001-ai-content-studio/data-model.md")
    _git(tmp_path, "add", "specs/001-ai-content-studio/quickstart.md")
    _git(tmp_path, "commit", "--no-gpg-sign", "-m", "initial state")
    _git(tmp_path, "checkout", "-b", "epic/E001-execution-domain")

    monkeypatch.setattr(preflight.workstream_validation, "validate_manifests", lambda directory: [])
    monkeypatch.setattr(preflight.workstream_validation, "validate_task_epic_consistency", lambda tasks_file, directory: [])
    monkeypatch.setattr(
        preflight.workstream_validation,
        "validate_active_epic",
        lambda task_selector="next", runtime_file=None, directory=None: [],
    )
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
    monkeypatch.setattr(
        preflight.workstream_validation,
        "validate_active_epic",
        lambda task_selector="next", runtime_file=None, directory=None: [],
    )
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


def test_next_skips_task_with_unfinished_dependencies_and_selects_t045(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)

    result = preflight.run_preflight("next", version_info=(3, 11, 0))

    dependency_check = _dependency_check(result)
    assert result.exit_code == 0
    assert result.task_id == "T045"
    assert dependency_check.status == "PASS"
    assert dependency_check.details["task"] == "T045"
    assert dependency_check.details["declared_dependencies"] == ["T001", "T002"]
    assert dependency_check.details["completed_dependencies"] == ["T001", "T002"]
    assert dependency_check.details["incomplete_dependencies"] == []
    assert dependency_check.details["unknown_dependencies"] == []


def test_next_selects_t046_after_t045_is_completed(tmp_path, monkeypatch):
    _setup_repo(tmp_path, completed_tasks={"T001", "T002", "T003", "T004", "T005", "T045"})
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)

    result = preflight.run_preflight("next", version_info=(3, 11, 0))

    dependency_check = _dependency_check(result)
    assert result.exit_code == 0
    assert result.task_id == "T046"
    assert dependency_check.details["task"] == "T046"
    assert dependency_check.details["declared_dependencies"] == ["T001", "T002"]
    assert dependency_check.details["completed_dependencies"] == ["T001", "T002"]


def test_next_selects_t006_after_t045_and_t046_are_completed(tmp_path, monkeypatch):
    _setup_repo(tmp_path, completed_tasks={"T001", "T002", "T003", "T004", "T005", "T045", "T046"})
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)

    result = preflight.run_preflight("next", version_info=(3, 11, 0))

    dependency_check = _dependency_check(result)
    assert result.exit_code == 0
    assert result.task_id == "T006"
    assert dependency_check.details["task"] == "T006"
    assert dependency_check.details["declared_dependencies"] == ["T045", "T046"]
    assert dependency_check.details["completed_dependencies"] == ["T045", "T046"]
    assert dependency_check.details["incomplete_dependencies"] == []


def test_explicit_t006_fails_with_incomplete_dependencies_and_no_baseline(tmp_path, monkeypatch, capsys):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)
    monkeypatch.setattr(preflight.sys, "version_info", (3, 11, 0))

    exit_code = preflight.main(["--selector", "T006", "--json"])

    payload = json.loads(capsys.readouterr().out)
    baseline_path = tmp_path / ".specify" / "runtime" / "task-runs" / "T006" / "baseline.json"
    assert exit_code == 1
    assert payload["status"] == "FAIL"
    assert payload["task"] == "T006"
    assert payload["reason"] == "task dependencies are incomplete"
    assert payload["declared_dependencies"] == ["T045", "T046"]
    assert payload["completed_dependencies"] == []
    assert payload["incomplete_dependencies"] == ["T045", "T046"]
    assert payload["unknown_dependencies"] == []
    assert not baseline_path.exists()


def test_explicit_t006_passes_after_dependencies_are_complete(tmp_path, monkeypatch):
    _setup_repo(tmp_path, completed_tasks={"T001", "T002", "T003", "T004", "T005", "T045", "T046"})
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)

    result = preflight.run_preflight("T006", version_info=(3, 11, 0))

    dependency_check = _dependency_check(result)
    assert result.exit_code == 0
    assert result.task_id == "T006"
    assert dependency_check.status == "PASS"
    assert dependency_check.details["declared_dependencies"] == ["T045", "T046"]
    assert dependency_check.details["completed_dependencies"] == ["T045", "T046"]
    assert dependency_check.details["incomplete_dependencies"] == []
    assert dependency_check.details["unknown_dependencies"] == []


def test_none_none_na_and_brackets_mean_no_dependencies(tmp_path, monkeypatch):
    variants = {
        "T001": "None",
        "T002": "none",
        "T003": "N/A",
        "T004": "[]",
    }
    for task_id, dependency_text in variants.items():
        case_root = tmp_path / task_id.lower()
        completed = {"T001", "T002", "T003", "T004", "T005"} - {task_id}
        _setup_repo(case_root, completed_tasks=completed, dependency_overrides={task_id: dependency_text})
        _patch_context(monkeypatch, case_root)
        _patch_successful_runtime(monkeypatch)

        result = preflight.run_preflight(task_id, version_info=(3, 11, 0))

        dependency_check = _dependency_check(result)
        assert result.exit_code == 0, task_id
        assert result.task_id == task_id
        assert dependency_check.details["declared_dependencies"] == []
        assert dependency_check.details["completed_dependencies"] == []
        assert dependency_check.details["incomplete_dependencies"] == []
        assert dependency_check.details["unknown_dependencies"] == []


def test_multiple_dependencies_and_backticks_are_parsed(tmp_path, monkeypatch):
    _setup_repo(tmp_path, completed_tasks={"T001", "T002", "T003", "T004"})
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)

    result = preflight.run_preflight("T005", version_info=(3, 11, 0))

    dependency_check = _dependency_check(result)
    assert result.exit_code == 0
    assert result.task_id == "T005"
    assert dependency_check.details["declared_dependencies"] == ["T001", "T002"]
    assert dependency_check.details["completed_dependencies"] == ["T001", "T002"]
    assert dependency_check.details["incomplete_dependencies"] == []
    assert dependency_check.details["unknown_dependencies"] == []


def test_unknown_dependency_causes_fail(tmp_path, monkeypatch, capsys):
    _setup_repo(tmp_path, dependency_overrides={"T006": "T999"})
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)
    monkeypatch.setattr(preflight.sys, "version_info", (3, 11, 0))

    exit_code = preflight.main(["--selector", "T006", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["status"] == "FAIL"
    assert payload["task"] == "T006"
    assert payload["reason"] == "unknown dependency task"
    assert payload["unknown_dependencies"] == ["T999"]
    assert payload["incomplete_dependencies"] == []


def test_baseline_not_created_on_dependency_failure(tmp_path, monkeypatch):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)

    result = preflight.run_preflight("T006", version_info=(3, 11, 0))

    assert result.exit_code == 1
    assert result.status == "FAIL"
    assert result.reason == "task dependencies are incomplete"
    assert result.baseline_path is None
    assert not (tmp_path / ".specify" / "runtime" / "task-runs" / "T006" / "baseline.json").exists()


def test_metadata_findings_still_block_task(tmp_path, monkeypatch, capsys):
    _setup_repo(tmp_path, completed_tasks={"T001", "T002", "T003", "T004", "T005"})
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)
    monkeypatch.setattr(preflight.sys, "version_info", (3, 11, 0))
    monkeypatch.setattr(
        preflight.repository_checks,
        "task_metadata",
        lambda tasks: [
            {
                "task": "T045",
                "reason": "metadata finding",
            }
        ],
    )

    exit_code = preflight.main(["--selector", "T045", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["status"] == "FAIL"
    assert payload["reason"] == "task T045 has outstanding metadata findings"
    assert payload.get("task", "") == ""


def test_completed_task_is_rejected(tmp_path, monkeypatch, capsys):
    _setup_repo(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)
    monkeypatch.setattr(preflight.sys, "version_info", (3, 11, 0))

    exit_code = preflight.main(["--selector", "T001", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["status"] == "FAIL"
    assert payload["reason"] == "task T001 is already completed"


def test_manifest_order_remains_source_of_selection(tmp_path, monkeypatch):
    _setup_repo(tmp_path, completed_tasks={"T001", "T002", "T003", "T004", "T005", "T045"})
    _patch_context(monkeypatch, tmp_path)
    _patch_successful_runtime(monkeypatch)

    result = preflight.run_preflight("next", version_info=(3, 11, 0))

    assert result.exit_code == 0
    assert result.task_id == "T046"

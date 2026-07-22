from pathlib import Path
import subprocess

import pytest
import yaml

from app.tooling import workstream_validation
from app.tooling.workstream_validation import (
    validate_active_epic,
    validate_close_preconditions,
    validate_manifests,
    validate_task_epic_consistency,
)


ROOT = Path(__file__).resolve().parents[4]
FEATURE_PATH = "specs/001-ai-content-studio"


def put(directory, name, text):
    (directory / name).write_text(text, encoding="utf-8")


def task_block(identifier="T001", epic_id="E001", milestone_id="M001", dependencies="None"):
    return (
        f"- [ ] {identifier} Task\n"
        f"Milestone: {milestone_id}\n"
        f"Epic: {epic_id}\n"
        f"Dependencies: {dependencies}\n"
    )


def task_consistency_block(identifier="T001", epic_id="E001", milestone_id="M001", dependencies="None"):
    return task_block(identifier, epic_id, milestone_id, dependencies)


def milestone(
    identifier="M001",
    title="Test milestone",
    status="active",
    goal="Test goal",
    epics=("E001",),
    completion=("Tests pass",),
):
    epics_text = "".join(f"  - {epic}\n" for epic in epics)
    completion_text = "".join(f"  - {item}\n" for item in completion)
    return (
        f"id: {identifier}\n"
        f"title: {title}\n"
        f"status: {status}\n"
        f"goal: {goal}\n"
        "epics:\n"
        f"{epics_text}"
        "completion_criteria:\n"
        f"{completion_text}"
    )


def _write_workstreams(directory: Path, manifests: dict[str, str]) -> None:
    for name, text in manifests.items():
        put(directory, name, text)


def epic(
    identifier="E001",
    milestone_id="M001",
    status="active",
    risk="low",
    branch="epic/E001",
    base="master",
    tasks=None,
    dependency="",
    pr_policy=None,
    commit_policy=None,
    feature=FEATURE_PATH,
    required_checks=None,
):
    if tasks is None:
        tasks = ["T001"]
    depends_on = [dependency] if dependency else []
    if pr_policy is None:
        pr_policy = {
            "one_pr_per_epic": True,
            "merge_requires_human": True,
            "auto_merge": False,
        }
    if commit_policy is None:
        commit_policy = {
            "one_commit_per_task": True,
            "commit_requires_human": True,
            "auto_commit": False,
        }
    if required_checks is None:
        required_checks = ["python -m pytest", "git --no-pager diff --check"]

    manifest = {
        "id": identifier,
        "title": "Test epic",
        "milestone": milestone_id,
        "feature": feature,
        "base_branch": base,
        "branch": branch,
        "status": status,
        "risk": risk,
        "depends_on": depends_on,
        "required_checks": required_checks,
        "pr_policy": pr_policy,
        "commit_policy": commit_policy,
    }
    if isinstance(tasks, str):
        tasks_block = f"tasks:\n{tasks}\n"
        return yaml.safe_dump(manifest, sort_keys=False) + tasks_block
    manifest["tasks"] = tasks
    return yaml.safe_dump(manifest, sort_keys=False)


def _patch_repo_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(workstream_validation, "ROOT", tmp_path)
    monkeypatch.setattr(workstream_validation, "DEFAULT_DIRECTORY", tmp_path / ".specify" / "workstreams")
    monkeypatch.setattr(workstream_validation, "ACTIVE_EPIC_FILE", tmp_path / ".specify" / "runtime" / "active-epic")
    monkeypatch.setattr(workstream_validation, "TASKS_FILE", tmp_path / "specs" / "001-ai-content-studio" / "tasks.md")


def _create_feature_tree(
    tmp_path: Path,
    *,
    include_spec: bool = True,
    include_plan: bool = True,
    include_tasks: bool = True,
    feature: str = FEATURE_PATH,
) -> Path:
    feature_dir = tmp_path / feature
    feature_dir.mkdir(parents=True, exist_ok=True)
    if include_spec:
        (feature_dir / "spec.md").write_text("# Spec\n", encoding="utf-8")
    if include_plan:
        (feature_dir / "plan.md").write_text("# Plan\n", encoding="utf-8")
    if include_tasks:
        (feature_dir / "tasks.md").write_text("- [ ] T001 Task\n", encoding="utf-8")
    return feature_dir


def test_valid_milestone_and_epic(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic())
    assert validate_manifests(tmp_path) == []


def test_missing_milestone(tmp_path):
    put(tmp_path, "E001.yml", epic(milestone_id="M999"))
    assert any("unknown milestone" in error for error in validate_manifests(tmp_path))


def test_epic_missing_from_milestone_is_invalid(tmp_path):
    put(tmp_path, "M002.yml", milestone(identifier="M002", epics=("E004",), completion=("Tests pass",)))
    put(tmp_path, "E004.yml", epic(identifier="E004", milestone_id="M002"))
    put(tmp_path, "E005.yml", epic(identifier="E005", milestone_id="M002"))
    errors = validate_manifests(tmp_path)
    assert any("epic E005 is not listed by milestone M002" in error for error in errors)


def test_milestone_listing_epic_with_other_milestone_is_invalid(tmp_path):
    put(tmp_path, "M002.yml", milestone(identifier="M002", epics=("E004",), completion=("Tests pass",)))
    put(tmp_path, "M003.yml", milestone(identifier="M003", epics=("E006",), completion=("Tests pass",)))
    put(tmp_path, "E004.yml", epic(identifier="E004", milestone_id="M002"))
    put(tmp_path, "E006.yml", epic(identifier="E006", milestone_id="M002"))
    errors = validate_manifests(tmp_path)
    assert any("epic E006 points to milestone 'M002', expected M003" in error for error in errors)


def test_duplicate_task(tmp_path):
    put(tmp_path, "M001.yml", milestone(epics=("E001", "E002"), completion=("Tests pass",)))
    put(tmp_path, "E001.yml", epic())
    put(tmp_path, "E002.yml", epic(identifier="E002"))
    assert any("belongs to multiple epics" in error for error in validate_manifests(tmp_path))


def test_duplicate_manifest_id(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "M001-copy.yml", milestone())
    assert any("duplicate manifest id" in error for error in validate_manifests(tmp_path))


def test_invalid_task_id(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(tasks="  - TASK-1"))
    assert any("invalid task id" in error for error in validate_manifests(tmp_path))


def test_valid_task_id_formats_are_accepted(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(tasks=["T001", "T999", "T006A", "T006Z"]))
    assert validate_manifests(tmp_path) == []


def test_invalid_task_id_formats_are_rejected(tmp_path):
    for invalid in ("t006", "T006AA", "T0001", "TASK-006"):
        put(tmp_path, "M001.yml", milestone())
        put(tmp_path, "E001.yml", epic(tasks=[invalid]))
        errors = validate_manifests(tmp_path)
        assert any("invalid task id" in error for error in errors)
        assert any(invalid in error for error in errors)


def test_invalid_branch(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(branch="master"))
    assert any("branch must differ" in error for error in validate_manifests(tmp_path))


def test_unknown_dependency(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(dependency="E999"))
    assert any("unknown epic dependency" in error for error in validate_manifests(tmp_path))


def test_dependency_cycle(tmp_path):
    put(tmp_path, "M001.yml", milestone(epics=("E001", "E002"), completion=("Tests pass",)))
    put(tmp_path, "E001.yml", epic(dependency="E002"))
    put(tmp_path, "E002.yml", epic(identifier="E002", dependency="E001"))
    assert any("dependency cycle" in error for error in validate_manifests(tmp_path))


def test_invalid_status_and_risk(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(status="unknown", risk="extreme"))
    errors = validate_manifests(tmp_path)
    assert any("invalid status" in error for error in errors)
    assert any("invalid risk" in error for error in errors)


def test_valid_policies_are_accepted(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(
        tmp_path,
        "E001.yml",
        epic(
            pr_policy={
                "one_pr_per_epic": True,
                "merge_requires_human": True,
                "auto_merge": False,
            },
            commit_policy={
                "one_commit_per_task": True,
                "commit_requires_human": True,
                "auto_commit": False,
            },
        ),
    )
    assert validate_manifests(tmp_path) == []


def test_feature_validation_accepts_existing_feature(tmp_path, monkeypatch):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _create_feature_tree(tmp_path)
    put(workstreams, "M001.yml", milestone())
    put(tmp_path, "workstreams/E001.yml", epic())
    _patch_repo_root(monkeypatch, tmp_path)
    assert validate_manifests(workstreams) == []


def test_feature_validation_rejects_missing_feature_directory(tmp_path, monkeypatch):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    put(workstreams, "M001.yml", milestone())
    put(workstreams, "E001.yml", epic())
    _patch_repo_root(monkeypatch, tmp_path)
    errors = validate_manifests(workstreams)
    assert any("feature directory does not exist" in error for error in errors)


def test_feature_validation_rejects_path_traversal(tmp_path, monkeypatch):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _create_feature_tree(tmp_path)
    put(workstreams, "M001.yml", milestone())
    put(
        workstreams,
        "E001.yml",
        epic(feature="../specs/001-ai-content-studio"),
    )
    _patch_repo_root(monkeypatch, tmp_path)
    errors = validate_manifests(workstreams)
    assert any("feature must be 'specs/001-ai-content-studio'" in error for error in errors)


def test_feature_validation_rejects_missing_spec_md(tmp_path, monkeypatch):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _create_feature_tree(tmp_path, include_spec=False)
    put(workstreams, "M001.yml", milestone())
    put(workstreams, "E001.yml", epic())
    _patch_repo_root(monkeypatch, tmp_path)
    errors = validate_manifests(workstreams)
    assert any("missing spec.md" in error for error in errors)


def test_missing_policies_are_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(
        tmp_path,
        "E001.yml",
        "\n".join(
            [
                "id: E001",
                "title: Test epic",
                "milestone: M001",
                "feature: specs/001-ai-content-studio",
                "base_branch: master",
                "branch: epic/E001",
                "status: active",
                "risk: low",
                "depends_on: []",
                "tasks:",
                "  - T001",
                "required_checks:",
                "  - python -m pytest",
            ]
        ),
    )
    errors = validate_manifests(tmp_path)
    assert any("missing required field pr_policy" in error for error in errors)
    assert any("missing required field commit_policy" in error for error in errors)


def test_missing_policy_field_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(
        tmp_path,
        "E001.yml",
        epic(
            pr_policy={
                "one_pr_per_epic": True,
                "merge_requires_human": True,
            },
        ),
    )
    errors = validate_manifests(tmp_path)
    assert any("pr_policy missing required field auto_merge" in error for error in errors)


def test_required_checks_as_string_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(required_checks="python -m pytest"))
    errors = validate_manifests(tmp_path)
    assert any("required_checks must be a list" in error for error in errors)


def test_required_checks_empty_list_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(required_checks=[]))
    errors = validate_manifests(tmp_path)
    assert any("required_checks must be a non-empty list" in error for error in errors)


def test_required_checks_empty_element_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(required_checks=[""]))
    errors = validate_manifests(tmp_path)
    assert any("must be a non-empty string" in error for error in errors)


def test_required_checks_duplicate_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(required_checks=["python -m pytest", "python -m pytest"]))
    errors = validate_manifests(tmp_path)
    assert any("duplicate command" in error for error in errors)


def test_required_checks_semicolon_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(required_checks=["python -m pytest; git status"]))
    errors = validate_manifests(tmp_path)
    assert any("forbidden shell operator" in error for error in errors)


def test_required_checks_double_ampersand_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(required_checks=["python -m pytest && git status"]))
    errors = validate_manifests(tmp_path)
    assert any("forbidden shell operator" in error for error in errors)


def test_required_checks_pipe_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(required_checks=["python -m pytest | tee output.txt"]))
    errors = validate_manifests(tmp_path)
    assert any("forbidden shell operator" in error for error in errors)


def test_required_checks_forbidden_git_command_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(required_checks=["git push origin main"]))
    errors = validate_manifests(tmp_path)
    assert any("forbidden git command" in error for error in errors)


def test_required_checks_valid_pytest_and_git_diff_check(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(
        tmp_path,
        "E001.yml",
        epic(required_checks=["python -m pytest", "git --no-pager diff --check"]),
    )
    assert validate_manifests(tmp_path) == []


def test_policy_with_bad_type_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(
        tmp_path,
        "E001.yml",
        epic(
            pr_policy="human",
        ),
    )
    errors = validate_manifests(tmp_path)
    assert any("pr_policy must be a mapping" in error for error in errors)


def test_auto_merge_true_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(
        tmp_path,
        "E001.yml",
        epic(
            pr_policy={
                "one_pr_per_epic": True,
                "merge_requires_human": True,
                "auto_merge": True,
            },
        ),
    )
    errors = validate_manifests(tmp_path)
    assert any("pr_policy.auto_merge must be false" in error for error in errors)


def test_auto_commit_true_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(
        tmp_path,
        "E001.yml",
        epic(
            commit_policy={
                "one_commit_per_task": True,
                "commit_requires_human": True,
                "auto_commit": True,
            },
        ),
    )
    errors = validate_manifests(tmp_path)
    assert any("commit_policy.auto_commit must be false" in error for error in errors)


def test_merge_requires_human_false_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(
        tmp_path,
        "E001.yml",
        epic(
            pr_policy={
                "one_pr_per_epic": True,
                "merge_requires_human": False,
                "auto_merge": False,
            },
        ),
    )
    errors = validate_manifests(tmp_path)
    assert any("pr_policy.merge_requires_human must be true" in error for error in errors)


def test_commit_requires_human_false_is_invalid(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(
        tmp_path,
        "E001.yml",
        epic(
            commit_policy={
                "one_commit_per_task": True,
                "commit_requires_human": False,
                "auto_commit": False,
            },
        ),
    )
    errors = validate_manifests(tmp_path)
    assert any("commit_policy.commit_requires_human must be true" in error for error in errors)


def test_pr_and_commit_policies_are_validated_separately(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(
        tmp_path,
        "E001.yml",
        epic(
            pr_policy={
                "one_pr_per_epic": True,
                "merge_requires_human": True,
                "auto_merge": False,
            },
            commit_policy={
                "one_commit_per_task": True,
                "commit_requires_human": True,
                "auto_commit": False,
            },
        ),
    )
    assert validate_manifests(tmp_path) == []


def test_active_epic_requires_tasks(tmp_path):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic(tasks=[]))
    assert any("non-empty tasks" in error for error in validate_manifests(tmp_path))


def test_repository_manifests_are_valid():
    assert validate_manifests(ROOT / ".specify" / "workstreams") == []


def test_guard_pipeline_runs_all_checks_in_order(monkeypatch, tmp_path):
    calls = []

    def fake_manifests(directory=object()):
        calls.append("manifests")
        return ["manifest error"]

    def fake_task_consistency(tasks_file=object(), directory=object()):
        calls.append("tasks")
        return ["task error"]

    def fake_active_epic(task_selector="next", runtime_file=object(), directory=object()):
        calls.append("active")
        return ["active error"]

    monkeypatch.setattr(workstream_validation, "validate_manifests", fake_manifests)
    monkeypatch.setattr(workstream_validation, "validate_task_epic_consistency", fake_task_consistency)
    monkeypatch.setattr(workstream_validation, "validate_active_epic", fake_active_epic)
    assert workstream_validation.validate_guard("T001", tmp_path / "active-epic", tmp_path, tmp_path / "tasks.md") == [
        "manifest error",
        "task error",
        "active error",
    ]
    assert calls == ["manifests", "tasks", "active"]


def test_guard_accepts_valid_manifest_task_and_branch(tmp_path, monkeypatch):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    runtime = tmp_path / "active-epic"
    tasks = tmp_path / "tasks.md"
    put(tmp_path, "workstreams/M001.yml", milestone(epics=("E001",), completion=("Tests pass",)))
    put(workstreams, "E001.yml", epic(tasks=["T006A"]))
    tasks.write_text(task_block("T006A", "E001"), encoding="utf-8")
    runtime.write_text("E001\n", encoding="utf-8")
    monkeypatch.setattr(
        workstream_validation.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"stdout": "epic/E001\n", "returncode": 0})(),
    )
    assert workstream_validation.validate_guard("T006A", runtime, workstreams, tasks) == []


def test_guard_rejects_lowercase_task_selector_without_normalizing(tmp_path, monkeypatch):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    runtime = tmp_path / "active-epic"
    tasks = tmp_path / "tasks.md"
    put(tmp_path, "workstreams/M001.yml", milestone(epics=("E001",), completion=("Tests pass",)))
    put(workstreams, "E001.yml", epic(tasks=["T006A"]))
    tasks.write_text(task_block("T006A", "E001"), encoding="utf-8")
    runtime.write_text("E001\n", encoding="utf-8")
    monkeypatch.setattr(
        workstream_validation.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"stdout": "epic/E001\n", "returncode": 0})(),
    )
    errors = workstream_validation.validate_guard("t006", runtime, workstreams, tasks)
    assert any("invalid task selector" in error for error in errors)


def test_guard_reports_invalid_manifest_with_valid_branch(tmp_path, monkeypatch):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    runtime = tmp_path / "active-epic"
    tasks = tmp_path / "tasks.md"
    put(workstreams, "M001.yml", milestone(epics=("E001",), completion=("Tests pass",)))
    put(workstreams, "E001.yml", epic(status="unknown"))
    tasks.write_text(task_block("T001", "E001"), encoding="utf-8")
    runtime.write_text("E001\n", encoding="utf-8")
    monkeypatch.setattr(
        workstream_validation.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"stdout": "epic/E001\n", "returncode": 0})(),
    )
    errors = workstream_validation.validate_guard("next", runtime, workstreams, tasks)
    assert any("invalid status" in error for error in errors)


def test_guard_reports_task_outside_active_epic(tmp_path, monkeypatch):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    runtime = tmp_path / "active-epic"
    tasks = tmp_path / "tasks.md"
    put(workstreams, "M001.yml", milestone(epics=("E001", "E002"), completion=("Tests pass",)))
    put(workstreams, "E001.yml", epic(tasks=["T001"]))
    put(workstreams, "E002.yml", epic(identifier="E002", tasks=["T999"]))
    tasks.write_text(task_block("T001", "E001") + "\n" + task_block("T999", "E002"), encoding="utf-8")
    runtime.write_text("E001\n", encoding="utf-8")
    monkeypatch.setattr(
        workstream_validation.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"stdout": "epic/E001\n", "returncode": 0})(),
    )
    errors = workstream_validation.validate_guard("T999", runtime, workstreams, tasks)
    assert any("task does not belong to the active epic" in error for error in errors)


def test_guard_reports_missing_active_epic(tmp_path, monkeypatch):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    runtime = tmp_path / "active-epic"
    tasks = tmp_path / "tasks.md"
    put(workstreams, "M001.yml", milestone(epics=("E001",), completion=("Tests pass",)))
    put(workstreams, "E001.yml", epic())
    tasks.write_text(task_block("T001", "E001"), encoding="utf-8")
    monkeypatch.setattr(
        workstream_validation.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"stdout": "epic/E001\n", "returncode": 0})(),
    )
    errors = workstream_validation.validate_guard("next", runtime, workstreams, tasks)
    assert any("active epic does not exist" in error for error in errors)


def test_guard_reports_incomplete_dependency(tmp_path, monkeypatch):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    runtime = tmp_path / "active-epic"
    tasks = tmp_path / "tasks.md"
    put(workstreams, "M001.yml", milestone(epics=("E001", "E002"), completion=("Tests pass",)))
    put(workstreams, "E001.yml", epic(identifier="E001", tasks=["T001"]))
    put(workstreams, "E002.yml", epic(identifier="E002", dependency="E001", tasks=["T002"]))
    tasks.write_text(task_block("T001", "E001") + "\n" + task_block("T002", "E002"), encoding="utf-8")
    runtime.write_text("E002\n", encoding="utf-8")
    monkeypatch.setattr(
        workstream_validation.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"stdout": "epic/E002\n", "returncode": 0})(),
    )
    errors = workstream_validation.validate_guard("next", runtime, workstreams, tasks)
    assert any("dependency E001 is not completed" in error for error in errors)


def test_guard_reports_git_timeout(tmp_path, monkeypatch):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    runtime = tmp_path / "active-epic"
    tasks = tmp_path / "tasks.md"
    put(workstreams, "M001.yml", milestone(epics=("E001",), completion=("Tests pass",)))
    put(workstreams, "E001.yml", epic())
    tasks.write_text(task_block("T001", "E001"), encoding="utf-8")
    runtime.write_text("E001\n", encoding="utf-8")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 20)

    monkeypatch.setattr(workstream_validation.subprocess, "run", fake_run)
    errors = workstream_validation.validate_guard("next", runtime, workstreams, tasks)
    assert any("timed out after 20 seconds" in error for error in errors)


def test_guard_reports_detached_head(tmp_path, monkeypatch):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    runtime = tmp_path / "active-epic"
    tasks = tmp_path / "tasks.md"
    put(workstreams, "M001.yml", milestone(epics=("E001",), completion=("Tests pass",)))
    put(workstreams, "E001.yml", epic())
    tasks.write_text(task_block("T001", "E001"), encoding="utf-8")
    runtime.write_text("E001\n", encoding="utf-8")

    def fake_run(*args, **kwargs):
        return type("Result", (), {"stdout": "\n", "returncode": 0})()

    monkeypatch.setattr(workstream_validation.subprocess, "run", fake_run)
    errors = workstream_validation.validate_guard("next", runtime, workstreams, tasks)
    assert any("detached HEAD" in error for error in errors)


def test_guard_requires_active_epic(tmp_path, monkeypatch):
    monkeypatch.setattr(workstream_validation.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"stdout": "epic/E001\n"})())
    errors = validate_active_epic("next", tmp_path / "active-epic", tmp_path)
    assert any("active epic does not exist" in error for error in errors)


def test_guard_rejects_task_outside_active_epic(tmp_path, monkeypatch):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic())
    active = tmp_path / "active-epic"
    active.write_text("E001\n", encoding="utf-8")
    monkeypatch.setattr(workstream_validation.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"stdout": "epic/E001\n"})())
    errors = validate_active_epic("T999", active, tmp_path)
    assert any("task does not belong" in error for error in errors)


def test_guard_rejects_uncompleted_dependency(tmp_path, monkeypatch):
    put(tmp_path, "M001.yml", milestone())
    put(tmp_path, "E001.yml", epic())
    put(tmp_path, "E002.yml", epic(identifier="E002", dependency="E001"))
    active = tmp_path / "active-epic"
    active.write_text("E002\n", encoding="utf-8")
    monkeypatch.setattr(workstream_validation.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"stdout": "epic/E002\n"})())
    errors = validate_active_epic("next", active, tmp_path)
    assert any("dependency E001 is not completed" in error for error in errors)


def test_task_epic_consistency_accepts_matching_repo(tmp_path, monkeypatch):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    tasks = tmp_path / "tasks.md"
    runtime = tmp_path / "active-epic"
    _write_workstreams(
        workstreams,
        {
            "M001.yml": milestone(identifier="M001", epics=("E001",), completion=("Tests pass",)),
            "M002.yml": milestone(identifier="M002", epics=("E002",), completion=("Tests pass",)),
            "E001.yml": epic(identifier="E001", milestone_id="M001", tasks=["T001"]),
            "E002.yml": epic(identifier="E002", milestone_id="M002", tasks=["T002"]),
        },
    )
    tasks.write_text(
        task_consistency_block("T001", "E001", "M001")
        + "\n"
        + task_consistency_block("T002", "E002", "M002"),
        encoding="utf-8",
    )
    runtime.write_text("E001\n", encoding="utf-8")
    monkeypatch.setattr(
        workstream_validation.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"stdout": "epic/E001\n", "returncode": 0})(),
    )
    assert validate_task_epic_consistency(tasks, workstreams) == []
    assert workstream_validation.validate_guard("next", runtime, workstreams, tasks) == []


def test_task_epic_consistency_reports_wrong_milestone(tmp_path):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    tasks = tmp_path / "tasks.md"
    _write_workstreams(
        workstreams,
        {
            "M001.yml": milestone(identifier="M001", epics=("E001",), completion=("Tests pass",)),
            "E001.yml": epic(identifier="E001", milestone_id="M001", tasks=["T001"]),
        },
    )
    tasks.write_text(task_consistency_block("T001", "E001", "M002"), encoding="utf-8")
    errors = validate_task_epic_consistency(tasks, workstreams)
    assert any("task.Milestone does not match the epic milestone" in error for error in errors)


def test_task_epic_consistency_reports_wrong_epic(tmp_path):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    tasks = tmp_path / "tasks.md"
    _write_workstreams(
        workstreams,
        {
            "M001.yml": milestone(identifier="M001", epics=("E001", "E002"), completion=("Tests pass",)),
            "M002.yml": milestone(identifier="M002", epics=("E002",), completion=("Tests pass",)),
            "E001.yml": epic(identifier="E001", milestone_id="M001", tasks=["T001"]),
            "E002.yml": epic(identifier="E002", milestone_id="M002", tasks=["T002"]),
        },
    )
    tasks.write_text(task_consistency_block("T001", "E002", "M001"), encoding="utf-8")
    errors = validate_task_epic_consistency(tasks, workstreams)
    assert any("listed in the wrong epic manifest" in error for error in errors)


def test_task_epic_consistency_reports_task_omitted_from_manifest_and_missing_task(tmp_path):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    tasks = tmp_path / "tasks.md"
    _write_workstreams(
        workstreams,
        {
            "M001.yml": milestone(identifier="M001", epics=("E001",), completion=("Tests pass",)),
            "E001.yml": epic(identifier="E001", milestone_id="M001", tasks=["T999"]),
        },
    )
    tasks.write_text(task_consistency_block("T001", "E001", "M001"), encoding="utf-8")
    errors = validate_task_epic_consistency(tasks, workstreams)
    assert any("omitted from all epic manifests" in error for error in errors)
    assert any("not listed by its epic manifest" in error for error in errors)
    assert any("manifest references a task that does not exist in tasks.md" in error for error in errors)


def test_task_epic_consistency_reports_multiple_manifest_ownership(tmp_path):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    tasks = tmp_path / "tasks.md"
    _write_workstreams(
        workstreams,
        {
            "M001.yml": milestone(identifier="M001", epics=("E001", "E002"), completion=("Tests pass",)),
            "M002.yml": milestone(identifier="M002", epics=("E002",), completion=("Tests pass",)),
            "E001.yml": epic(identifier="E001", milestone_id="M001", tasks=["T001"]),
            "E002.yml": epic(identifier="E002", milestone_id="M002", tasks=["T001"]),
        },
    )
    tasks.write_text(task_consistency_block("T001", "E001", "M001"), encoding="utf-8")
    errors = validate_task_epic_consistency(tasks, workstreams)
    assert any("present in multiple epic manifests" in error for error in errors)


def test_task_epic_consistency_reports_unknown_epic_and_milestone(tmp_path):
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    tasks = tmp_path / "tasks.md"
    _write_workstreams(
        workstreams,
        {
            "M001.yml": milestone(identifier="M001", epics=("E001",), completion=("Tests pass",)),
            "E001.yml": epic(identifier="E001", milestone_id="M001", tasks=["T002"]),
        },
    )
    tasks.write_text(task_consistency_block("T001", "E999", "M999"), encoding="utf-8")
    errors = validate_task_epic_consistency(tasks, workstreams)
    assert any("unknown epic" in error for error in errors)
    assert any("unknown milestone" in error for error in errors)


def test_close_rejects_unmerged_epic():
    errors = validate_close_preconditions("review", False, True, False)
    assert "merge evidence is required before closing the epic" in errors


def test_workstream_validation_import_smoke():
    from app.tooling import workstream_validation as imported_module

    assert imported_module.validate_manifests is workstream_validation.validate_manifests

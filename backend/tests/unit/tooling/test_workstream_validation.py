from pathlib import Path

from app.tooling import workstream_validation
from app.tooling.workstream_validation import (
    validate_active_epic,
    validate_close_preconditions,
    validate_manifests,
)


ROOT = Path(__file__).resolve().parents[4]
MILESTONE = ("id: M001\n" "title: Test milestone\n" "status: active\n" "goal: Test goal\n" "epics:\n" "  - E001\n" "completion_criteria:\n" "  - Tests pass\n")


def put(directory, name, text):
    (directory / name).write_text(text, encoding="utf-8")


def epic(identifier="E001", milestone="M001", status="active", risk="low", branch="epic/E001", base="master", tasks="  - T001", dependency=""):
    dependency_line = f"  - {dependency}\n" if dependency else ""
    return (f"id: {identifier}\n" f"title: Test epic\n" f"milestone: {milestone}\n" "feature: specs/001-ai-content-studio\n" f"base_branch: {base}\n" f"branch: {branch}\n" f"status: {status}\n" f"risk: {risk}\n" "depends_on: []\n" f"{dependency_line}" "tasks:\n" f"{tasks}\n" "required_checks:\n" "  - python -m pytest\n" "pr_policy: human\n" "commit_policy: human\n")


def test_valid_milestone_and_epic(tmp_path):
    put(tmp_path, "M001.yml", MILESTONE)
    put(tmp_path, "E001.yml", epic())
    assert validate_manifests(tmp_path) == []


def test_missing_milestone(tmp_path):
    put(tmp_path, "E001.yml", epic(milestone="M999"))
    assert any("unknown milestone" in error for error in validate_manifests(tmp_path))


def test_duplicate_task(tmp_path):
    put(tmp_path, "M001.yml", MILESTONE + "  - E002\n")
    put(tmp_path, "E001.yml", epic())
    put(tmp_path, "E002.yml", epic(identifier="E002"))
    assert any("belongs to multiple epics" in error for error in validate_manifests(tmp_path))


def test_duplicate_manifest_id(tmp_path):
    put(tmp_path, "M001.yml", MILESTONE)
    put(tmp_path, "M001-copy.yml", MILESTONE)
    assert any("duplicate manifest id" in error for error in validate_manifests(tmp_path))


def test_invalid_task_id(tmp_path):
    put(tmp_path, "M001.yml", MILESTONE)
    put(tmp_path, "E001.yml", epic(tasks="  - TASK-1"))
    assert any("invalid task id" in error for error in validate_manifests(tmp_path))


def test_invalid_branch(tmp_path):
    put(tmp_path, "M001.yml", MILESTONE)
    put(tmp_path, "E001.yml", epic(branch="master"))
    assert any("branch must differ" in error for error in validate_manifests(tmp_path))


def test_unknown_dependency(tmp_path):
    put(tmp_path, "M001.yml", MILESTONE)
    put(tmp_path, "E001.yml", epic(dependency="E999"))
    assert any("unknown epic dependency" in error for error in validate_manifests(tmp_path))


def test_dependency_cycle(tmp_path):
    put(tmp_path, "M001.yml", MILESTONE + "  - E002\n")
    put(tmp_path, "E001.yml", epic(dependency="E002"))
    put(tmp_path, "E002.yml", epic(identifier="E002", dependency="E001"))
    assert any("dependency cycle" in error for error in validate_manifests(tmp_path))


def test_invalid_status_and_risk(tmp_path):
    put(tmp_path, "M001.yml", MILESTONE)
    put(tmp_path, "E001.yml", epic(status="unknown", risk="extreme"))
    errors = validate_manifests(tmp_path)
    assert any("invalid status" in error for error in errors)
    assert any("invalid risk" in error for error in errors)


def test_active_epic_requires_tasks(tmp_path):
    put(tmp_path, "M001.yml", MILESTONE)
    put(tmp_path, "E001.yml", epic(tasks=""))
    assert any("non-empty tasks" in error for error in validate_manifests(tmp_path))


def test_repository_manifests_are_valid():
    assert validate_manifests(ROOT / ".specify" / "workstreams") == []


def test_guard_requires_active_epic(tmp_path, monkeypatch):
    monkeypatch.setattr(workstream_validation.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"stdout": "epic/E001\n"})())
    errors = validate_active_epic("next", tmp_path / "active-epic", tmp_path)
    assert any("active epic does not exist" in error for error in errors)


def test_guard_rejects_task_outside_active_epic(tmp_path, monkeypatch):
    put(tmp_path, "M001.yml", MILESTONE)
    put(tmp_path, "E001.yml", epic())
    active = tmp_path / "active-epic"
    active.write_text("E001\n", encoding="utf-8")
    monkeypatch.setattr(workstream_validation.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"stdout": "epic/E001\n"})())
    errors = validate_active_epic("T999", active, tmp_path)
    assert any("task does not belong" in error for error in errors)


def test_guard_rejects_uncompleted_dependency(tmp_path, monkeypatch):
    put(tmp_path, "M001.yml", MILESTONE)
    put(tmp_path, "E001.yml", epic())
    put(tmp_path, "E002.yml", epic(identifier="E002", dependency="E001"))
    active = tmp_path / "active-epic"
    active.write_text("E002\n", encoding="utf-8")
    monkeypatch.setattr(workstream_validation.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"stdout": "epic/E002\n"})())
    errors = validate_active_epic("next", active, tmp_path)
    assert any("dependency E001 is not completed" in error for error in errors)


def test_close_rejects_unmerged_epic():
    errors = validate_close_preconditions("review", False, True, False)
    assert "merge evidence is required before closing the epic" in errors

from __future__ import annotations

from pathlib import Path

import pytest

from app.tooling.local_autopilot import workstreams


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _milestone(identifier: str, epics: list[str]) -> str:
    return "\n".join(
        [
            f"id: {identifier}",
            f"title: Milestone {identifier}",
            "status: active",
            "goal: goal",
            "epics:",
            *[f"  - {epic}" for epic in epics],
            "completion_criteria:",
            "  - done",
            "",
        ]
    )


def _epic(identifier: str, milestone_id: str, tasks: list[str], *, status: str = "planned", depends_on: list[str] | None = None) -> str:
    depends_on = depends_on or []
    return "\n".join(
        [
            f"id: {identifier}",
            f"title: Epic {identifier}",
            f"milestone: {milestone_id}",
            "feature: specs/001-ai-content-studio",
            "base_branch: master",
            f"branch: epic/{identifier}",
            f"status: {status}",
            "risk: low",
            "depends_on:",
            *[f"  - {epic}" for epic in depends_on],
            "tasks:",
            *[f"  - {task}" for task in tasks],
            "required_checks:",
            "  - python -m pytest",
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
    )


def _tasks(*blocks: str) -> str:
    return "\n".join(blocks) + "\n"


def _task(identifier: str, epic_id: str, milestone_id: str, *, completed: bool = False, dependencies: str = "None") -> str:
    checkbox = "X" if completed else " "
    return "\n".join(
        [
            f"- [{checkbox}] {identifier} Task {identifier}",
            f"Milestone: {milestone_id}",
            f"Epic: {epic_id}",
            "Risk: low",
            "Implementation files: none",
            "Test files: none",
            f"Dependencies: {dependencies}",
            "Validation commands: python -m pytest",
            "Acceptance criteria: behavior",
            "Test requirements: direct coverage",
        ]
    )


def _patch_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(workstreams, "ROOT", tmp_path)
    monkeypatch.setattr(workstreams, "DEFAULT_DIRECTORY", tmp_path / ".specify" / "workstreams")
    monkeypatch.setattr(workstreams, "DEFAULT_TASKS_FILE", tmp_path / "specs" / "001-ai-content-studio" / "tasks.md")


def test_list_and_get_milestones_and_epics(tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    directory = tmp_path / ".specify" / "workstreams"
    _write(directory / "M001.yml", _milestone("M001", ["E001", "E002"]))
    _write(directory / "E001.yml", _epic("E001", "M001", ["T001"]))
    _write(directory / "E002.yml", _epic("E002", "M001", ["T002"], status="active"))

    assert [item["id"] for item in workstreams.list_milestones(directory)] == ["M001"]
    assert [item["id"] for item in workstreams.list_epics(directory)] == ["E001", "E002"]
    assert [item["id"] for item in workstreams.list_epics(directory, milestone_id="M001")] == ["E001", "E002"]
    assert workstreams.get_milestone("M001", directory)["goal"] == "goal"
    assert workstreams.get_epic("E001", directory)["branch"] == "epic/E001"


def test_validate_dependencies_requires_completed_dependencies(tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    directory = tmp_path / ".specify" / "workstreams"
    _write(directory / "M001.yml", _milestone("M001", ["E001", "E002"]))
    _write(directory / "E001.yml", _epic("E001", "M001", ["T001"], status="completed"))
    _write(directory / "E002.yml", _epic("E002", "M001", ["T002"], depends_on=["E001"]))

    assert workstreams.validate_dependencies("E002", directory) == []
    _write(directory / "E001.yml", _epic("E001", "M001", ["T001"], status="active"))
    assert workstreams.validate_dependencies("E002", directory) == ["dependency E001 is not completed"]


def test_activate_epic_requires_human_authorization(tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    directory = tmp_path / ".specify" / "workstreams"
    path = directory / "E001.yml"
    _write(directory / "M001.yml", _milestone("M001", ["E001"]))
    _write(path, _epic("E001", "M001", ["T001"], status="planned"))

    with pytest.raises(ValueError, match="human authorization is required"):
        workstreams.activate_epic_with_human_authorization("E001", human_authorized=False, directory=directory)

    updated = workstreams.activate_epic_with_human_authorization("E001", human_authorized=True, directory=directory)
    assert updated["status"] == "active"
    assert "status: active" in path.read_text(encoding="utf-8")


def test_completed_epic_never_reactivates(tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    directory = tmp_path / ".specify" / "workstreams"
    _write(directory / "M001.yml", _milestone("M001", ["E001"]))
    _write(directory / "E001.yml", _epic("E001", "M001", ["T001"], status="completed"))

    with pytest.raises(ValueError, match="completed epics cannot be reactivated"):
        workstreams.activate_epic_with_human_authorization("E001", human_authorized=True, directory=directory)


def test_list_epic_tasks_and_dependency_order(tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    directory = tmp_path / ".specify" / "workstreams"
    tasks_file = tmp_path / "specs" / "001-ai-content-studio" / "tasks.md"
    _write(directory / "M001.yml", _milestone("M001", ["E002"]))
    _write(directory / "E002.yml", _epic("E002", "M001", ["T007", "T008"]))
    _write(
        tasks_file,
        _tasks(
            _task("T007", "E002", "M001", completed=False, dependencies="None"),
            _task("T008", "E002", "M001", completed=False, dependencies="T007"),
        ),
    )

    assert workstreams.list_epic_tasks("E002", tasks_file, directory) == ["T007", "T008"]
    assert workstreams.next_dependency_ready_task("E002", tasks_file=tasks_file, directory=directory) == "T007"

    _write(
        tasks_file,
        _tasks(
            _task("T007", "E002", "M001", completed=True, dependencies="None"),
            _task("T008", "E002", "M001", completed=False, dependencies="T007"),
        ),
    )
    assert workstreams.next_dependency_ready_task("E002", tasks_file=tasks_file, directory=directory) == "T008"
    assert workstreams.all_epic_tasks_complete("E002", tasks_file=tasks_file, directory=directory) is False


def test_next_ready_epic_for_milestone_uses_manifest_order_and_dependency_readiness(tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    directory = tmp_path / ".specify" / "workstreams"
    tasks_file = tmp_path / "specs" / "001-ai-content-studio" / "tasks.md"
    _write(directory / "M001.yml", _milestone("M001", ["E001", "E002"]))
    _write(directory / "E001.yml", _epic("E001", "M001", ["T001"], status="planned"))
    _write(directory / "E002.yml", _epic("E002", "M001", ["T002"], status="planned", depends_on=["E001"]))
    _write(tasks_file, _tasks(_task("T001", "E001", "M001", completed=False), _task("T002", "E002", "M001", completed=False)))

    assert workstreams.next_ready_epic_for_milestone("M001", tasks_file=tasks_file, directory=directory) == "E001"

    _write(directory / "E001.yml", _epic("E001", "M001", ["T001"], status="completed"))
    assert workstreams.next_ready_epic_for_milestone("M001", tasks_file=tasks_file, directory=directory) == "E002"

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.tooling import repository_checks as checks


def _write_tasks(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _write_workstream(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _full_task_block(identifier: str, epic_id: str, milestone_id: str, implementation: str, test_file: str, dependencies: str = "None") -> str:
    return (
        f"- [ ] {identifier} Implement task\n"
        f"Milestone: {milestone_id}\n"
        f"Epic: {epic_id}\n"
        "Risk: low\n"
        f"Implementation files: {implementation}\n"
        f"Test files: {test_file}\n"
        f"Dependencies: {dependencies}\n"
        "Validation commands: python -m pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n"
    )


def _full_milestone(identifier: str, epics: list[str]) -> str:
    epics_text = "".join(f"  - {epic}\n" for epic in epics)
    return (
        f"id: {identifier}\n"
        f"title: Milestone {identifier}\n"
        "status: planned\n"
        "goal: goal\n"
        "epics:\n"
        f"{epics_text}"
        "completion_criteria:\n"
        "  - Tests pass\n"
    )


def _full_epic(identifier: str, milestone_id: str, tasks: list[str], depends_on: list[str] | None = None) -> str:
    if depends_on is None:
        depends_on = []
    depends_text = "".join(f"  - {epic}\n" for epic in depends_on)
    tasks_text = "".join(f"  - {task}\n" for task in tasks)
    return (
        f"id: {identifier}\n"
        f"title: Epic {identifier}\n"
        f"milestone: {milestone_id}\n"
        "feature: specs/001-ai-content-studio\n"
        "base_branch: master\n"
        f"branch: epic/{identifier}\n"
        "status: planned\n"
        "risk: low\n"
        "depends_on:\n"
        f"{depends_text}"
        "tasks:\n"
        f"{tasks_text}"
        "required_checks:\n"
        "  - python -m pytest\n"
        "pr_policy:\n"
        "  one_pr_per_epic: true\n"
        "  merge_requires_human: true\n"
        "  auto_merge: false\n"
        "commit_policy:\n"
        "  one_commit_per_task: true\n"
        "  commit_requires_human: true\n"
        "  auto_commit: false\n"
    )


def test_current_task_metadata_is_clean():
    assert checks.task_metadata() == []


def test_task_metadata_filter_t006_with_valid_dependencies(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        _full_task_block("T004", "E001", "M001", "backend/app/domain/base.py", "backend/tests/unit/test_t004.py")
        + "\n"
        + _full_task_block("T005", "E001", "M001", "backend/app/domain/project.py", "backend/tests/unit/test_t005.py")
        + "\n"
        + _full_task_block("T045", "E001", "M001", "backend/tests/unit/test_t045_domain_primitives.py", "backend/tests/unit/test_t045_domain_primitives.py")
        + "\n"
        + _full_task_block("T046", "E001", "M001", "backend/tests/unit/test_t046_project_config_models.py", "backend/tests/unit/test_t046_project_config_models.py")
        + "\n"
        + _full_task_block("T006", "E001", "M001", "backend/app/domain/workflow_run.py", "backend/tests/unit/test_t006.py", "T004, T005, T045, T046"),
    )
    _write_workstream(workstreams / "M001.yml", _full_milestone("M001", ["E001"]))
    _write_workstream(workstreams / "E001.yml", _full_epic("E001", "M001", ["T004", "T005", "T045", "T046", "T006"]))
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    assert checks.task_metadata(["T006"]) == []


def test_task_metadata_filter_reports_unknown_dependency_for_selected_task(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        _full_task_block("T006", "E001", "M001", "backend/app/domain/workflow_run.py", "backend/tests/unit/test_t006.py", "T004"),
    )
    _write_workstream(workstreams / "M001.yml", _full_milestone("M001", ["E001"]))
    _write_workstream(workstreams / "E001.yml", _full_epic("E001", "M001", ["T006"]))
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    findings = checks.task_metadata(["T006"])
    assert any(finding["task"] == "T006" for finding in findings)
    assert any("unknown dependency task" in finding["reason"] for finding in findings)


def test_task_metadata_filter_keeps_selected_task_cycle(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        _full_task_block("T006", "E001", "M001", "backend/app/domain/workflow_run.py", "backend/tests/unit/test_t006.py", "T004")
        + "\n"
        + _full_task_block("T004", "E001", "M001", "backend/app/domain/base.py", "backend/tests/unit/test_t004.py", "T006"),
    )
    _write_workstream(workstreams / "M001.yml", _full_milestone("M001", ["E001"]))
    _write_workstream(workstreams / "E001.yml", _full_epic("E001", "M001", ["T006", "T004"]))
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    findings = checks.task_metadata(["T006"])
    assert any("task dependency cycle" in finding["reason"] for finding in findings)
    assert any(finding["task"] in {"T004", "T006"} for finding in findings)


def test_task_metadata_filter_hides_unrelated_task_errors(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        _full_task_block("T006", "E001", "M001", "backend/app/domain/workflow_run.py", "backend/tests/unit/test_t006.py")
        + "\n"
        + _full_task_block("T001", "E002", "M002", "backend/app/modules/brief.py", "backend/tests/unit/test_t001.py", "T999"),
    )
    _write_workstream(workstreams / "M001.yml", _full_milestone("M001", ["E001"]))
    _write_workstream(workstreams / "M002.yml", _full_milestone("M002", ["E002"]))
    _write_workstream(workstreams / "E001.yml", _full_epic("E001", "M001", ["T006"]))
    _write_workstream(workstreams / "E002.yml", _full_epic("E002", "M002", ["T001"]))
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    findings = checks.task_metadata(["T006"])
    assert all(finding["task"] != "T001" for finding in findings)


def test_task_metadata_filter_accepts_multiple_tasks(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        _full_task_block("T045", "E001", "M001", "backend/tests/unit/test_t045_domain_primitives.py", "backend/tests/unit/test_t045_domain_primitives.py")
        + "\n"
        + _full_task_block("T046", "E001", "M001", "backend/tests/unit/test_t046_project_config_models.py", "backend/tests/unit/test_t046_project_config_models.py"),
    )
    _write_workstream(workstreams / "M001.yml", _full_milestone("M001", ["E001"]))
    _write_workstream(workstreams / "E001.yml", _full_epic("E001", "M001", ["T045", "T046"]))
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    assert checks.task_metadata(["T045", "T046"]) == []


def test_task_metadata_reports_unknown_selected_task(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        _full_task_block("T006", "E001", "M001", "backend/app/domain/workflow_run.py", "backend/tests/unit/test_t006.py"),
    )
    _write_workstream(workstreams / "M001.yml", _full_milestone("M001", ["E001"]))
    _write_workstream(workstreams / "E001.yml", _full_epic("E001", "M001", ["T006"]))
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    findings = checks.task_metadata(["T999"])
    assert any(finding["task"] == "T999" for finding in findings)
    assert any("unknown task selector" in finding["reason"] for finding in findings)


def test_task_metadata_accepts_full_valid_repo(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        _full_task_block("T001", "E001", "M001", "backend/app/modules/brief.py", "backend/tests/unit/test_t001.py")
        + "\n"
        + _full_task_block("T002", "E002", "M002", "backend/app/modules/voiceover.py", "backend/tests/unit/test_t002.py"),
    )
    _write_workstream(workstreams / "M001.yml", _full_milestone("M001", ["E001"]))
    _write_workstream(workstreams / "M002.yml", _full_milestone("M002", ["E002"]))
    _write_workstream(workstreams / "E001.yml", _full_epic("E001", "M001", ["T001"]))
    _write_workstream(workstreams / "E002.yml", _full_epic("E002", "M002", ["T002"]))
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    assert checks.task_metadata() == []


def test_task_metadata_reports_structured_consistency_findings(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        _full_task_block("T001", "E001", "M002", "backend/app/modules/brief.py", "backend/tests/unit/test_t001.py"),
    )
    _write_workstream(workstreams / "M001.yml", _full_milestone("M001", ["E001"]))
    _write_workstream(workstreams / "E001.yml", _full_epic("E001", "M001", ["T001"]))
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    findings = checks.task_metadata()
    assert any(finding.get("check") == "Milestone" for finding in findings)
    assert any(finding.get("expected") == "M001" and finding.get("actual") == "M002" for finding in findings)


def test_task_metadata_reports_milestone_drift_from_epic_manifest(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        "- [ ] T011 Implement BriefModule\n"
        "Milestone: M001\n"
        "Epic: E004\n"
        "Risk: high\n"
        "Implementation files: backend/app/modules/brief.py\n"
        "Test files: backend/tests/unit/test_t011.py\n"
        "Dependencies: None\n"
        "Validation commands: python -m pytest; git diff --check\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n",
    )
    _write_workstream(
        workstreams / "E004.yml",
        "id: E004\n"
        "title: Epic Four\n"
        "milestone: M002\n"
        "feature: specs/001-ai-content-studio\n"
        "base_branch: master\n"
        "branch: epic/E004\n"
        "status: planned\n"
        "risk: high\n"
        "depends_on:\n"
        "  - E002\n"
        "tasks:\n"
        "  - T011\n"
        "required_checks:\n"
        "  - python -m pytest\n"
        "pr_policy: human\n"
        "commit_policy: human\n",
    )
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    findings = checks.task_metadata()
    assert any(finding["task"] == "T011" for finding in findings)
    assert any("milestone M001 does not match epic E004 milestone M002" in finding["reason"] for finding in findings)


def test_missing_tasks_file_is_reported(tmp_path, monkeypatch):
    missing = tmp_path / "missing.md"
    monkeypatch.setattr(checks, "TASKS_FILE", missing)
    findings = checks.task_metadata()
    assert findings == [
        {
            "path": str(missing),
            "line": 0,
            "task": "",
            "phrase": "",
            "reason": f"missing file: {missing}",
        }
    ]


def test_forbidden_deferred_test_phrase_is_reported(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        "- [ ] T001 Implement model\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: app/model.py\n"
        "Test files: none\n"
        "Dependencies: None\n"
        "Test requirements: add tests later\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n",
    )
    _write_workstream(workstreams / "M001.yml", _full_milestone("M001", ["E001"]))
    _write_workstream(workstreams / "E001.yml", _full_epic("E001", "M001", ["T001"]))
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    findings = checks.task_metadata()
    assert any(finding["task"] == "T001" for finding in findings)
    assert any(finding["phrase"] == "tests later" for finding in findings)


def test_phase_ten_heading_alone_is_ignored(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        "- [ ] T001 Implement model\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: none\n"
        "Test files: none\n"
        "Dependencies: None\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "\n"
        "## Phase 10: Tests\n",
    )
    _write_workstream(workstreams / "M001.yml", _full_milestone("M001", ["E001"]))
    _write_workstream(workstreams / "E001.yml", _full_epic("E001", "M001", ["T001"]))
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    assert checks.task_metadata() == []


def test_task_metadata_accepts_lettered_task_ids(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        "- [ ] T001 Implement model\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: app/model.py\n"
        "Test files: backend/tests/unit/test_model.py\n"
        "Dependencies: None\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n"
        "\n"
        "- [ ] T999 Final task\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: app/final.py\n"
        "Test files: backend/tests/unit/test_final.py\n"
        "Dependencies: None\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n"
        "\n"
        "- [ ] T006A Lettered task\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: app/lettered.py\n"
        "Test files: backend/tests/unit/test_lettered.py\n"
        "Dependencies: None\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n"
        "\n"
        "- [ ] T006Z Another lettered task\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: app/lettered_z.py\n"
        "Test files: backend/tests/unit/test_lettered_z.py\n"
        "Dependencies: None\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n",
    )
    _write_workstream(workstreams / "M001.yml", _full_milestone("M001", ["E001"]))
    _write_workstream(workstreams / "E001.yml", _full_epic("E001", "M001", ["T001", "T999", "T006A", "T006Z"]))
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    assert checks.task_metadata() == []


def test_task_filter_and_bounded_output(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    body = (
        "- [ ] T001 task\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: none\n"
        "Test files: none\n"
        "Dependencies: None\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
    )
    body += "\n".join(f"- [ ] T{i:03d} task" for i in range(2, 250))
    _write_tasks(tasks, body)
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    assert checks.task_metadata(["T001"]) == []

    output, truncated = checks._truncate_lines("x\n" * 250)
    assert truncated is True
    assert len(output) == 200
    assert output[-1] == "[output truncated]"

    clipped, clipped_truncated = checks._truncate_lines("y" * 400)
    assert clipped_truncated is True
    assert clipped[0] == "y" * checks.MAX_LINE_LENGTH


def test_task_filter_accepts_lettered_task_id(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        "- [ ] T006A Lettered task\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: app/lettered.py\n"
        "Test files: backend/tests/unit/test_lettered.py\n"
        "Dependencies: None\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n",
    )
    _write_workstream(workstreams / "M001.yml", _full_milestone("M001", ["E001"]))
    _write_workstream(workstreams / "E001.yml", _full_epic("E001", "M001", ["T006A"]))
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    assert checks.task_metadata(["T006A"]) == []


def test_task_metadata_reports_test_file_requirement_conflict(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    _write_tasks(
        tasks,
        "- [ ] T001 Implement model\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: app/model.py\n"
        "Test files: none\n"
        "Dependencies: None\n"
        "Test requirements: add direct unit tests\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n",
    )
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    findings = checks.task_metadata()
    assert any("direct tests" in finding["reason"] for finding in findings)


def test_invalid_task_id_formats_are_reported(tmp_path, monkeypatch):
    for invalid in ("t006", "T006AA", "T0001", "TASK-006"):
        tasks = tmp_path / "tasks.md"
        _write_tasks(
            tasks,
            f"- [ ] {invalid} Implement model\n"
            "Milestone: M001\n"
            "Epic: E001\n"
            "Risk: low\n"
            "Implementation files: app/model.py\n"
            "Test files: backend/tests/unit/test_model.py\n"
            "Dependencies: None\n"
            "Validation commands: pytest\n"
            "Acceptance criteria: behavior\n"
            "Test requirements: Add direct tests.\n",
        )
        monkeypatch.setattr(checks, "TASKS_FILE", tasks)
        findings = checks.task_metadata()
        assert any(finding["task"] == invalid for finding in findings)
        assert any(finding["reason"] == "invalid task ID" for finding in findings)


def test_task_dependency_within_same_epic_is_allowed(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        "- [ ] T001 Base task\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: app/base.py\n"
        "Test files: backend/tests/unit/test_base.py\n"
        "Dependencies: None\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n"
        "\n"
        "- [ ] T002 Follow-up task\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: app/follow_up.py\n"
        "Test files: backend/tests/unit/test_follow_up.py\n"
        "Dependencies: T001\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n",
    )
    _write_workstream(
        workstreams / "E001.yml",
        "id: E001\n"
        "title: Epic One\n"
        "milestone: M001\n"
        "feature: specs/001-ai-content-studio\n"
        "base_branch: master\n"
        "branch: epic/E001\n"
        "status: planned\n"
        "risk: low\n"
        "depends_on: []\n"
        "tasks:\n"
        "  - T001\n"
        "  - T002\n"
        "required_checks:\n"
        "  - python -m pytest\n"
        "pr_policy: human\n"
        "commit_policy: human\n",
    )
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    assert checks.task_metadata() == []


def test_cross_epic_dependency_with_declared_manifest_dependency_is_allowed(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        "- [ ] T001 Foundation task\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: app/foundation.py\n"
        "Test files: backend/tests/unit/test_foundation.py\n"
        "Dependencies: None\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n"
        "\n"
        "- [ ] T002 Cross-epic task\n"
        "Milestone: M001\n"
        "Epic: E002\n"
        "Risk: low\n"
        "Implementation files: app/cross_epic.py\n"
        "Test files: backend/tests/unit/test_cross_epic.py\n"
        "Dependencies: T001\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n",
    )
    _write_workstream(
        workstreams / "E001.yml",
        "id: E001\n"
        "title: Epic One\n"
        "milestone: M001\n"
        "feature: specs/001-ai-content-studio\n"
        "base_branch: master\n"
        "branch: epic/E001\n"
        "status: planned\n"
        "risk: low\n"
        "depends_on: []\n"
        "tasks:\n"
        "  - T001\n"
        "required_checks:\n"
        "  - python -m pytest\n"
        "pr_policy: human\n"
        "commit_policy: human\n",
    )
    _write_workstream(
        workstreams / "E002.yml",
        "id: E002\n"
        "title: Epic Two\n"
        "milestone: M001\n"
        "feature: specs/001-ai-content-studio\n"
        "base_branch: master\n"
        "branch: epic/E002\n"
        "status: planned\n"
        "risk: low\n"
        "depends_on:\n"
        "  - E001\n"
        "tasks:\n"
        "  - T002\n"
        "required_checks:\n"
        "  - python -m pytest\n"
        "pr_policy: human\n"
        "commit_policy: human\n",
    )
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    assert checks.task_metadata() == []


def test_cross_epic_dependency_without_declared_manifest_dependency_is_reported(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        "- [X] T021 Implement canonical WorkflowConfig schema and enum validation\n"
        "Milestone: M001\n"
        "Epic: E003\n"
        "Risk: high\n"
        "Implementation files: backend/app/domain/workflow_config.py, backend/app/domain/enums.py\n"
        "Test files: backend/tests/unit/test_t021.py\n"
        "Dependencies: T005, T014\n"
        "Validation commands: python -m pytest; git diff --check\n"
        "Acceptance criteria: Valid short_video and long_form_script_voiceover configs pass; invalid enum values fail; any module in both enabledModules and disabledModules fails; provider validation runs after config validation.\n"
        "Test requirements: Add tests for valid short_video config, valid long_form_script_voiceover config, invalid enum, module conflict and validation ordering.\n"
        "\n"
        "- [ ] T014 Add API main and schemas\n"
        "Milestone: M001\n"
        "Epic: E004\n"
        "Risk: high\n"
        "Implementation files: backend/app/api/main.py, backend/app/api/schemas.py, backend/app/api/dependencies.py\n"
        "Test files: backend/tests/unit/test_t014.py\n"
        "Dependencies: T003\n"
        "Validation commands: python -m pytest; git diff --check\n"
        "Acceptance criteria: API application can be constructed.\n"
        "Test requirements: Add direct schema validation and application-construction tests in this task.\n",
    )
    _write_workstream(
        workstreams / "E003.yml",
        "id: E003\n"
        "title: Artifact Storage\n"
        "milestone: M001\n"
        "feature: specs/001-ai-content-studio\n"
        "base_branch: master\n"
        "branch: epic/E003-artifact-storage\n"
        "status: planned\n"
        "risk: high\n"
        "depends_on:\n"
        "  - E001\n"
        "tasks:\n"
        "  - T021\n"
        "required_checks:\n"
        "  - python -m pytest\n"
        "pr_policy: human\n"
        "commit_policy: human\n",
    )
    _write_workstream(
        workstreams / "E004.yml",
        "id: E004\n"
        "title: MVP Modules and Presets\n"
        "milestone: M002\n"
        "feature: specs/001-ai-content-studio\n"
        "base_branch: master\n"
        "branch: epic/E004-mvp-modules\n"
        "status: planned\n"
        "risk: high\n"
        "depends_on:\n"
        "  - E002\n"
        "  - E003\n"
        "tasks:\n"
        "  - T014\n"
        "required_checks:\n"
        "  - python -m pytest\n"
        "pr_policy: human\n"
        "commit_policy: human\n",
    )
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    findings = checks.task_metadata()
    assert any("does not depend on epic E004" in finding["reason"] for finding in findings)


def test_task_dependency_cycle_is_reported(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        "- [ ] T001 Cycle A\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: app/a.py\n"
        "Test files: backend/tests/unit/test_a.py\n"
        "Dependencies: T002\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n"
        "\n"
        "- [ ] T002 Cycle B\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: app/b.py\n"
        "Test files: backend/tests/unit/test_b.py\n"
        "Dependencies: T001\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n",
    )
    _write_workstream(
        workstreams / "E001.yml",
        "id: E001\n"
        "title: Epic One\n"
        "milestone: M001\n"
        "feature: specs/001-ai-content-studio\n"
        "base_branch: master\n"
        "branch: epic/E001\n"
        "status: planned\n"
        "risk: low\n"
        "depends_on: []\n"
        "tasks:\n"
        "  - T001\n"
        "  - T002\n"
        "required_checks:\n"
        "  - python -m pytest\n"
        "pr_policy: human\n"
        "commit_policy: human\n",
    )
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    findings = checks.task_metadata()
    assert any("task dependency cycle" in finding["reason"] for finding in findings)


def test_unknown_dependency_task_is_reported(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        "- [ ] T001 Unknown dependency\n"
        "Milestone: M001\n"
        "Epic: E001\n"
        "Risk: low\n"
        "Implementation files: app/a.py\n"
        "Test files: backend/tests/unit/test_a.py\n"
        "Dependencies: T999\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n",
    )
    _write_workstream(
        workstreams / "E001.yml",
        "id: E001\n"
        "title: Epic One\n"
        "milestone: M001\n"
        "feature: specs/001-ai-content-studio\n"
        "base_branch: master\n"
        "branch: epic/E001\n"
        "status: planned\n"
        "risk: low\n"
        "depends_on: []\n"
        "tasks:\n"
        "  - T001\n"
        "required_checks:\n"
        "  - python -m pytest\n"
        "pr_policy: human\n"
        "commit_policy: human\n",
    )
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    findings = checks.task_metadata()
    assert any("unknown dependency task" in finding["reason"] for finding in findings)


def test_task_without_epic_is_reported(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    workstreams = tmp_path / "workstreams"
    workstreams.mkdir()
    _write_tasks(
        tasks,
        "- [ ] T001 Missing epic\n"
        "Milestone: M001\n"
        "Risk: low\n"
        "Implementation files: app/a.py\n"
        "Test files: backend/tests/unit/test_a.py\n"
        "Dependencies: None\n"
        "Validation commands: pytest\n"
        "Acceptance criteria: behavior\n"
        "Test requirements: Add direct tests.\n",
    )
    _write_workstream(
        workstreams / "E001.yml",
        "id: E001\n"
        "title: Epic One\n"
        "milestone: M001\n"
        "feature: specs/001-ai-content-studio\n"
        "base_branch: master\n"
        "branch: epic/E001\n"
        "status: planned\n"
        "risk: low\n"
        "depends_on: []\n"
        "tasks:\n"
        "  - T001\n"
        "required_checks:\n"
        "  - python -m pytest\n"
        "pr_policy: human\n"
        "commit_policy: human\n",
    )
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    monkeypatch.setattr(checks, "WORKSTREAMS_DIR", workstreams)
    findings = checks.task_metadata()
    assert any("does not declare an epic" in finding["reason"] for finding in findings)


def test_process_uses_bounded_subprocess_without_shell(monkeypatch):
    captured = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = "warn"

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.run_process(["git", "status"])
    assert result["status"] == "PASS"
    assert result["output_lines"] == ["ok", "[stderr]", "warn"]
    assert captured["kwargs"]["timeout"] == 20
    assert captured["kwargs"]["check"] is False
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["env"]["GIT_PAGER"] == "cat"
    assert captured["kwargs"]["env"]["PAGER"] == "cat"
    assert captured["kwargs"]["env"]["TERM"] == "dumb"
    assert "shell" not in captured["kwargs"]


def test_process_timeout_is_controlled(monkeypatch):
    def fake_run(*args, **kwargs):
        raise checks.subprocess.TimeoutExpired(args[0], 20)

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.run_process(["git", "status"])
    assert result["status"] == "TIMEOUT"
    assert result["exit_code"] is None
    assert "timed out" in result["output_lines"][0]


def test_process_handles_missing_git_binary(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.run_process(["git", "status"])
    assert result["status"] == "MISSING"
    assert result["exit_code"] is None
    assert "missing executable: git" in result["output_lines"][0]


def test_json_mode_serializes_checks(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["repository_checks.py", "--mode", "task-metadata", "--json"])
    monkeypatch.setattr(
        checks,
        "checks",
        lambda mode, tasks=None: {"status": "PASS", "checks": [{"name": "task_metadata", "status": "PASS", "exit_code": 0, "findings": []}]},
    )
    assert checks.main() == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] == "PASS"
    assert parsed["checks"][0]["name"] == "task_metadata"


def test_capture_git_snapshot_handles_modified_staged_deleted_renamed_and_untracked_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "branch", "-M", "feature/snapshot-test")

    (tmp_path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (tmp_path / "staged.txt").write_text("staged\n", encoding="utf-8")
    (tmp_path / "deleted.txt").write_text("deleted\n", encoding="utf-8")
    (tmp_path / "rename-old.txt").write_text("rename\n", encoding="utf-8")
    (tmp_path / "space name.txt").write_text("space\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt", "staged.txt", "deleted.txt", "rename-old.txt", "space name.txt")
    _git(tmp_path, "commit", "--no-gpg-sign", "-m", "initial commit")

    (tmp_path / "tracked.txt").write_text("tracked modified\n", encoding="utf-8")
    (tmp_path / "staged.txt").write_text("staged modified\n", encoding="utf-8")
    (tmp_path / "space name.txt").write_text("space modified\n", encoding="utf-8")
    _git(tmp_path, "add", "staged.txt")
    (tmp_path / "deleted.txt").unlink()
    _git(tmp_path, "mv", "rename-old.txt", "rename new.txt")
    (tmp_path / "untracked file.txt").write_text("untracked\n", encoding="utf-8")

    snapshot = checks.capture_git_snapshot()

    assert snapshot["status"] == "PASS"
    assert snapshot["branch"] == "feature/snapshot-test"
    assert snapshot["head_sha"] == _git_head_sha(tmp_path)
    assert "tracked.txt" in snapshot["tracked"]
    assert "staged.txt" in snapshot["staged"]
    assert "deleted.txt" in snapshot["deleted"]
    assert snapshot["renamed"]
    assert all(entry["old"] and entry["new"] for entry in snapshot["renamed"])
    assert "untracked file.txt" in snapshot["untracked"]
    assert "space name.txt" in snapshot["tracked"] or "space name.txt" in snapshot["staged"]


def test_capture_git_snapshot_parses_raw_nul_output(monkeypatch):
    raw_status = (
        b"## feature/raw-snapshot\0"
        b" M tracked file.txt\0"
        b"A  staged file.txt\0"
        b"D  deleted file.txt\0"
        b"R  new file.txt\0"
        b"old file.txt\0"
        b"?? untracked file.txt\0"
        b" M path with spaces/file name.txt\0"
    )
    calls = []

    def fake_run_process(command, **kwargs):
        calls.append(tuple(command))
        if tuple(command) == ("git", "status", "--porcelain=v1", "-z", "--branch", "--untracked-files=all"):
            return checks.process_runner.ProcessResult(
                command=tuple(command),
                status="PASS",
                exit_code=0,
                duration_ms=1,
                timed_out=False,
                stdout_lines=(),
                stderr_lines=(),
                output_truncated=False,
                process_tree_killed=False,
                pid=123,
                stdout_bytes=raw_status,
                raw_output_truncated=False,
            )
        if tuple(command) == ("git", "rev-parse", "HEAD"):
            return checks.process_runner.ProcessResult(
                command=tuple(command),
                status="PASS",
                exit_code=0,
                duration_ms=1,
                timed_out=False,
                stdout_lines=("a" * 40 + "\n",),
                stderr_lines=(),
                output_truncated=False,
                process_tree_killed=False,
                pid=456,
            )
        raise AssertionError(f"unexpected command: {tuple(command)}")

    monkeypatch.setattr(checks.process_runner, "run_process", fake_run_process)
    snapshot = checks.capture_git_snapshot()

    assert calls == [
        ("git", "status", "--porcelain=v1", "-z", "--branch", "--untracked-files=all"),
        ("git", "rev-parse", "HEAD"),
    ]
    assert snapshot["status"] == "PASS"
    assert snapshot["branch"] == "feature/raw-snapshot"
    assert "tracked file.txt" in snapshot["tracked"]
    assert "staged file.txt" in snapshot["staged"]
    assert "deleted file.txt" in snapshot["deleted"]
    assert snapshot["renamed"] == [{"old": "old file.txt", "new": "new file.txt"}]
    assert "untracked file.txt" in snapshot["untracked"]
    assert "path with spaces/file name.txt" in snapshot["tracked"]


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env["TERM"] = "dumb"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    return env


def _git_head_sha(tmp_path: Path) -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True, timeout=10, env=_git_env())
    return result.stdout.strip()


def _git(tmp_path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True, timeout=10, env=_git_env())


def test_capture_git_snapshot_handles_large_real_repo_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    empty_hooks = tmp_path / "empty-hooks"
    empty_hooks.mkdir()
    _git(tmp_path, "config", "core.hooksPath", str(empty_hooks))
    _git(tmp_path, "config", "credential.interactive", "never")
    _git(tmp_path, "branch", "-M", "feature/snapshot-test")

    tracked_files = [f"tracked_{index:02d}.txt" for index in range(1, 16)]
    tracked_files.append("path with spaces/file name.txt")
    tracked_files.append("nested/" + ("very_long_segment_" * 6) + "/long-file.txt")
    tracked_files.append("delete_me.txt")
    tracked_files.append("rename_me.txt")
    for path in tracked_files:
        file_path = tmp_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f"{path}\n", encoding="utf-8")
    _git(tmp_path, "add", *tracked_files)
    _git(tmp_path, "commit", "--no-gpg-sign", "-m", "initial commit")

    changed_files = tracked_files[:10]
    for path in changed_files:
        (tmp_path / path).write_text(f"updated {path}\n", encoding="utf-8")
    (tmp_path / "path with spaces/file name.txt").write_text("updated spaced path\n", encoding="utf-8")
    (tmp_path / tracked_files[16]).write_text("updated long path\n", encoding="utf-8")
    _git(tmp_path, "add", tracked_files[10], tracked_files[11])
    (tmp_path / tracked_files[10]).write_text("updated after stage\n", encoding="utf-8")
    (tmp_path / tracked_files[17]).unlink()
    _git(tmp_path, "mv", tracked_files[18], "renamed file.txt")
    (tmp_path / "untracked one.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "untracked two.txt").write_text("two\n", encoding="utf-8")
    (tmp_path / "untracked three.txt").write_text("three\n", encoding="utf-8")

    snapshot = checks.capture_git_snapshot()

    assert snapshot["status"] == "PASS"
    assert snapshot["branch"] == "feature/snapshot-test"
    assert snapshot["head_sha"] == _git_head_sha(tmp_path)
    assert len(snapshot["tracked"]) + len(snapshot["staged"]) + len(snapshot["deleted"]) + len(snapshot["renamed"]) + len(snapshot["untracked"]) >= 15
    assert "path with spaces/file name.txt" in snapshot["tracked"] or "path with spaces/file name.txt" in snapshot["staged"]
    assert any("very_long_segment_" in path for path in snapshot["tracked"] + snapshot["staged"] + snapshot["deleted"] + snapshot["untracked"])
    assert "delete_me.txt" in snapshot["deleted"]
    assert snapshot["renamed"]
    assert any(entry["new"] == "renamed file.txt" for entry in snapshot["renamed"])
    assert "untracked one.txt" in snapshot["untracked"]
    assert "untracked two.txt" in snapshot["untracked"]
    assert "untracked three.txt" in snapshot["untracked"]


def test_checks_preflight_handles_git_snapshot_without_command(monkeypatch):
    monkeypatch.setattr(checks, "task_metadata", lambda tasks=None: [])
    monkeypatch.setattr(
        checks,
        "git_checks",
        lambda: [
            {"name": "git_snapshot", "status": "PASS", "exit_code": 0, "snapshot": {"status": "PASS"}},
            {"name": "git_diff_check", "status": "PASS", "exit_code": 0, "command": ["git", "--no-pager", "diff", "--check"]},
        ],
    )

    result = checks.checks("preflight")

    assert result["status"] == "PASS"
    assert result["checks"][0]["name"] == "task_metadata"
    assert result["checks"][1]["name"] == "git_snapshot"
    assert result["checks"][2]["name"] == checks._command_name(["git", "--no-pager", "diff", "--check"])


def test_git_checks_use_snapshot_and_diff_only(monkeypatch):
    calls = []

    monkeypatch.setattr(
        checks,
        "capture_git_snapshot",
        lambda: {"status": "PASS", "branch": "main", "head_sha": "a" * 40, "tracked": [], "staged": [], "untracked": [], "deleted": [], "renamed": [], "duration_ms": 1, "reason": ""},
    )

    def fake_run_process(command, **kwargs):
        calls.append(tuple(command))
        return checks.process_runner.ProcessResult(
            command=tuple(command),
            status="PASS",
            exit_code=0,
            duration_ms=1,
            timed_out=False,
            stdout_lines=(),
            stderr_lines=(),
            output_truncated=False,
            process_tree_killed=False,
            pid=123,
        )

    monkeypatch.setattr(checks.process_runner, "run_process", fake_run_process)

    results = checks.git_checks()

    assert results[0]["name"] == "git_snapshot"
    assert calls == [("git", "--no-pager", "diff", "--check")]

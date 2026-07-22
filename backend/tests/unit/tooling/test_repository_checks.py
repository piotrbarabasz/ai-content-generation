import json
import sys
from pathlib import Path

from app.tooling import repository_checks as checks


def _write_tasks(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _write_workstream(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_current_task_metadata_is_clean():
    assert checks.task_metadata() == []


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
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    findings = checks.task_metadata()
    assert any(finding["task"] == "T001" for finding in findings)
    assert any(finding["phrase"] == "tests later" for finding in findings)


def test_phase_ten_heading_alone_is_ignored(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
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
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    assert checks.task_metadata() == []


def test_task_metadata_accepts_lettered_task_ids(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
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
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
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
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
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

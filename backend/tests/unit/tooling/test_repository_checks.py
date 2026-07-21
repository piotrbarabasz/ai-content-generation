from pathlib import Path

from app.tooling import repository_checks as checks


def test_current_task_metadata_is_clean():
    assert checks.task_metadata() == []


def test_forbidden_deferred_test_phrase_is_reported(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    tasks.write_text(
        "- [ ] T001 Implement model\nImplementation files: app/model.py\n"
        "Test files: tests/test_model.py\nTest requirements: add tests later\n"
        "Milestone: M001\nEpic: E001\nRisk: low\nValidation commands: pytest\n"
        "Acceptance criteria: behavior\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    findings = checks.task_metadata()
    assert findings[0]["task"] == "T001"
    assert "tests later" in findings[0]["phrase"]


def test_task_filter_and_bounded_output(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks.md"
    valid = "- [ ] T001 task\nMilestone: M001\nEpic: E001\nRisk: low\nImplementation files: none\nTest files: none\nValidation commands: pytest\nAcceptance criteria: behavior\n"
    tasks.write_text(valid + "\n".join(f"- [ ] T{i:03d} task" for i in range(2, 250)), encoding="utf-8")
    monkeypatch.setattr(checks, "TASKS_FILE", tasks)
    assert checks.task_metadata(["T001"]) == []
    output, shortened = checks._limited("x\n" * 250)
    assert shortened is True
    assert len(output) <= 201


def test_process_uses_bounded_subprocess_without_shell(monkeypatch):
    captured = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.run_process(["git", "status"])
    assert result["status"] == "PASS"
    assert captured["kwargs"]["timeout"] == 20
    assert captured["kwargs"]["check"] is False
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["env"]["GIT_PAGER"] == "cat"
    assert "shell" not in captured["kwargs"]


def test_process_timeout_is_controlled(monkeypatch):
    def fake_run(*args, **kwargs):
        raise checks.subprocess.TimeoutExpired(args[0], 20)

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.run_process(["git", "status"])
    assert result["status"] == "TIMEOUT"
    assert result["exit_code"] is None

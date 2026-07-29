from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest

from app.tooling.local_autopilot import process_runner
from app.tooling.local_autopilot import codex_adapter as codex_module
from app.tooling.local_autopilot.codex_adapter import (
    CodexAdapter,
    CodexAvailability,
    CodexRunResult,
    parse_autopilot_result,
    validate_autopilot_result_contract,
)


@dataclass
class FakeProcessResult:
    command: tuple[str, ...]
    status: str = "PASS"
    exit_code: int | None = 0
    timed_out: bool = False
    cancelled: bool = False
    stdout_lines: tuple[str, ...] = ()
    stderr_lines: tuple[str, ...] = ()
    output_truncated: bool = False
    process_tree_killed: bool = False
    pid: int | None = 1234


REAL_RESOLVE_CODEX_CLI_EXECUTABLE = codex_module.resolve_codex_cli_executable


@pytest.fixture(autouse=True)
def _default_codex_executable(monkeypatch):
    monkeypatch.setattr(codex_module, "resolve_codex_cli_executable", lambda: "codex")


def _help_result(command: tuple[str, ...], *, status: str = "PASS", stdout: tuple[str, ...] = ()) -> FakeProcessResult:
    return FakeProcessResult(command=command, status=status, exit_code=0 if status == "PASS" else 1, stdout_lines=stdout)


def _write_output_last_message(command: tuple[str, ...], text: str) -> None:
    index = command.index("--output-last-message")
    output_path = Path(command[index + 1])
    output_path.write_text(text, encoding="utf-8")


def _result_payload(
    task_id: str = "T007",
    *,
    final_status: str = "COMPLETED",
    review_verdict: str | None = "PASS",
    reason: str | None = None,
    files_changed: list[str] | None = None,
    validation: list[object] | None = None,
    tasks_md_change: str = "- [X] T007 Implement one task",
    repair_cycles_used: int = 0,
    safe_to_commit: bool | None = None,
    next_task_started: bool = False,
    retryable: bool = False,
) -> dict[str, object]:
    if safe_to_commit is None:
        safe_to_commit = final_status == "COMPLETED"
    if files_changed is None:
        files_changed = ["backend/app/tooling/local_autopilot/task_pipeline.py"]
    if validation is None:
        validation = []
    return {
        "task_id": task_id,
        "final_status": final_status,
        "review_verdict": review_verdict,
        "reason": reason,
        "files_changed": files_changed,
        "validation": validation,
        "tasks_md_change": tasks_md_change,
        "repair_cycles_used": repair_cycles_used,
        "safe_to_commit": safe_to_commit,
        "next_task_started": next_task_started,
        "retryable": retryable,
    }


def _new_result_payload(
    task_id: str = "T007",
    *,
    agent_outcome: str = "finished",
    summary: str = "Implementation complete.",
    files_touched: list[str] | None = None,
    notes: list[str] | None = None,
) -> dict[str, object]:
    if files_touched is None:
        files_touched = ["backend/app/tooling/local_autopilot/task_pipeline.py"]
    if notes is None:
        notes = ["Implementation complete."]
    return {
        "task_id": task_id,
        "agent_outcome": agent_outcome,
        "summary": summary,
        "files_touched": files_touched,
        "notes": notes,
    }


def test_detect_availability_uses_codex_help_and_exec_help(tmp_path):
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        raise AssertionError(command)

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    availability = adapter.detect_availability()

    assert availability == CodexAvailability(True, True, True, None)
    assert calls == [("codex", "--help"), ("codex", "exec", "--help")]


def test_detect_availability_reports_missing_cli(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        return _help_result(command, status="MISSING")

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    availability = adapter.detect_availability()

    assert availability.has_cli is False
    assert availability.has_exec is False
    assert availability.supports_non_interactive is False
    assert availability.reason == "codex CLI is missing"


def test_detect_availability_reports_missing_exec_mode(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        return _help_result(command, stdout=("Codex exec",))

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    availability = adapter.detect_availability()

    assert availability.has_cli is True
    assert availability.has_exec is True
    assert availability.supports_non_interactive is False
    assert availability.reason == "codex exec help does not advertise non-interactive mode"


def test_resolve_codex_cli_executable_prefers_env_path(tmp_path, monkeypatch):
    executable = tmp_path / "npm" / "codex.cmd"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_CLI_PATH", str(executable))
    monkeypatch.setattr(codex_module.shutil, "which", lambda candidate: None)

    assert REAL_RESOLVE_CODEX_CLI_EXECUTABLE() == str(executable)


def test_resolve_codex_cli_executable_prefers_codex_cmd_from_path(monkeypatch, tmp_path):
    executable = tmp_path / "tools" / "codex.cmd"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)

    def fake_which(candidate):
        return str(executable) if candidate == "codex.cmd" else None

    monkeypatch.setattr(codex_module.shutil, "which", fake_which)

    assert REAL_RESOLVE_CODEX_CLI_EXECUTABLE() == str(executable)


def test_resolve_codex_cli_executable_uses_windows_appdata_fallback(monkeypatch, tmp_path):
    appdata = tmp_path / "AppData" / "Roaming"
    executable = appdata / "npm" / "codex.cmd"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(codex_module, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr(codex_module.shutil, "which", lambda candidate: None)

    assert REAL_RESOLVE_CODEX_CLI_EXECUTABLE() == str(executable)


def test_resolve_codex_cli_executable_uses_posix_which(monkeypatch, tmp_path):
    executable = tmp_path / "bin" / "codex"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    calls: list[str] = []

    monkeypatch.setattr(codex_module, "os", SimpleNamespace(name="posix", environ=os.environ))
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)

    def fake_which(candidate):
        calls.append(candidate)
        if candidate == "codex":
            return str(executable)
        return None

    monkeypatch.setattr(codex_module.shutil, "which", fake_which)

    assert REAL_RESOLVE_CODEX_CLI_EXECUTABLE() == str(executable)
    assert calls == ["codex.exe", "codex.cmd", "codex"]


def test_detect_availability_uses_resolved_executable_for_both_help_commands(tmp_path, monkeypatch):
    executable = Path(r"C:\Users\user\AppData\Roaming\npm\codex.cmd")
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    monkeypatch.setattr(codex_module, "resolve_codex_cli_executable", lambda: str(executable))

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append((command, dict(kwargs)))
        if command == (str(executable), "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == (str(executable), "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        raise AssertionError(command)

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    availability = adapter.detect_availability(timeout_seconds=5)

    assert availability == CodexAvailability(True, True, True, None)
    assert calls[0][0] == (str(executable), "--help")
    assert calls[1][0] == (str(executable), "exec", "--help")
    assert all("shell" not in kwargs for _, kwargs in calls)


def test_build_command_uses_full_path_and_preserves_spaces(tmp_path, monkeypatch):
    executable = tmp_path / "Program Files" / "npm" / "codex.cmd"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(codex_module, "resolve_codex_cli_executable", lambda: str(executable))

    adapter = CodexAdapter(tmp_path, process_runner_fn=lambda *args, **kwargs: _help_result(tuple(args[0])))
    command = adapter.build_command(
        output_last_message_path=tmp_path / "Program Files" / "output-last-message.txt",
        output_schema_path=tmp_path / "Program Files" / "output-schema.json",
    )

    assert command[0] == str(executable)
    assert command[0].endswith("codex.cmd")
    assert "Program Files" in command[0]
    assert command[1:6] == ["--sandbox", "workspace-write", "--ask-for-approval", "never", "exec"]
    assert command[-5] == "--output-schema"
    assert command[-4].endswith("output-schema.json")
    assert command[-3] == "--output-last-message"
    assert command[-2].endswith("output-last-message.txt")
    assert command[-1] == "-"


def test_detect_availability_reports_missing_cli_when_no_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_module, "resolve_codex_cli_executable", lambda: None)

    def fake_run(argv, **kwargs):
        raise AssertionError("runner should not be called when codex CLI is missing")

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    availability = adapter.detect_availability()

    assert availability.has_cli is False
    assert availability.has_exec is False
    assert availability.supports_non_interactive is False
    assert "Codex CLI was not found" in (availability.reason or "")


def test_build_prompt_requires_single_task_and_local_controls(tmp_path):
    adapter = CodexAdapter(tmp_path, process_runner_fn=lambda *args, **kwargs: _help_result(tuple(args[0])))
    prompt = adapter.build_prompt(
        task_id="T007",
        task_text="Implement one task",
        agent_python="D:/Projects/ai-content-generation/.venv/Scripts/python.exe",
        speckit_selector="T007",
    )

    assert "Selected task: T007" in prompt
    assert "Execute the following command now:" in prompt
    assert "$speckit-loop T007" in prompt
    assert "Start immediately. Do not ask the user what task to execute." in prompt
    assert "Do not wait for another message." in prompt
    assert "Use the local speckit-loop workflow for exactly one task and do not broaden scope." in prompt
    assert "Use the specified Python interpreter exactly as given." in prompt
    assert "Do not create commits, pushes, pull requests, merges, or deployments." in prompt
    assert "Task ownership and closure rules:" in prompt
    assert "The outer TaskPipeline owns persistent task closure." in prompt
    assert "Do not run spec_closer." in prompt
    assert "Do not modify tasks.md." in prompt
    assert "Do not change any checkbox in tasks.md." in prompt
    assert "Perform implementation, targeted validation, and review only." in prompt
    assert "Stop after reviewer PASS / SAFE_TO_CLOSE." in prompt
    assert "The outer TaskPipeline will run the finalizer, close the checkbox, and commit." in prompt
    assert "Do not add tasks.md to files_touched." in prompt
    assert "files_touched must contain only implementation and test files that were actually changed." in prompt
    assert "Do not start another task." in prompt
    assert "Return exactly one JSON object and nothing else." in prompt
    assert "Do not wrap the JSON in markers, fences, markdown, or explanatory text." in prompt
    assert "Do not include legacy keys." in prompt
    assert "Do not include final_status, review_verdict, files_changed, reason, validation, tasks_md_change, safe_to_commit, next_task_started or retryable." in prompt
    assert "The final response must follow this example shape exactly:" in prompt
    assert json.dumps(
        {
            "task_id": "T007",
            "agent_outcome": "finished",
            "summary": "Implementation complete.",
            "files_touched": [],
            "notes": [],
        },
        indent=2,
    ) in prompt
    assert "Legacy-compatible example" not in prompt
    assert "Use the canonical lowercase keys task_id, final_status" not in prompt
    assert "Do not emit any trailing text after the JSON object." in prompt

    with pytest.raises(ValueError):
        adapter.build_prompt(task_id="bad", task_text="x", agent_python="py", speckit_selector="T007")


def test_build_command_uses_supported_codex_exec_flags(tmp_path):
    adapter = CodexAdapter(tmp_path, process_runner_fn=lambda *args, **kwargs: _help_result(tuple(args[0])))
    command = adapter.build_command(
        output_last_message_path=tmp_path / "output-last-message.txt",
        output_schema_path=tmp_path / "output-schema.json",
    )

    assert command[:6] == ["codex", "--sandbox", "workspace-write", "--ask-for-approval", "never", "exec"]
    assert command[6:8] == ["-C", str(tmp_path)]
    assert "--ignore-user-config" not in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert "--sandbox" in command
    assert "workspace-write" in command
    assert "--color" in command
    assert "never" in command
    assert "--output-schema" in command
    assert "--output-last-message" in command
    assert command[-5] == "--output-schema"
    assert command[-4] == str(tmp_path / "output-schema.json")
    assert command[-3] == "--output-last-message"
    assert command[-2] == str(tmp_path / "output-last-message.txt")
    assert command[-1] == "-"


def test_parse_autopilot_result_accepts_raw_json():
    parsed, error = parse_autopilot_result(
        json.dumps(
            {
                "task_id": "T007",
                "final_status": "COMPLETED",
                "review_verdict": "PASS",
                "reason": None,
                "files_changed": [],
                "validation": [],
                "tasks_md_change": "- [X] T007 ...",
                "repair_cycles_used": 0,
                "safe_to_commit": True,
                "next_task_started": False,
                "retryable": False,
            },
            indent=2,
        )
    )

    assert error is None
    assert parsed is not None
    assert parsed["task_id"] == "T007"


def test_validate_autopilot_result_contract_requires_reason_for_blocked_and_failed():
    blocked_payload = _result_payload("T007", final_status="BLOCKED", review_verdict="FAIL", reason=None, safe_to_commit=False)
    failed_payload = _result_payload("T007", final_status="FAILED", review_verdict="FAIL", reason=None, safe_to_commit=False)

    blocked = validate_autopilot_result_contract(blocked_payload, task_id="T007")
    failed = validate_autopilot_result_contract(failed_payload, task_id="T007")

    assert blocked[0] is None
    assert "reason is missing" in (blocked[3] or "")
    assert failed[0] is None
    assert "reason is missing" in (failed[3] or "")


def test_autopilot_result_schema_requires_new_contract_only():
    schema = codex_module.AUTOPILOT_RESULT_SCHEMA

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["task_id", "agent_outcome", "summary", "files_touched", "notes"]


def test_validate_autopilot_result_contract_accepts_new_payload():
    payload = _new_result_payload()

    validated, status, retryable, error = validate_autopilot_result_contract(payload, task_id="T007")

    assert error is None
    assert status == "PASS"
    assert retryable is False
    assert validated is not None
    assert validated["agent_outcome"] == "finished"
    assert validated["summary"] == "Implementation complete."


def test_validate_autopilot_result_contract_accepts_legacy_only_payload():
    payload = _result_payload("T007", final_status="COMPLETED", review_verdict="PASS", reason=None, safe_to_commit=True)

    validated, status, retryable, error = validate_autopilot_result_contract(payload, task_id="T007")

    assert error is None
    assert status == "PASS"
    assert retryable is False
    assert validated is not None
    assert validated["final_status"] == "COMPLETED"


def test_validate_autopilot_result_contract_rejects_mixed_payload():
    payload = {
        **_new_result_payload(),
        "final_status": "COMPLETED",
        "review_verdict": "PASS",
    }

    validated, status, retryable, error = validate_autopilot_result_contract(payload, task_id="T007")

    assert validated is None
    assert status is None
    assert retryable is False
    assert "both legacy and new contract keys" in (error or "")


def test_run_task_removes_output_schema_tempfile_on_success(tmp_path, monkeypatch):
    created_paths: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def fake_mkstemp(*args, **kwargs):
        fd, raw_path = real_mkstemp(*args, **kwargs)
        created_paths.append(Path(raw_path))
        return fd, raw_path

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        assert "--output-schema" in command
        schema_path = Path(command[command.index("--output-schema") + 1])
        assert schema_path.is_file()
        _write_output_last_message(
            command,
            json.dumps(
                _new_result_payload(),
                indent=2,
            ),
        )
        return FakeProcessResult(command=command, status="PASS", exit_code=0, stdout_lines=("sandbox: workspace-write",))

    monkeypatch.setattr(codex_module.tempfile, "mkstemp", fake_mkstemp)

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "PASS"
    assert len(created_paths) == 2
    assert all(not path.exists() for path in created_paths)


def test_run_task_removes_output_schema_tempfile_on_error(tmp_path, monkeypatch):
    created_paths: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def fake_mkstemp(*args, **kwargs):
        fd, raw_path = real_mkstemp(*args, **kwargs)
        created_paths.append(Path(raw_path))
        return fd, raw_path

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        assert "--output-schema" in command
        schema_path = Path(command[command.index("--output-schema") + 1])
        assert schema_path.is_file()
        return FakeProcessResult(command=command, status="FAIL", exit_code=1, stdout_lines=("sandbox: workspace-write",))

    monkeypatch.setattr(codex_module.tempfile, "mkstemp", fake_mkstemp)

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "FAIL"
    assert len(created_paths) == 2
    assert all(not path.exists() for path in created_paths)


def test_run_task_parses_last_valid_result_json_and_ignores_invalid_blocks(tmp_path):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append((command, dict(kwargs)))
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        assert kwargs["stdin_text"].startswith("You are Codex running inside the local AI Content Studio autopilot.")
        assert command[:6] == ("codex", "--sandbox", "workspace-write", "--ask-for-approval", "never", "exec")
        _write_output_last_message(
            command,
            "\n".join(
                [
                    "sandbox: workspace-write",
                    "noise",
                    "AUTOPILOT_RESULT_JSON",
                    "{not-json}",
                    "AUTOPILOT_RESULT_JSON",
                    json.dumps(
                        _result_payload(
                            "T007",
                            final_status="COMPLETED",
                            review_verdict="PASS",
                            reason=None,
                            files_changed=["backend/app/tooling/local_autopilot/task_pipeline.py"],
                            validation=[{"name": "pytest", "status": "PASS"}],
                            tasks_md_change="- [X] T007 Implement one task",
                            repair_cycles_used=0,
                            safe_to_commit=True,
                            next_task_started=False,
                            retryable=True,
                        ),
                        indent=2,
                    ),
                ]
            ),
        )
        return FakeProcessResult(
            command=command,
            status="PASS",
            exit_code=0,
            stdout_lines=("Codex CLI banner", "\x1b[31mstdout truncated\x1b[0m"),
            stderr_lines=("secret=redacted", "stderr banner"),
            output_truncated=True,
        )

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="D:/Projects/ai-content-generation/.venv/Scripts/python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert isinstance(result, CodexRunResult)
    assert result.status == "PASS"
    assert result.result_json == _result_payload(
        "T007",
        final_status="COMPLETED",
        review_verdict="PASS",
        reason=None,
        files_changed=["backend/app/tooling/local_autopilot/task_pipeline.py"],
        validation=[{"name": "pytest", "status": "PASS"}],
        tasks_md_change="- [X] T007 Implement one task",
        repair_cycles_used=0,
        safe_to_commit=True,
        next_task_started=False,
        retryable=True,
    )
    assert result.semantic_status == "PASS"
    assert result.retryable is True
    assert result.effective_sandbox == "workspace-write"
    assert result.parse_error is None
    assert result.raw_output.startswith("sandbox: workspace-write")
    assert result.stdout_lines == ("Codex CLI banner", "\x1b[31mstdout truncated\x1b[0m")
    assert result.command[:6] == ("codex", "--sandbox", "workspace-write", "--ask-for-approval", "never", "exec")
    assert [command for command, _ in calls[:2]] == [("codex", "--help"), ("codex", "exec", "--help")]
    assert all("shell" not in kwargs for _, kwargs in calls)
    assert any(kwargs.get("stdin_text") for _, kwargs in calls)
    assert any(kwargs.get("stdin_text") and "$speckit-loop T007" in kwargs["stdin_text"] for _, kwargs in calls)


def test_run_task_reports_missing_json_as_failure(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        _write_output_last_message(command, "no json")
        return FakeProcessResult(command=command, status="FAIL", exit_code=1, stdout_lines=("stdout",))

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "FAIL"
    assert result.result_json is None
    assert result.parse_error == "AUTOPILOT_RESULT_JSON block not found"


def test_run_task_surfaces_codex_api_error_before_empty_output(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        return FakeProcessResult(
            command=command,
            status="FAIL",
            exit_code=1,
            stdout_lines=(),
            stderr_lines=(
                "invalid_request_error",
                "code: invalid_json_schema",
                "message: Invalid schema for response_format 'codex_output_schema': additionalProperties is required to be supplied and to be false.",
            ),
        )

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "FAIL"
    assert result.result_json is None
    assert "invalid_json_schema" in (result.parse_error or "")
    assert "empty" not in (result.parse_error or "").lower()


def test_run_task_treats_fail_status_as_failure_even_when_exit_code_is_zero(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        _write_output_last_message(
            command,
            "\n".join(
                [
                    "sandbox: workspace-write",
                    "AUTOPILOT_RESULT_JSON",
                    json.dumps(
                        _result_payload(
                            "T007",
                            final_status="FAILED",
                            review_verdict="FAIL",
                            reason="validation failed",
                            files_changed=[],
                            validation=[],
                            tasks_md_change="",
                            repair_cycles_used=1,
                            safe_to_commit=False,
                            next_task_started=False,
                            retryable=False,
                        ),
                        indent=2,
                    ),
                ]
            ),
        )
        return FakeProcessResult(command=command, status="PASS", exit_code=0, stdout_lines=("sandbox: workspace-write", "Codex banner"))

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "FAIL"
    assert result.exit_code == 1
    assert result.result_json == _result_payload(
        "T007",
        final_status="FAILED",
        review_verdict="FAIL",
        reason="validation failed",
        files_changed=[],
        validation=[],
        tasks_md_change="",
        repair_cycles_used=1,
        safe_to_commit=False,
        next_task_started=False,
        retryable=False,
    )
    assert result.semantic_status == "FAIL"
    assert result.retryable is False


def test_run_task_removes_output_last_message_tempfile(tmp_path, monkeypatch):
    created_paths: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def fake_mkstemp(*args, **kwargs):
        fd, raw_path = real_mkstemp(*args, **kwargs)
        created_paths.append(Path(raw_path))
        return fd, raw_path

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        _write_output_last_message(
            command,
            json.dumps(
                _result_payload(
                    "T007",
                    final_status="COMPLETED",
                    review_verdict="PASS",
                    reason=None,
                    files_changed=[],
                    validation=[],
                    tasks_md_change="- [X] T007 Implement one task",
                    repair_cycles_used=0,
                    safe_to_commit=True,
                    next_task_started=False,
                    retryable=False,
                ),
                indent=2,
            ),
        )
        return FakeProcessResult(command=command, status="PASS", exit_code=0, stdout_lines=("sandbox: workspace-write",))

    monkeypatch.setattr(codex_module.tempfile, "mkstemp", fake_mkstemp)

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "PASS"
    assert len(created_paths) == 2
    assert created_paths[0].suffix == ".txt"
    assert created_paths[1].suffix == ".json"
    assert not created_paths[0].exists()
    assert not created_paths[1].exists()


def test_run_task_accepts_final_status_and_retryable_flag(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        _write_output_last_message(
            command,
            json.dumps(
                {
                    "TASK_ID": "T007",
                    "FINAL_STATUS": "COMPLETED",
                    "REVIEW_VERDICT": "PASS",
                    "FILES_CHANGED": ["backend/app/tooling/local_autopilot/task_pipeline.py"],
                    "VALIDATION": [],
                    "TASKS_MD_CHANGE": "- [X] T007 Implement one task",
                    "REPAIR_CYCLES_USED": 0,
                    "SAFE_TO_COMMIT": True,
                    "NEXT_TASK_STARTED": False,
                    "RETRYABLE": True,
                },
                indent=2,
            ),
        )
        return FakeProcessResult(command=command, status="PASS", exit_code=0, stdout_lines=("sandbox: workspace-write",))

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "PASS"
    assert result.semantic_status == "PASS"
    assert result.retryable is True
    assert result.result_json["task_id"] == "T007"
    assert result.result_json["final_status"] == "COMPLETED"


def test_run_task_reports_blocked_status_from_blocked_reason_alias(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        _write_output_last_message(
            command,
            json.dumps(
                {
                    "TASK_ID": "T007",
                    "FINAL_STATUS": "FAILED",
                    "BLOCKED_REASON": "Mandatory checklists are incomplete",
                    "FILES_CHANGED": [],
                    "VALIDATION": [],
                    "TASKS_MD_CHANGE": "",
                    "REPAIR_CYCLES_USED": 0,
                    "SAFE_TO_COMMIT": False,
                    "NEXT_TASK_STARTED": False,
                    "RETRYABLE": False,
                },
                indent=2,
            ),
        )
        return FakeProcessResult(command=command, status="PASS", exit_code=0, stdout_lines=("sandbox: workspace-write",))

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "BLOCKED"
    assert result.semantic_status == "BLOCKED"
    assert result.parse_error is None
    assert result.result_json["reason"] == "Mandatory checklists are incomplete"


def test_run_task_fails_when_task_id_does_not_match(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        _write_output_last_message(
            command,
            "\n".join(
                [
                    "sandbox: workspace-write",
                    "AUTOPILOT_RESULT_JSON",
                    json.dumps(
                        {
                            "TASK_ID": "T008",
                            "FINAL_STATUS": "COMPLETED",
                            "REVIEW_VERDICT": "PASS",
                            "FILES_CHANGED": ["backend/app/tooling/local_autopilot/task_pipeline.py"],
                            "VALIDATION": [],
                            "TASKS_MD_CHANGE": "- [X] T007 Implement one task",
                            "REPAIR_CYCLES_USED": 0,
                            "SAFE_TO_COMMIT": True,
                            "NEXT_TASK_STARTED": False,
                            "RETRYABLE": True,
                        },
                        indent=2,
                    ),
                ]
            ),
        )
        return FakeProcessResult(command=command, status="PASS", exit_code=0, stdout_lines=("sandbox: workspace-write",))

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "FAIL"
    assert result.result_json is not None
    assert result.result_json["TASK_ID"] == "T008"
    assert "task_id must match" in (result.parse_error or "")


def test_run_task_fails_when_effective_sandbox_is_read_only(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        _write_output_last_message(
            command,
            "\n".join(
                [
                    "sandbox: read-only",
                    "AUTOPILOT_RESULT_JSON",
                    json.dumps(
                        _result_payload(
                            "T007",
                            final_status="COMPLETED",
                            review_verdict="PASS",
                            reason=None,
                            files_changed=["backend/app/tooling/local_autopilot/task_pipeline.py"],
                            validation=[],
                            tasks_md_change="- [X] T007 Implement one task",
                            repair_cycles_used=0,
                            safe_to_commit=True,
                            next_task_started=False,
                            retryable=True,
                        ),
                        indent=2,
                    ),
                ]
            ),
        )
        return FakeProcessResult(command=command, status="PASS", exit_code=0, stdout_lines=("sandbox: read-only",))

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "FAIL"
    assert result.retryable is False
    assert result.effective_sandbox == "read-only"
    assert "workspace-write was requested" in (result.parse_error or "")


def test_run_task_reports_missing_output_last_message_file_as_failure(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        return FakeProcessResult(command=command, status="FAIL", exit_code=1, stdout_lines=("stdout",))

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "FAIL"
    assert result.result_json is None
    assert "output-last-message" in (result.parse_error or "")


def test_run_task_reports_empty_output_last_message_file_as_failure(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        _write_output_last_message(command, "")
        return FakeProcessResult(command=command, status="FAIL", exit_code=1, stdout_lines=("stdout",))

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "FAIL"
    assert result.result_json is None
    assert "empty" in (result.parse_error or "")


def test_run_task_propagates_cancel_event(tmp_path):
    cancel_event = threading.Event()
    cancel_event.set()
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        raise AssertionError("codex exec should not run after cancellation")

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
        cancel_event=cancel_event,
    )

    assert result.status == "CANCELLED"
    assert result.cancelled is True
    assert result.parse_error is None
    assert calls == []


def test_run_task_treats_missing_result_json_as_failure_even_on_zero_exit(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        _write_output_last_message(command, "plain text")
        return FakeProcessResult(command=command, status="PASS", exit_code=0, stdout_lines=("plain text",))

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "FAIL"
    assert result.exit_code == 1
    assert result.result_json is None
    assert result.parse_error == "AUTOPILOT_RESULT_JSON block not found"


def test_parse_autopilot_result_picks_last_valid_block():
    text = "\n".join(
        [
            "noise",
            "AUTOPILOT_RESULT_JSON",
            "{not-json}",
            "AUTOPILOT_RESULT_JSON",
            '{"TASK_ID": "T001", "FINAL_STATUS": "COMPLETED", "REVIEW_VERDICT": "PASS", "FILES_CHANGED": [], "VALIDATION": [], "TASKS_MD_CHANGE": "-", "REPAIR_CYCLES_USED": 0, "SAFE_TO_COMMIT": true, "NEXT_TASK_STARTED": false, "RETRYABLE": false}',
        ]
    )

    parsed, error = parse_autopilot_result(text)
    assert error is None
    assert parsed and parsed["TASK_ID"] == "T001"


def test_parse_autopilot_result_rejects_trailing_text():
    payload = json.dumps({"task_id": "T001", "final_status": "COMPLETED"})
    parsed, error = parse_autopilot_result(f"{payload} trailing")
    assert parsed is None
    assert "trailing non-whitespace text" in (error or "")


def test_run_task_rejects_case_insensitive_key_collisions(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        _write_output_last_message(
            command,
            json.dumps(
                {
                    "task_id": "T007",
                    "TASK_ID": "T008",
                    "final_status": "COMPLETED",
                    "review_verdict": "PASS",
                    "files_changed": [],
                    "validation": [],
                    "tasks_md_change": "- [X] T007 Implement one task",
                    "repair_cycles_used": 0,
                    "safe_to_commit": True,
                    "next_task_started": False,
                    "retryable": False,
                },
                indent=2,
            ),
        )
        return FakeProcessResult(command=command, status="PASS", exit_code=0, stdout_lines=("sandbox: workspace-write",))

    adapter = CodexAdapter(tmp_path, process_runner_fn=fake_run)
    result = adapter.run_task(
        task_id="T007",
        task_text="Implement one task",
        agent_python="python.exe",
        speckit_selector="T007",
        timeout_seconds=60,
    )

    assert result.status == "FAIL"
    assert "duplicate AUTOPILOT_RESULT_JSON key" in (result.parse_error or "")
    assert result.result_json is not None


def test_run_local_autopilot_ps1_configures_current_process_only(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("PowerShell launcher behavior is only validated on Windows")

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    appdata = tmp_path / "AppData" / "Roaming"
    npm_bin = appdata / "npm"
    npm_bin.mkdir(parents=True, exist_ok=True)
    codex_cmd = npm_bin / "codex.cmd"
    codex_cmd.write_text("@echo off\n", encoding="utf-8")

    env = os.environ.copy()
    env["APPDATA"] = str(appdata)
    env["PATH"] = r"C:\Windows\System32"
    env["Path"] = r"C:\Windows\System32"
    env.pop("CODEX_CLI_PATH", None)

    script_path = Path(__file__).resolve().parents[5] / "scripts" / "run-local-autopilot.ps1"
    command = dedent(
        f"""
        . '{script_path}'
        Initialize-LocalAutopilotEnvironment | Out-Null
        Write-Output "CODEX=$env:CODEX_CLI_PATH"
        Write-Output "PATH=$env:Path"
        """
    ).strip()

    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=Path(__file__).resolve().parents[5],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    stdout_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    codex_line = next(line for line in stdout_lines if line.startswith("CODEX="))
    path_line = next(line for line in stdout_lines if line.startswith("PATH="))
    assert codex_line == f"CODEX={codex_cmd}"
    assert path_line.startswith(f"PATH={npm_bin};")
    assert os.environ.get("CODEX_CLI_PATH") != str(codex_cmd)

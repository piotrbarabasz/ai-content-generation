from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.tooling.local_autopilot import process_runner
from app.tooling.local_autopilot.codex_adapter import CodexAdapter, CodexAvailability, CodexRunResult, parse_autopilot_result


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


def _help_result(command: tuple[str, ...], *, status: str = "PASS", stdout: tuple[str, ...] = ()) -> FakeProcessResult:
    return FakeProcessResult(command=command, status=status, exit_code=0 if status == "PASS" else 1, stdout_lines=stdout)


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


def test_build_prompt_requires_single_task_and_local_controls(tmp_path):
    adapter = CodexAdapter(tmp_path, process_runner_fn=lambda *args, **kwargs: _help_result(tuple(args[0])))
    prompt = adapter.build_prompt(
        task_id="T007",
        task_text="Implement one task",
        agent_python="D:/Projects/ai-content-generation/.venv/Scripts/python.exe",
        speckit_selector="T007",
    )

    assert "Selected task: T007" in prompt
    assert "Use the local speckit-loop workflow" in prompt
    assert "Do not create commits, pushes, pull requests, merges, or deployments." in prompt
    assert "AUTOPILOT_RESULT_JSON" in prompt

    with pytest.raises(ValueError):
        adapter.build_prompt(task_id="bad", task_text="x", agent_python="py", speckit_selector="T007")


def test_build_command_uses_supported_codex_exec_flags(tmp_path):
    adapter = CodexAdapter(tmp_path, process_runner_fn=lambda *args, **kwargs: _help_result(tuple(args[0])))
    command = adapter.build_command("hello")

    assert command[:4] == ["codex", "exec", "-C", str(tmp_path)]
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert command[-1] == "hello"


def test_run_task_parses_last_valid_result_json_and_ignores_invalid_blocks(tmp_path):
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        return FakeProcessResult(
            command=command,
            status="PASS",
            exit_code=0,
            stdout_lines=(
                "noise",
                "AUTOPILOT_RESULT_JSON",
                "{not-json}",
                "AUTOPILOT_RESULT_JSON",
                '{"status": "PASS", "task_id": "T007", "notes": "ok"}',
            ),
            stderr_lines=("secret=redacted",),
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
    assert result.result_json == {"status": "PASS", "task_id": "T007", "notes": "ok"}
    assert result.parse_error is None
    assert result.command[:4] == ("codex", "exec", "-C", str(tmp_path))
    assert calls[:2] == [("codex", "--help"), ("codex", "exec", "--help")]


def test_run_task_reports_missing_json_as_failure(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("codex", "--help"):
            return _help_result(command, stdout=("Codex CLI",))
        if command == ("codex", "exec", "--help"):
            return _help_result(command, stdout=("Run Codex non-interactively",))
        return FakeProcessResult(command=command, status="FAIL", exit_code=1, stdout_lines=("no json",))

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
            '{"status": "PASS", "task_id": "T001"}',
            "tail",
        ]
    )

    parsed, error = parse_autopilot_result(text)
    assert error is None
    assert parsed == {"status": "PASS", "task_id": "T001"}

"""Adapter for the local Codex CLI used by the autopilot."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from . import process_runner

ROOT = Path(__file__).resolve().parents[4]
AUTOPILOT_RESULT_MARKER = "AUTOPILOT_RESULT_JSON"
TASK_ID_PATTERN = re.compile(r"^T\d{3}[A-Z]?$")


@dataclass(frozen=True)
class CodexAvailability:
    has_cli: bool
    has_exec: bool
    supports_non_interactive: bool
    reason: str | None = None


@dataclass(frozen=True)
class CodexRunResult:
    command: tuple[str, ...]
    status: str
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    stdout_lines: tuple[str, ...]
    stderr_lines: tuple[str, ...]
    output_truncated: bool
    process_tree_killed: bool
    pid: int | None
    raw_output: str
    result_json: dict[str, Any] | None
    parse_error: str | None = None


class CodexAdapter:
    def __init__(
        self,
        root: Path | str = ROOT,
        *,
        process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
    ) -> None:
        self.root = Path(root)
        self._run = process_runner_fn

    def detect_availability(self, *, timeout_seconds: int = 20) -> CodexAvailability:
        cli_help = self._run(
            ["codex", "--help"],
            cwd=self.root,
            timeout_seconds=timeout_seconds,
            heartbeat_seconds=0,
        )
        exec_help = self._run(
            ["codex", "exec", "--help"],
            cwd=self.root,
            timeout_seconds=timeout_seconds,
            heartbeat_seconds=0,
        )
        has_cli = cli_help.status == "PASS"
        has_exec = exec_help.status == "PASS"
        supports_non_interactive = has_cli and has_exec and any(
            "Run Codex non-interactively" in line for line in exec_help.stdout_lines
        )
        if not has_cli:
            return CodexAvailability(False, False, False, reason="codex CLI is missing")
        if not has_exec:
            return CodexAvailability(True, False, False, reason="codex exec is unavailable")
        if not supports_non_interactive:
            return CodexAvailability(True, True, False, reason="codex exec help does not advertise non-interactive mode")
        return CodexAvailability(True, True, True, reason=None)

    def build_prompt(
        self,
        *,
        task_id: str,
        task_text: str,
        agent_python: str,
        speckit_selector: str,
    ) -> str:
        normalized_task_id = _validate_task_id(task_id)
        normalized_task_text = _validate_non_empty_text("task_text", task_text)
        normalized_agent_python = _validate_non_empty_text("agent_python", agent_python)
        normalized_selector = _validate_non_empty_text("speckit_selector", speckit_selector)
        return "\n".join(
            [
                "You are Codex running inside the local AI Content Studio autopilot.",
                f"Selected task: {normalized_task_id}",
                f"Task summary: {normalized_task_text}",
                f"Python interpreter: {normalized_agent_python}",
                f"Spec Kit selector: {normalized_selector}",
                "Work on exactly one task only.",
                "Use the local speckit-loop workflow for that one task and do not broaden scope.",
                "Do not create commits, pushes, pull requests, merges, or deployments.",
                "Do not attempt any GitHub or network operations.",
                "When you finish, end with a single AUTOPILOT_RESULT_JSON block containing a JSON object.",
                "The JSON block must be the final machine-readable result.",
            ]
        )

    def run_task(
        self,
        *,
        task_id: str,
        task_text: str,
        agent_python: str,
        speckit_selector: str,
        timeout_seconds: int,
        cancel_event: threading.Event | None = None,
    ) -> CodexRunResult:
        if cancel_event is not None and cancel_event.is_set():
            return CodexRunResult(
                command=("codex", "exec"),
                status="CANCELLED",
                exit_code=None,
                timed_out=False,
                cancelled=True,
                stdout_lines=(),
                stderr_lines=(),
                output_truncated=False,
                process_tree_killed=False,
                pid=None,
                raw_output="",
                result_json=None,
                parse_error=None,
            )

        availability = self.detect_availability(timeout_seconds=min(timeout_seconds, 20))
        if not availability.has_cli:
            return CodexRunResult(
                command=("codex", "exec"),
                status="MISSING",
                exit_code=None,
                timed_out=False,
                cancelled=False,
                stdout_lines=(),
                stderr_lines=(),
                output_truncated=False,
                process_tree_killed=False,
                pid=None,
                raw_output="",
                result_json=None,
                parse_error=availability.reason,
            )
        if not availability.supports_non_interactive:
            return CodexRunResult(
                command=("codex", "exec"),
                status="FAIL",
                exit_code=1,
                timed_out=False,
                cancelled=False,
                stdout_lines=(),
                stderr_lines=(),
                output_truncated=False,
                process_tree_killed=False,
                pid=None,
                raw_output="",
                result_json=None,
                parse_error=availability.reason,
            )

        prompt = self.build_prompt(
            task_id=task_id,
            task_text=task_text,
            agent_python=agent_python,
            speckit_selector=speckit_selector,
        )
        command = self.build_command(prompt)
        process_result = self._run(
            command,
            cwd=self.root,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
            heartbeat_seconds=30,
        )
        raw_output = "\n".join(list(process_result.stdout_lines) + list(process_result.stderr_lines))
        parsed_json, parse_error = parse_autopilot_result(raw_output)
        status = process_result.status
        exit_code = process_result.exit_code
        if status == "PASS" and parsed_json is None:
            status = "FAIL"
            if exit_code == 0:
                exit_code = 1
        return CodexRunResult(
            command=tuple(process_result.command),
            status=status,
            exit_code=exit_code,
            timed_out=process_result.timed_out,
            cancelled=process_result.cancelled,
            stdout_lines=process_result.stdout_lines,
            stderr_lines=process_result.stderr_lines,
            output_truncated=process_result.output_truncated,
            process_tree_killed=process_result.process_tree_killed,
            pid=process_result.pid,
            raw_output=raw_output,
            result_json=parsed_json,
            parse_error=parse_error,
        )

    def build_command(self, prompt: str) -> list[str]:
        normalized_prompt = _validate_non_empty_text("prompt", prompt)
        return [
            "codex",
            "exec",
            "-C",
            str(self.root),
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            normalized_prompt,
        ]


def _validate_non_empty_text(field_name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _validate_task_id(task_id: str) -> str:
    normalized = _validate_non_empty_text("task_id", task_id)
    if not TASK_ID_PATTERN.fullmatch(normalized):
        raise ValueError("task_id must match T### or T###A")
    return normalized


def parse_autopilot_result(text: str) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(text, str) or not text.strip():
        return None, "AUTOPILOT_RESULT_JSON block not found"
    decoder = json.JSONDecoder()
    last_valid: dict[str, Any] | None = None
    last_error: str | None = "AUTOPILOT_RESULT_JSON block not found"
    for match in re.finditer(rf"(?m)^{re.escape(AUTOPILOT_RESULT_MARKER)}\s*$", text):
        candidate = text[match.end() :].lstrip()
        if not candidate:
            last_error = "AUTOPILOT_RESULT_JSON block not found"
            continue
        try:
            parsed, _ = decoder.raw_decode(candidate)
        except json.JSONDecodeError as exc:
            last_error = f"invalid AUTOPILOT_RESULT_JSON block: {exc}"
            continue
        if isinstance(parsed, dict):
            last_valid = parsed
            last_error = None
        else:
            last_error = "AUTOPILOT_RESULT_JSON block must decode to a JSON object"
    if last_valid is None:
        return None, last_error
    return last_valid, None


__all__ = [
    "AUTOPILOT_RESULT_MARKER",
    "CodexAdapter",
    "CodexAvailability",
    "CodexRunResult",
    "parse_autopilot_result",
]

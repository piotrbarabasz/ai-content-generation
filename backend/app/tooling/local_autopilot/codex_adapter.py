"""Adapter for the local Codex CLI used by the autopilot."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from . import process_runner

ROOT = Path(__file__).resolve().parents[4]
AUTOPILOT_RESULT_MARKER = "AUTOPILOT_RESULT_JSON"
TASK_ID_PATTERN = re.compile(r"^T\d{3}[A-Z]?$")
SUCCESS_RESULT_STATUSES = {"PASS", "PASSED", "SUCCESS", "COMPLETED"}
FAILURE_RESULT_STATUSES = {"FAIL", "FAILED", "BLOCKED", "CANCELLED", "TIMEOUT"}


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
    semantic_status: str | None = None
    effective_sandbox: str | None = None
    retryable: bool = False
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
        executable = resolve_codex_cli_executable()
        if executable is None:
            return CodexAvailability(False, False, False, reason=_missing_codex_cli_reason())
        cli_help = self._run(
            [executable, "--help"],
            cwd=self.root,
            timeout_seconds=timeout_seconds,
            heartbeat_seconds=0,
        )
        exec_help = self._run(
            [executable, "exec", "--help"],
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
                "Execute the following command now:",
                "",
                f"$speckit-loop {normalized_selector}",
                "",
                "Start immediately. Do not ask the user what task to execute.",
                "Do not wait for another message.",
                "Use the local speckit-loop workflow for exactly one task and do not broaden scope.",
                "Use the specified Python interpreter exactly as given.",
                "Do not create commits, pushes, pull requests, merges, or deployments.",
                "Do not attempt any GitHub or network operations.",
                "If the task cannot be completed, return FAIL with a reason in the final AUTOPILOT_RESULT_JSON block.",
                "The final response must end exactly with the AUTOPILOT_RESULT_JSON marker and a pretty-printed JSON object.",
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
        executable = resolve_codex_cli_executable()
        if executable is None:
            raise RuntimeError(_missing_codex_cli_reason())
        output_last_message_path = _create_output_last_message_path()
        try:
            command = self.build_command(output_last_message_path=output_last_message_path)
            process_result = self._run(
                command,
                cwd=self.root,
                timeout_seconds=timeout_seconds,
                cancel_event=cancel_event,
                heartbeat_seconds=30,
                stdin_text=prompt,
            )
            raw_output, parse_error = _read_output_last_message(output_last_message_path)
            parsed_json: dict[str, Any] | None = None
            semantic_status: str | None = None
            retryable = False
            effective_sandbox = _extract_effective_sandbox(process_result.stdout_lines)
            if effective_sandbox == "read-only":
                parse_error = "Codex effective sandbox is read-only; workspace-write was requested."
            elif raw_output is not None:
                if effective_sandbox is None:
                    effective_sandbox = _extract_effective_sandbox(raw_output.splitlines())
                parsed_json, parse_error = parse_autopilot_result(raw_output)
                if parsed_json is not None:
                    semantic_status, retryable, contract_error = validate_autopilot_result_contract(
                        parsed_json,
                        task_id=task_id,
                    )
                    if contract_error is not None:
                        parse_error = contract_error
                        retryable = False
                    elif semantic_status == "FAIL":
                        retryable = retryable and process_result.status == "PASS"
            status = process_result.status
            if process_result.status == "PASS" and semantic_status == "PASS":
                status = "PASS"
            elif process_result.status == "PASS" and semantic_status == "FAIL":
                status = "FAIL"
            elif process_result.status == "PASS" and parsed_json is None:
                status = "FAIL"
            elif process_result.status == "PASS" and parse_error is not None:
                status = "FAIL"
            exit_code = process_result.exit_code
            if status == "FAIL" and exit_code == 0:
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
                raw_output=raw_output or "",
                result_json=parsed_json,
                semantic_status=semantic_status,
                effective_sandbox=effective_sandbox,
                retryable=retryable,
                parse_error=parse_error,
            )
        finally:
            try:
                output_last_message_path.unlink()
            except FileNotFoundError:
                pass

    def build_command(self, *, output_last_message_path: Path | str) -> list[str]:
        output_path = Path(output_last_message_path)
        executable = resolve_codex_cli_executable()
        if executable is None:
            raise RuntimeError(_missing_codex_cli_reason())
        return [
            executable,
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
            "exec",
            "-C",
            str(self.root),
            "--ignore-rules",
            "--ephemeral",
            "--color",
            "never",
            "--output-last-message",
            str(output_path),
            "-",
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


def resolve_codex_cli_executable() -> str | None:
    configured = _existing_executable(os.environ.get("CODEX_CLI_PATH"))
    if configured is not None:
        return configured

    for candidate in ("codex.exe", "codex.cmd", "codex"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            fallback = _existing_executable(Path(appdata) / "npm" / "codex.cmd")
            if fallback is not None:
                return fallback
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            fallback = _existing_executable(Path(localappdata) / "npm" / "codex.cmd")
            if fallback is not None:
                return fallback
    return None


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


def validate_autopilot_result_contract(payload: dict[str, Any], *, task_id: str) -> tuple[str | None, bool, str | None]:
    if not isinstance(payload, dict):
        return None, False, "AUTOPILOT_RESULT_JSON block must decode to a JSON object"

    normalized_task_id = _validate_task_id(task_id)
    payload_task_id = payload.get("task_id")
    if not isinstance(payload_task_id, str) or payload_task_id.strip() != normalized_task_id:
        return None, False, f"AUTOPILOT_RESULT_JSON task_id must match {normalized_task_id}"

    status_value = payload.get("status")
    if status_value is None:
        status_value = payload.get("final_status")
    if not isinstance(status_value, str) or not status_value.strip():
        return None, False, "AUTOPILOT_RESULT_JSON status is missing"
    normalized_status = status_value.strip().upper()
    if normalized_status in SUCCESS_RESULT_STATUSES:
        return "PASS", _parse_retryable(payload), None
    if normalized_status in FAILURE_RESULT_STATUSES:
        return "FAIL", _parse_retryable(payload), None
    return None, False, f"unknown AUTOPILOT_RESULT_JSON status: {status_value!r}"


def _existing_executable(value: str | os.PathLike[str] | None) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip().strip('"')
    if not candidate:
        return None
    path = Path(candidate)
    if path.is_file():
        return str(path)
    return None


def _create_output_last_message_path() -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="autopilot-codex-", suffix=".txt")
    os.close(fd)
    return Path(raw_path)


def _read_output_last_message(path: Path) -> tuple[str | None, str | None]:
    if not path.is_file():
        return None, "AUTOPILOT_RESULT_JSON block not found (codex did not write output-last-message)"
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, f"AUTOPILOT_RESULT_JSON block not found (failed to read codex output-last-message: {exc})"
    if not content.strip():
        return None, "AUTOPILOT_RESULT_JSON block not found (codex output-last-message is empty)"
    return content, None


def _parse_retryable(payload: dict[str, Any]) -> bool:
    retryable = payload.get("retryable", False)
    return isinstance(retryable, bool) and retryable


def _extract_effective_sandbox(stdout_lines: Sequence[str]) -> str | None:
    for line in stdout_lines:
        normalized = line.strip().lower()
        if not normalized.startswith("sandbox:"):
            continue
        if "read-only" in normalized:
            return "read-only"
        if "workspace-write" in normalized:
            return "workspace-write"
    return None


def _missing_codex_cli_reason() -> str:
    if os.name == "nt":
        return "Codex CLI was not found.\nChecked PATH, CODEX_CLI_PATH and %APPDATA%\\npm\\codex.cmd."
    return "Codex CLI was not found. Checked PATH and CODEX_CLI_PATH."


__all__ = [
    "AUTOPILOT_RESULT_MARKER",
    "CodexAdapter",
    "CodexAvailability",
    "CodexRunResult",
    "parse_autopilot_result",
    "resolve_codex_cli_executable",
]

"""Deterministic runner for Git hook and CI validation sequences."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from . import process_runner

ROOT = Path(__file__).resolve().parents[3]
MAX_OUTPUT_LINES = 20
MAX_LINE_LENGTH = 300
ZERO_SHA = "0" * 40
GLOBAL_TIMEOUTS = {
    "pre-commit": 60,
    "pre-push": 480,
    "ci": 900,
}


@dataclass(frozen=True)
class HookCommand:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: int


@dataclass(frozen=True)
class HookCommandResult:
    name: str
    command: str
    status: str
    exit_code: int | None
    timeout_seconds: int
    duration_ms: int
    timed_out: bool = False
    output: str | None = None


@dataclass(frozen=True)
class HookRunResult:
    mode: str
    status: str
    global_timeout: bool
    global_timeout_seconds: int
    commands: tuple[HookCommandResult, ...]


def _truncate(value: str, *, limit: int = MAX_LINE_LENGTH) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _command_text(argv: Sequence[str]) -> str:
    return " ".join(argv)


def _summarize_output(stdout_lines: Sequence[str], stderr_lines: Sequence[str]) -> str | None:
    stdout = [line.strip() for line in stdout_lines if line.strip()]
    stderr = [line.strip() for line in stderr_lines if line.strip()]
    if stdout and stderr:
        return _truncate(f"{stdout[0]} | {stderr[0]}")
    if stdout:
        return _truncate(stdout[0])
    if stderr:
        return _truncate(stderr[0])
    return None


def _ci_diff_argv(base_sha: str | None, head_sha: str | None) -> tuple[str, ...]:
    if base_sha and head_sha and base_sha != ZERO_SHA:
        return ("git", "--no-pager", "diff", "--check", f"{base_sha}...{head_sha}")
    if head_sha:
        return ("git", "--no-pager", "diff", "--check", f"{head_sha}^!")
    return ("git", "--no-pager", "diff", "--check")


def _commands_for_mode(mode: str, *, base_sha: str | None = None, head_sha: str | None = None) -> tuple[HookCommand, ...]:
    if mode == "pre-commit":
        return (
            HookCommand(
                name="workstream_validation",
                argv=(sys.executable, "-m", "backend.app.tooling.workstream_validation"),
                timeout_seconds=20,
            ),
            HookCommand(
                name="repository_checks_task_metadata",
                argv=(sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
                timeout_seconds=20,
            ),
            HookCommand(
                name="git_diff_cached_check",
                argv=("git", "--no-pager", "diff", "--cached", "--check"),
                timeout_seconds=20,
            ),
        )

    if mode == "pre-push":
        return (
            HookCommand(
                name="workstream_validation",
                argv=(sys.executable, "-m", "backend.app.tooling.workstream_validation"),
                timeout_seconds=20,
            ),
            HookCommand(
                name="repository_checks_task_metadata",
                argv=(sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
                timeout_seconds=20,
            ),
            HookCommand(
                name="pytest_full",
                argv=(sys.executable, "-m", "pytest"),
                timeout_seconds=300,
            ),
            HookCommand(
                name="git_diff_check",
                argv=("git", "--no-pager", "diff", "--check"),
                timeout_seconds=20,
            ),
        )

    if mode == "ci":
        return (
            HookCommand(
                name="workstream_validation",
                argv=(sys.executable, "-m", "backend.app.tooling.workstream_validation"),
                timeout_seconds=30,
            ),
            HookCommand(
                name="repository_checks_task_metadata",
                argv=(sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
                timeout_seconds=30,
            ),
            HookCommand(
                name="pytest_full",
                argv=(sys.executable, "-m", "pytest"),
                timeout_seconds=600,
            ),
            HookCommand(
                name="git_diff_check",
                argv=_ci_diff_argv(base_sha, head_sha),
                timeout_seconds=30,
            ),
        )

    raise ValueError(f"unsupported mode: {mode}")


def _effective_timeout_seconds(command_timeout_seconds: int, remaining_seconds: float) -> int:
    return max(1, min(command_timeout_seconds, max(1, math.ceil(remaining_seconds))))


def _run_command(command: HookCommand, *, deadline: float, heartbeat_seconds: int) -> HookCommandResult:
    remaining_seconds = deadline - time.monotonic()
    if remaining_seconds <= 0:
        raise TimeoutError("GLOBAL_TIMEOUT")

    effective_timeout = _effective_timeout_seconds(command.timeout_seconds, remaining_seconds)
    started = time.perf_counter()
    result = process_runner.run_process(
        command.argv,
        cwd=ROOT,
        timeout_seconds=effective_timeout,
        total_deadline=deadline,
        heartbeat_seconds=heartbeat_seconds,
    )
    duration_ms = max(0, int((time.perf_counter() - started) * 1000))
    output = None
    if result.status != "PASS":
        output = _summarize_output(result.stdout_lines, result.stderr_lines)
    return HookCommandResult(
        name=command.name,
        command=_command_text(command.argv),
        status=result.status,
        exit_code=result.exit_code,
        timeout_seconds=effective_timeout,
        duration_ms=duration_ms,
        timed_out=result.timed_out,
        output=output,
    )


def run_hook(
    mode: str,
    *,
    base_sha: str | None = None,
    head_sha: str | None = None,
    heartbeat_seconds: int = 30,
) -> HookRunResult:
    commands = _commands_for_mode(mode, base_sha=base_sha, head_sha=head_sha)
    deadline = time.monotonic() + GLOBAL_TIMEOUTS[mode]
    results: list[HookCommandResult] = []
    global_timeout = False
    overall_status = "PASS"

    for command in commands:
        try:
            result = _run_command(command, deadline=deadline, heartbeat_seconds=heartbeat_seconds)
        except TimeoutError:
            global_timeout = True
            overall_status = "TIMEOUT"
            break

        results.append(result)
        now = time.monotonic()
        if result.status == "TIMEOUT" and now >= deadline:
            global_timeout = True
            overall_status = "TIMEOUT"
            break
        if now >= deadline:
            global_timeout = True
            overall_status = "TIMEOUT"
            break
        if result.status != "PASS":
            overall_status = result.status
            break

    return HookRunResult(
        mode=mode,
        status=overall_status,
        global_timeout=global_timeout,
        global_timeout_seconds=GLOBAL_TIMEOUTS[mode],
        commands=tuple(results),
    )


def _render_text(result: HookRunResult) -> str:
    lines = [
        f"mode: {result.mode}",
        f"status: {result.status}",
    ]
    if result.global_timeout:
        lines.append(f"GLOBAL_TIMEOUT: budget={result.global_timeout_seconds}s")
    for index, command in enumerate(result.commands, 1):
        exit_code = "None" if command.exit_code is None else str(command.exit_code)
        line = (
            f"{index}. {command.name}: {command.status} exit={exit_code} "
            f"timeout={command.timeout_seconds}s duration={command.duration_ms}ms command={command.command}"
        )
        if command.timed_out:
            line += " TIMEOUT"
        lines.append(_truncate(line))
        if command.output and command.status != "PASS":
            lines.append(_truncate(f"detail: {command.output}"))
    if len(lines) > MAX_OUTPUT_LINES:
        lines = lines[: MAX_OUTPUT_LINES - 1] + ["[output truncated]"]
    return "\n".join(lines)


def _render_json(result: HookRunResult) -> str:
    lines = [
        "{",
        f'  "mode": {json.dumps(result.mode, ensure_ascii=False)},',
        f'  "status": {json.dumps(result.status, ensure_ascii=False)},',
        f'  "global_timeout": {json.dumps(result.global_timeout)},',
        f'  "global_timeout_seconds": {result.global_timeout_seconds},',
        '  "commands": [',
    ]
    for index, command in enumerate(result.commands):
        payload = {
            "name": command.name,
            "status": command.status,
            "exit_code": command.exit_code,
            "timeout_seconds": command.timeout_seconds,
            "duration_ms": command.duration_ms,
            "timed_out": command.timed_out,
        }
        if command.output and command.status != "PASS":
            payload["output"] = command.output
        suffix = "," if index < len(result.commands) - 1 else ""
        lines.append(f"    {json.dumps(payload, ensure_ascii=False)}{suffix}")
    lines.extend(["  ]", "}"])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backend.app.tooling.git_hook_runner")
    parser.add_argument("mode", choices=["pre-commit", "pre-push", "ci"])
    parser.add_argument("--base-sha")
    parser.add_argument("--head-sha")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-heartbeat", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code)

    try:
        result = run_hook(
            args.mode,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            heartbeat_seconds=0 if args.no_heartbeat else 30,
        )
    except ValueError as exc:
        print(str(exc))
        return 2

    if args.json:
        print(_render_json(result))
    else:
        print(_render_text(result))
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

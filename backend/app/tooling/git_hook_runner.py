"""Deterministic runner for Git hook and CI validation sequences."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[3]
MAX_OUTPUT_LINES = 20
MAX_LINE_LENGTH = 300
ZERO_SHA = "0" * 40
COMMAND_TIMEOUTS = {
    "workstream_validation": 30,
    "repository_checks_task_metadata": 30,
    "git_diff_cached_check": 30,
    "git_diff_check": 30,
    "pytest_unit_tooling": 180,
    "pytest_full": 600,
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
    commands: tuple[HookCommandResult, ...]


PRE_COMMIT_COMMANDS = (
    HookCommand(
        name="workstream_validation",
        argv=(sys.executable, "-m", "backend.app.tooling.workstream_validation"),
        timeout_seconds=COMMAND_TIMEOUTS["workstream_validation"],
    ),
    HookCommand(
        name="repository_checks_task_metadata",
        argv=(sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
        timeout_seconds=COMMAND_TIMEOUTS["repository_checks_task_metadata"],
    ),
    HookCommand(
        name="git_diff_cached_check",
        argv=("git", "--no-pager", "diff", "--cached", "--check"),
        timeout_seconds=COMMAND_TIMEOUTS["git_diff_cached_check"],
    ),
)

PRE_PUSH_COMMANDS = (
    HookCommand(
        name="pytest_unit_tooling",
        argv=(sys.executable, "-m", "pytest", "backend/tests/unit/tooling"),
        timeout_seconds=COMMAND_TIMEOUTS["pytest_unit_tooling"],
    ),
    HookCommand(
        name="workstream_validation",
        argv=(sys.executable, "-m", "backend.app.tooling.workstream_validation"),
        timeout_seconds=COMMAND_TIMEOUTS["workstream_validation"],
    ),
    HookCommand(
        name="repository_checks_task_metadata",
        argv=(sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
        timeout_seconds=COMMAND_TIMEOUTS["repository_checks_task_metadata"],
    ),
    HookCommand(
        name="pytest_full",
        argv=(sys.executable, "-m", "pytest"),
        timeout_seconds=COMMAND_TIMEOUTS["pytest_full"],
    ),
    HookCommand(
        name="git_diff_check",
        argv=("git", "--no-pager", "diff", "--check"),
        timeout_seconds=COMMAND_TIMEOUTS["git_diff_check"],
    ),
)

HOOK_COMMANDS: dict[str, tuple[HookCommand, ...]] = {
    "pre-commit": PRE_COMMIT_COMMANDS,
    "pre-push": PRE_PUSH_COMMANDS,
    "ci": PRE_PUSH_COMMANDS,
}


def _ci_diff_argv(base_sha: str | None, head_sha: str | None) -> tuple[str, ...]:
    if base_sha and head_sha and base_sha != ZERO_SHA:
        return ("git", "--no-pager", "diff", "--check", f"{base_sha}...{head_sha}")
    if head_sha:
        return ("git", "--no-pager", "diff", "--check", f"{head_sha}^!")
    return ("git", "--no-pager", "diff", "--check")


def _commands_for_mode(mode: str, *, base_sha: str | None = None, head_sha: str | None = None) -> tuple[HookCommand, ...]:
    commands = list(HOOK_COMMANDS[mode])
    if mode == "ci":
        adjusted: list[HookCommand] = []
        for command in commands:
            if command.name == "git_diff_check":
                adjusted.append(
                    HookCommand(
                        name=command.name,
                        argv=_ci_diff_argv(base_sha, head_sha),
                        timeout_seconds=command.timeout_seconds,
                    )
                )
            else:
                adjusted.append(command)
        return tuple(adjusted)
    return tuple(commands)


def _command_text(argv: Sequence[str]) -> str:
    return " ".join(argv)


def _truncate(value: str, *, limit: int = MAX_LINE_LENGTH) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _to_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None:
        return ""
    return str(value)


def _summarize_output(stdout: str, stderr: str) -> str | None:
    stdout = stdout.strip()
    stderr = stderr.strip()
    if stdout and stderr:
        summary = f"{stdout.splitlines()[0]} | {stderr.splitlines()[0]}"
    else:
        text = stdout or stderr
        if not text:
            return None
        summary = text.splitlines()[0]
    return _truncate(summary)


def _run_command(command: HookCommand) -> HookCommandResult:
    env = os.environ.copy()
    env.update({"GIT_PAGER": "cat", "PAGER": "cat", "TERM": "dumb", "PYTHONUNBUFFERED": "1"})
    started = time.perf_counter()
    try:
        result = subprocess.run(
            list(command.argv),
            cwd=ROOT,
            shell=False,
            timeout=command.timeout_seconds,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        timeout_note = f"TIMEOUT after {command.timeout_seconds} seconds"
        if exc.stdout or exc.stderr:
            output = _summarize_output(_to_text(exc.stdout), _to_text(exc.stderr))
        else:
            output = timeout_note
        return HookCommandResult(
            name=command.name,
            command=_command_text(command.argv),
            status="TIMEOUT",
            exit_code=None,
            timeout_seconds=command.timeout_seconds,
            duration_ms=duration_ms,
            timed_out=True,
            output=output,
        )
    except (FileNotFoundError, OSError) as exc:
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        return HookCommandResult(
            name=command.name,
            command=_command_text(command.argv),
            status="FAIL",
            exit_code=None,
            timeout_seconds=command.timeout_seconds,
            duration_ms=duration_ms,
            output=_truncate(str(exc)),
        )

    duration_ms = max(0, int((time.perf_counter() - started) * 1000))
    status = "PASS" if result.returncode == 0 else "FAIL"
    return HookCommandResult(
        name=command.name,
        command=_command_text(command.argv),
        status=status,
        exit_code=result.returncode,
        timeout_seconds=command.timeout_seconds,
        duration_ms=duration_ms,
        output=_summarize_output(result.stdout or "", result.stderr or ""),
    )


def run_hook(mode: str, *, base_sha: str | None = None, head_sha: str | None = None) -> HookRunResult:
    commands = _commands_for_mode(mode, base_sha=base_sha, head_sha=head_sha)
    results: list[HookCommandResult] = []
    overall_status = "PASS"

    for command in commands:
        result = _run_command(command)
        results.append(result)
        if result.status != "PASS":
            overall_status = result.status
            break

    return HookRunResult(mode=mode, status=overall_status, commands=tuple(results))


def _render_text(result: HookRunResult) -> str:
    lines = [
        f"mode: {result.mode}",
        f"status: {result.status}",
    ]
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
    parser.add_argument("mode", choices=sorted(HOOK_COMMANDS))
    parser.add_argument("--base-sha")
    parser.add_argument("--head-sha")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code)

    result = run_hook(args.mode, base_sha=args.base_sha, head_sha=args.head_sha)
    if args.json:
        print(_render_json(result))
    else:
        print(_render_text(result))
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

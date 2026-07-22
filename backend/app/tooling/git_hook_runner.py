"""Deterministic runner for Git hook and CI validation sequences."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[3]
TIMEOUT_SECONDS = 20
MAX_OUTPUT_LINES = 20
MAX_LINE_LENGTH = 300


@dataclass(frozen=True)
class HookCommand:
    name: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class HookCommandResult:
    name: str
    command: str
    status: str
    exit_code: int | None
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
        argv=("python", "-m", "backend.app.tooling.workstream_validation"),
    ),
    HookCommand(
        name="repository_checks_task_metadata",
        argv=("python", "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
    ),
    HookCommand(
        name="git_diff_cached_check",
        argv=("git", "--no-pager", "diff", "--cached", "--check"),
    ),
)

PRE_PUSH_COMMANDS = (
    HookCommand(
        name="pytest_unit_tooling",
        argv=("python", "-m", "pytest", "backend/tests/unit/tooling"),
    ),
    HookCommand(
        name="workstream_validation",
        argv=("python", "-m", "backend.app.tooling.workstream_validation"),
    ),
    HookCommand(
        name="repository_checks_task_metadata",
        argv=("python", "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
    ),
    HookCommand(
        name="pytest_full",
        argv=("python", "-m", "pytest"),
    ),
    HookCommand(
        name="git_diff_check",
        argv=("git", "--no-pager", "diff", "--check"),
    ),
)

HOOK_COMMANDS: dict[str, tuple[HookCommand, ...]] = {
    "pre-commit": PRE_COMMIT_COMMANDS,
    "pre-push": PRE_PUSH_COMMANDS,
    "ci": PRE_PUSH_COMMANDS,
}


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
    env.update({"GIT_PAGER": "cat", "PAGER": "cat", "TERM": "dumb"})
    try:
        result = subprocess.run(
            list(command.argv),
            cwd=ROOT,
            shell=False,
            timeout=TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        timeout_note = f"TIMEOUT after {TIMEOUT_SECONDS} seconds"
        if exc.stdout or exc.stderr:
            output = _summarize_output(_to_text(exc.stdout), _to_text(exc.stderr))
        else:
            output = timeout_note
        return HookCommandResult(
            name=command.name,
            command=_command_text(command.argv),
            status="TIMEOUT",
            exit_code=None,
            timed_out=True,
            output=output,
        )
    except (FileNotFoundError, OSError) as exc:
        return HookCommandResult(
            name=command.name,
            command=_command_text(command.argv),
            status="FAIL",
            exit_code=None,
            output=_truncate(str(exc)),
        )

    status = "PASS" if result.returncode == 0 else "FAIL"
    return HookCommandResult(
        name=command.name,
        command=_command_text(command.argv),
        status=status,
        exit_code=result.returncode,
        output=_summarize_output(result.stdout or "", result.stderr or ""),
    )


def run_hook(mode: str) -> HookRunResult:
    commands = HOOK_COMMANDS[mode]
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
        line = f"{index}. {command.name}: {command.status} exit={exit_code} command={command.command}"
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
            "timed_out": command.timed_out,
        }
        suffix = "," if index < len(result.commands) - 1 else ""
        lines.append(f"    {json.dumps(payload, ensure_ascii=False)}{suffix}")
    lines.extend(["  ]", "}"])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backend.app.tooling.git_hook_runner")
    parser.add_argument("mode", choices=sorted(HOOK_COMMANDS))
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code)

    result = run_hook(args.mode)
    if args.json:
        print(_render_json(result))
    else:
        print(_render_text(result))
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

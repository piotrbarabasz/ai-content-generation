"""Deterministic runner for Git hook and CI validation sequences."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

from . import process_runner
from .local_autopilot.repository import Repository
from .local_autopilot.task_state_machine import (
    TaskLifecycleState,
    TaskReceiptRecord,
    TaskStateRecord,
    load_task_receipt,
    load_task_state,
    save_task_receipt,
    save_task_state,
)
from .local_autopilot import workstreams
from . import task_consistency

ROOT = Path(__file__).resolve().parents[3]
MAX_OUTPUT_LINES = 20
MAX_LINE_LENGTH = 300
ZERO_SHA = "0" * 40
TASK_COMMIT_MESSAGE_PATTERN = re.compile(r"^feat\((T\d{3}[A-Z]?)\):")
GLOBAL_TIMEOUTS = {
    "pre-commit": 60,
    "pre-push": 480,
    "post-commit": 60,
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
    reason: str | None = None


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

    if mode == "post-commit":
        return ()

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


def _task_state_records() -> list[TaskStateRecord]:
    state_dir = ROOT / ".specify" / "runtime" / "task-state"
    if not state_dir.is_dir():
        return []
    records: list[TaskStateRecord] = []
    for path in sorted(state_dir.glob("*.json")):
        record = load_task_state(path.stem, root=ROOT)
        if record is not None:
            records.append(record)
    return records


def _repo_relative_path(value: str | Path) -> str:
    path = Path(value)
    if path.is_absolute():
        try:
            path = path.relative_to(ROOT)
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def _path_allowed(path: str, allowlist: Sequence[str]) -> bool:
    normalized_path = str(path).replace("\\", "/").strip()
    if not normalized_path:
        return False
    for item in allowlist:
        normalized_item = str(item).replace("\\", "/").strip().rstrip("/")
        if not normalized_item:
            continue
        if normalized_path == normalized_item or normalized_path.startswith(f"{normalized_item}/"):
            return True
    return False


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_exception_reason(exc: BaseException) -> str:
    message = " ".join(str(exc).split())
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def _run_git(argv: Sequence[str], *, timeout_seconds: int = 20) -> process_runner.ProcessResult:
    return process_runner.run_process(list(argv), cwd=ROOT, timeout_seconds=timeout_seconds, heartbeat_seconds=0)


def _git_stdout_lines(argv: Sequence[str], *, timeout_seconds: int = 20) -> tuple[str, ...]:
    result = _run_git(argv, timeout_seconds=timeout_seconds)
    if result.status != "PASS":
        raise RuntimeError(f"git command failed: {' '.join(str(part) for part in argv)}")
    return result.stdout_lines


def _git_output_text(argv: Sequence[str], *, timeout_seconds: int = 20) -> str:
    return "\n".join(_git_stdout_lines(argv, timeout_seconds=timeout_seconds))


def _tasks_md_relpath(tasks_path: Path | None = None) -> str:
    path = tasks_path or _default_tasks_file()
    return _repo_relative_path(path)


def _default_tasks_file() -> Path:
    return ROOT / "specs" / "001-ai-content-studio" / "tasks.md"


def _staged_task_checkbox_changes(tasks_path: Path | None = None) -> list[tuple[str, str, str]]:
    tasks_path = tasks_path or _default_tasks_file()
    relpath = _repo_relative_path(tasks_path)
    diff_lines = _git_stdout_lines(("git", "diff", "--cached", "--unified=0", "--", relpath))
    removed: dict[str, str] = {}
    added: dict[str, str] = {}
    other_changes: list[str] = []
    for line in diff_lines:
        if not line or line.startswith(("diff --git", "index ", "--- ", "+++ ", "@@", "new file mode", "deleted file mode")):
            continue
        if line[0] not in "+-":
            continue
        body = line[1:]
        if body.startswith("- ["):
            match = task_consistency.TASK_HEADER_PATTERN.match(body)
            if match:
                task_id = match.group("task")
                checkbox = match.group("checkbox")
                if line[0] == "-":
                    removed[task_id] = checkbox
                else:
                    added[task_id] = checkbox
                continue
        other_changes.append(line)
    task_ids = sorted(set(removed) & set(added))
    changes: list[tuple[str, str, str]] = []
    for task_id in task_ids:
        before = removed[task_id]
        after = added[task_id]
        if before == " " and after == "X":
            changes.append((task_id, before, after))
    if len(changes) == 1 and not other_changes and len(removed) == 1 and len(added) == 1:
        return changes
    if changes:
        raise ValueError("staged tasks.md must change exactly one checkbox from [ ] to [X]")
    return []


def _task_checkbox_change_reason(tasks_path: Path | None = None) -> tuple[str | None, list[tuple[str, str, str]]]:
    tasks_path = tasks_path or _default_tasks_file()
    changes = _staged_task_checkbox_changes(tasks_path)
    if len(changes) > 1:
        raise ValueError("staged diff closes multiple tasks")
    if not changes:
        return None, []
    return changes[0][0], changes


def _task_checkbox(tasks_path: Path, task_id: str) -> str:
    for found_task_id, _start_line, lines in task_consistency._iter_task_blocks(tasks_path):
        if found_task_id != task_id:
            continue
        header = lines[0][1]
        if header.startswith("- [X]"):
            return "X"
        if header.startswith("- [x]"):
            return "x"
        if header.startswith("- [ ]"):
            return " "
        return header[2:3] if header.startswith("- [") else " "
    raise ValueError(f"task does not exist in tasks.md: {task_id}")


def _task_epic(tasks_path: Path, task_id: str) -> str:
    for found_task_id, _start_line, lines in task_consistency._iter_task_blocks(tasks_path):
        if found_task_id != task_id:
            continue
        epic = task_consistency._field_value(lines, "Epic:")
        if epic is None:
            raise ValueError(f"task {task_id} is missing Epic in {tasks_path.name}")
        return epic[1].strip()
    raise ValueError(f"task does not exist in tasks.md: {task_id}")


def _task_commit_task_id_from_message(message: str) -> str | None:
    first_line = message.strip().splitlines()[0].strip() if message.strip() else ""
    match = TASK_COMMIT_MESSAGE_PATTERN.match(first_line)
    if match:
        return match.group(1)
    return None


def _staged_paths(status: Any) -> set[str]:
    return {
        *(_repo_relative_path(item) for item in getattr(status, "staged", ())),
        *(_repo_relative_path(item) for item in getattr(status, "deleted", ())),
        *(_repo_relative_path(old) for old, _ in getattr(status, "renamed", ())),
        *(_repo_relative_path(new) for _, new in getattr(status, "renamed", ())),
    }


def _commit_paths(commit_ref: str) -> set[str]:
    paths = _git_stdout_lines(("git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit_ref))
    return {path.strip() for path in paths if path.strip()}


def _paths_include_tasks_md(paths: set[str], tasks_path: Path) -> bool:
    return _repo_relative_path(tasks_path) in paths


def _commit_exists(commit_sha: str) -> bool:
    result = _run_git(("git", "cat-file", "-e", f"{commit_sha}^{{commit}}"))
    return result.status == "PASS"


def _current_commit_subject() -> str:
    return _git_output_text(("git", "show", "-s", "--format=%s", "HEAD"))


def _parent_commit(commit_ref: str = "HEAD") -> str:
    return _git_stdout_lines(("git", "rev-parse", f"{commit_ref}^"))[0].strip()


def _is_task_commit_in_history(repository: Repository, ancestor: str, descendant: str) -> bool:
    if not ancestor or not descendant:
        return False
    return repository.is_ancestor(ancestor, descendant)


def _task_commit_task_ids_in_commit(commit_ref: str, tasks_path: Path | None = None) -> list[str]:
    tasks_path = tasks_path or _default_tasks_file()
    parent_ref = _parent_commit(commit_ref)
    relpath = _repo_relative_path(tasks_path)
    diff_lines = _git_stdout_lines(("git", "diff", "--unified=0", parent_ref, commit_ref, "--", relpath))
    return [task_id for task_id, _before, _after in _task_checkbox_changes_from_diff(diff_lines)]


def _task_checkbox_changes_from_diff(diff_lines: Sequence[str]) -> list[tuple[str, str, str]]:
    removed: dict[str, str] = {}
    added: dict[str, str] = {}
    other_changes: list[str] = []
    for line in diff_lines:
        if not line or line.startswith(("diff --git", "index ", "--- ", "+++ ", "@@", "new file mode", "deleted file mode")):
            continue
        if line[0] not in "+-":
            continue
        body = line[1:]
        match = task_consistency.TASK_HEADER_PATTERN.match(body)
        if match:
            task_id = match.group("task")
            checkbox = match.group("checkbox")
            if line[0] == "-":
                removed[task_id] = checkbox
            else:
                added[task_id] = checkbox
            continue
        other_changes.append(line)
    task_ids = sorted(set(removed) & set(added))
    changes: list[tuple[str, str, str]] = []
    for task_id in task_ids:
        before = removed[task_id]
        after = added[task_id]
        if before == " " and after == "X":
            changes.append((task_id, before, after))
    if changes and (len(changes) != 1 or other_changes or len(removed) != 1 or len(added) != 1):
        raise ValueError("staged tasks.md must change exactly one checkbox from [ ] to [X]")
    if removed or added:
        if len(changes) == 1 and not other_changes and len(removed) == 1 and len(added) == 1:
            return changes
        raise ValueError("staged tasks.md must change exactly one checkbox from [ ] to [X]")
    return changes


def _validate_pre_commit_state(repository: Repository) -> str | None:
    current = repository.status()
    tasks_path = _default_tasks_file()
    if _repo_relative_path(tasks_path) not in _staged_paths(current):
        return None
    try:
        task_id, changes = _task_checkbox_change_reason(tasks_path)
    except ValueError as exc:
        return str(exc)
    if not task_id:
        return None
    record = load_task_state(task_id, root=ROOT)
    if record is None:
        return f"missing state for closed task {task_id}"
    if record.state != TaskLifecycleState.CLOSED:
        return f"task {task_id} must be CLOSED before commit"
    receipt = load_task_receipt(task_id, root=ROOT)
    if receipt is None:
        return f"missing receipt for closed task {task_id}"
    if receipt.state != TaskLifecycleState.CLOSED:
        return f"receipt for {task_id} must be CLOSED before commit"
    if not record.tasks_path:
        return f"task {task_id} is missing tasks.md path in runtime state"
    tasks_path = ROOT / Path(record.tasks_path)
    if not tasks_path.is_file():
        return f"task {task_id} tasks.md is missing"
    checkbox = _task_checkbox(tasks_path, task_id)
    if checkbox != "X":
        return f"task {task_id} must be closed in tasks.md before commit"
    allowed = {*(_repo_relative_path(item) for item in (record.allowlist or ())), _repo_relative_path(tasks_path)}
    observed = _staged_paths(current)
    unexpected = [path for path in sorted(observed) if not _path_allowed(path, allowed)]
    if unexpected:
        return f"unexpected paths outside allowlist: {', '.join(unexpected)}"
    if not _paths_include_tasks_md(observed, tasks_path):
        return f"task {task_id} staged commit must include tasks.md"
    return None


def _validate_pre_push_state(repository: Repository) -> str | None:
    committed_records = [record for record in _task_state_records() if record.state == TaskLifecycleState.COMMITTED]
    if not committed_records:
        return None
    current = repository.status()
    for record in committed_records:
        receipt = load_task_receipt(record.task_id, root=ROOT)
        if receipt is None:
            return f"missing receipt for committed task {record.task_id}"
        if not record.tasks_path:
            return f"task {record.task_id} is missing tasks.md path in runtime state"
        tasks_path = ROOT / Path(record.tasks_path)
        if not tasks_path.is_file():
            return f"task {record.task_id} tasks.md is missing"
        if _task_checkbox(tasks_path, record.task_id) != "X":
            return f"task {record.task_id} must remain closed before push"
        if not receipt.commit_sha or receipt.commit_sha != record.head_sha:
            return f"receipt commit SHA for {record.task_id} does not match runtime state"
        if not _commit_exists(record.head_sha):
            return f"commit {record.head_sha} does not exist for task {record.task_id}"
        if not _is_task_commit_in_history(repository, record.head_sha, current.head_sha):
            return f"commit {record.head_sha} is not an ancestor of current HEAD {current.head_sha}"
        if record.branch and current.branch and record.branch != current.branch:
            return f"branch {current.branch!r} does not match committed task branch {record.branch!r}"
        if record.feature_dir:
            epic_manifest_path = ROOT / ".specify" / "workstreams"
            try:
                epic = workstreams.get_epic(_task_epic(tasks_path, record.task_id), epic_manifest_path)
            except Exception as exc:
                return str(exc)
            expected_branch = str(epic.get("branch") or "")
            if expected_branch and expected_branch != current.branch:
                return f"epic branch {expected_branch!r} does not match current branch {current.branch!r}"
    return None


def _apply_post_commit_updates() -> None:
    repository = Repository(ROOT)
    current = repository.status()
    current_head = current.head_sha or repository.head_sha()
    message = _current_commit_subject()
    task_id = _task_commit_task_id_from_message(message)
    if task_id is None:
        return
    record = load_task_state(task_id, root=ROOT)
    receipt = load_task_receipt(task_id, root=ROOT)
    if record is None:
        raise ValueError(f"missing state for committed task {task_id}")
    if receipt is None:
        raise ValueError(f"missing receipt for committed task {task_id}")
    if record.state == TaskLifecycleState.COMMITTED and receipt.commit_sha == current_head and receipt.state == TaskLifecycleState.COMMITTED:
        return
    if record.state != TaskLifecycleState.CLOSED:
        raise ValueError(f"task {task_id} must be CLOSED before post-commit promotion")
    if receipt.state != TaskLifecycleState.CLOSED:
        raise ValueError(f"receipt for {task_id} must be CLOSED before post-commit promotion")
    tasks_path = ROOT / Path(record.tasks_path) if record.tasks_path else _default_tasks_file()
    if not tasks_path.is_file():
        raise ValueError(f"task {task_id} tasks.md is missing")
    changed_paths = _commit_paths("HEAD")
    allowed = {*(_repo_relative_path(item) for item in (record.allowlist or ())), _repo_relative_path(tasks_path)}
    unexpected = [path for path in sorted(changed_paths) if not _path_allowed(path, allowed)]
    if unexpected:
        raise ValueError(f"commit includes paths outside task allowlist: {', '.join(unexpected)}")
    if _repo_relative_path(tasks_path) not in changed_paths:
        raise ValueError(f"commit for {task_id} must include tasks.md")
    if _task_checkbox(tasks_path, task_id) != "X":
        raise ValueError(f"task {task_id} must be closed in tasks.md before post-commit promotion")
    changed_task_ids = _task_commit_task_ids_in_commit("HEAD")
    if changed_task_ids != [task_id]:
        raise ValueError(f"commit must close exactly task {task_id}")
    updated_receipt = replace(
        receipt,
        updated_at=_timestamp(),
        state=TaskLifecycleState.COMMITTED,
        commit_sha=current_head,
    )
    save_task_receipt(updated_receipt, root=ROOT)
    updated_state = replace(
        record,
        state=TaskLifecycleState.COMMITTED,
        updated_at=_timestamp(),
        branch=current.branch or record.branch,
        head_sha=current_head,
    )
    save_task_state(updated_state, root=ROOT)


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
    reason: str | None = None

    if mode == "post-commit":
        try:
            _apply_post_commit_updates()
        except Exception as exc:
            overall_status = "FAIL"
            reason = _safe_exception_reason(exc)
        return HookRunResult(
            mode=mode,
            status=overall_status,
            global_timeout=False,
            global_timeout_seconds=GLOBAL_TIMEOUTS[mode],
            commands=tuple(results),
            reason=reason,
        )

    repository = Repository(ROOT)
    state_error: str | None = None
    try:
        if mode == "pre-commit":
            state_error = _validate_pre_commit_state(repository)
        elif mode == "pre-push":
            state_error = _validate_pre_push_state(repository)
    except Exception as exc:
        state_error = _safe_exception_reason(exc)
    if state_error is not None:
        return HookRunResult(
            mode=mode,
            status="FAIL",
            global_timeout=False,
            global_timeout_seconds=GLOBAL_TIMEOUTS[mode],
            commands=tuple(results),
            reason=state_error,
        )

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
        reason=reason,
    )


def _render_text(result: HookRunResult) -> str:
    lines = [
        f"mode: {result.mode}",
        f"status: {result.status}",
    ]
    if result.reason:
        lines.append(f"reason: {result.reason}")
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
        f'  "reason": {json.dumps(result.reason, ensure_ascii=False)},',
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
    parser.add_argument("mode", choices=["pre-commit", "pre-push", "post-commit", "ci"])
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

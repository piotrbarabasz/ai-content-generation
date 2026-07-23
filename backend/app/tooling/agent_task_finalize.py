"""Deterministic task finalization checks for agent task completion."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from . import process_runner, repository_checks
from .task_consistency import _field_value as _task_field_value
from .task_consistency import _iter_task_blocks as _task_iter_blocks

ROOT = Path(__file__).resolve().parents[3]
TASKS_FILE = ROOT / "specs" / "001-ai-content-studio" / "tasks.md"
TASK_RUNS_DIR = ROOT / ".specify" / "runtime" / "task-runs"
GLOBAL_TIMEOUT_SECONDS = 180
MANDATORY_COMMAND_TIMEOUT_SECONDS = 20
TASK_COMMAND_TIMEOUT_SECONDS = 120
TASK_ID_PATTERN = re.compile(r"^T\d{3}[A-Z]?$")
FORBIDDEN_SHELL_OPERATORS = ("&&", "||", "|", ">", "<", "`", "\n", "\r")


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    details: dict[str, Any]


@dataclass(frozen=True)
class FinalizeResult:
    status: str
    exit_code: int
    task_id: str
    epic_id: str
    branch: str
    head_sha: str | None
    duration_ms: int
    baseline_path: str | None
    allowlist: tuple[str, ...]
    validation_commands: tuple[str, ...]
    checks: tuple[CheckResult, ...]
    reasons: tuple[str, ...]


class TaskUsageError(ValueError):
    """Raised when the task selector is invalid."""


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _git_snapshot_stage_details(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "branch": snapshot.get("branch", ""),
        "head_sha": snapshot.get("head_sha"),
        "tracked": snapshot.get("tracked", []),
        "staged": snapshot.get("staged", []),
        "untracked": snapshot.get("untracked", []),
        "deleted": snapshot.get("deleted", []),
        "renamed": snapshot.get("renamed", []),
        "reason": snapshot.get("reason", ""),
        "duration_ms": snapshot.get("duration_ms", 0),
    }


def _make_check(name: str, status: str, **details: Any) -> CheckResult:
    return CheckResult(name=name, status=status, details=details)


def _stage_check(name: str, started: float, status: str, **details: Any) -> CheckResult:
    payload = dict(details)
    payload.setdefault("duration_ms", _elapsed_ms(started))
    return _make_check(name, status, **payload)


def _git_env() -> dict[str, str]:
    env = dict()
    env.update({"GIT_PAGER": "cat", "PAGER": "cat", "TERM": "dumb", "PYTHONUNBUFFERED": "1"})
    return env


def _task_argument(value: str) -> str:
    value = value.strip()
    if not TASK_ID_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("task must match T### or T###A")
    return value


def _is_none_value(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        normalized == "none"
        or normalized.startswith("none ")
        or normalized.startswith("none(")
        or normalized.startswith("none.")
        or normalized.startswith("none,")
        or normalized.startswith("none:")
        or normalized.startswith("none;")
        or normalized.startswith("none-")
        or normalized == "n/a"
        or normalized == "na"
        or normalized == "[]"
    )


def _split_comma_list(value: str) -> list[str]:
    if _is_none_value(value):
        return []
    items: list[str] = []
    for raw_item in value.split(","):
        item = raw_item.strip().strip("`")
        if item:
            items.append(item)
    return items


def _split_validation_commands(value: str) -> list[str]:
    if _is_none_value(value):
        return []
    commands: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in value:
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
            current.append(char)
            continue
        if char == ";":
            command = "".join(current).strip().strip("`").strip()
            if command:
                commands.append(command)
            current = []
            continue
        current.append(char)
    if quote is not None:
        raise ValueError(f"validation commands contain unterminated quote: {value!r}")
    command = "".join(current).strip().strip("`").strip()
    if command:
        commands.append(command)
    return commands


def _load_task_block(task_id: str) -> tuple[int, list[tuple[int, str]]]:
    if not TASKS_FILE.is_file():
        raise FileNotFoundError(f"tasks file does not exist: {TASKS_FILE}")
    for found_task_id, start_line, lines in _task_iter_blocks(TASKS_FILE):
        if found_task_id == task_id:
            return start_line, lines
    raise FileNotFoundError(f"task does not exist in tasks.md: {task_id}")


def _task_field(lines: list[tuple[int, str]], field_name: str) -> tuple[int, str] | None:
    return _task_field_value(lines, field_name)


def _load_task_context(task_id: str) -> dict[str, Any]:
    start_line, lines = _load_task_block(task_id)
    epic = _task_field(lines, "Epic:")
    milestone = _task_field(lines, "Milestone:")
    implementation = _task_field(lines, "Implementation files:")
    test_files = _task_field(lines, "Test files:")
    validation_commands = _task_field(lines, "Validation commands:")
    if epic is None:
        raise ValueError(f"{TASKS_FILE.name}:{start_line}: task {task_id} does not declare an epic")
    if milestone is None:
        raise ValueError(f"{TASKS_FILE.name}:{start_line}: task {task_id} does not declare a milestone")
    if implementation is None:
        raise ValueError(f"{TASKS_FILE.name}:{start_line}: task {task_id} does not declare implementation files")
    if test_files is None:
        raise ValueError(f"{TASKS_FILE.name}:{start_line}: task {task_id} does not declare test files")
    if validation_commands is None:
        raise ValueError(f"{TASKS_FILE.name}:{start_line}: task {task_id} does not declare validation commands")

    implementation_files = tuple(_split_comma_list(implementation[1]))
    test_file_list = tuple(_split_comma_list(test_files[1]))
    commands = tuple(_split_validation_commands(validation_commands[1]))
    return {
        "task_id": task_id,
        "task_line": start_line,
        "epic_id": epic[1].strip(),
        "epic_line": epic[0],
        "milestone_id": milestone[1].strip(),
        "milestone_line": milestone[0],
        "implementation_files": implementation_files,
        "test_files": test_file_list,
        "allowlist": tuple(list(implementation_files) + list(test_file_list)),
        "validation_commands": commands,
    }


def _baseline_path(task_id: str) -> Path:
    return TASK_RUNS_DIR / task_id / "baseline.json"


def _load_json_mapping(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"baseline does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name}: baseline must be a JSON object")
    return loaded


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").strip()


def _path_allowed(path: str, allowlist: Sequence[str]) -> bool:
    normalized_path = _normalize_path(path)
    if not normalized_path:
        return False
    for item in allowlist:
        normalized_item = _normalize_path(item).rstrip("/")
        if not normalized_item:
            continue
        if normalized_path == normalized_item:
            return True
        if normalized_path.startswith(f"{normalized_item}/"):
            return True
    return False


def _snapshot_paths(snapshot: dict[str, Any], *, exclude: str | None = None) -> set[str]:
    excluded = _normalize_path(exclude) if exclude else None
    paths: set[str] = set()
    for key in ("tracked", "staged", "untracked", "deleted"):
        for path in snapshot.get(key, []):
            normalized = _normalize_path(str(path))
            if not normalized or normalized == excluded:
                continue
            paths.add(normalized)
    for item in snapshot.get("renamed", []):
        if isinstance(item, dict):
            for key in ("old", "new"):
                normalized = _normalize_path(str(item.get(key) or ""))
                if normalized and normalized != excluded:
                    paths.add(normalized)
    return paths


def _baseline_conflicts(baseline: dict[str, Any], allowlist: Sequence[str]) -> list[str]:
    paths: list[str] = []
    for key in ("tracked", "staged", "untracked"):
        for path in baseline.get(key, []) or []:
            normalized = _normalize_path(str(path))
            if normalized and _path_allowed(normalized, allowlist):
                paths.append(normalized)
    return sorted(set(paths))


def _safe_validation_command_argv(command: str) -> list[str]:
    normalized = command.strip().strip("`").strip()
    if not normalized:
        raise ValueError("validation command is empty")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError(f"validation command contains newline injection: {command!r}")
    if "`" in normalized:
        raise ValueError(f"validation command contains backticks: {command!r}")
    for operator in FORBIDDEN_SHELL_OPERATORS:
        if operator in normalized:
            raise ValueError(f"validation command contains forbidden shell operator {operator!r}: {command!r}")
    argv = shlex.split(normalized, posix=False)
    if not argv:
        raise ValueError(f"validation command is empty: {command!r}")
    return argv


def _normalize_python_launcher(argv: Sequence[str]) -> tuple[list[str], bool]:
    if not argv:
        return [], False
    launcher = argv[0].lower()
    if launcher in {"python", "python3"}:
        normalized = [sys.executable, *argv[1:]]
    elif launcher == "py" and len(argv) >= 2 and argv[1] in {"-3", "-3.11"}:
        normalized = [sys.executable, *argv[2:]]
    else:
        normalized = list(argv)
    broad_validation = len(normalized) >= 3 and normalized[0] == sys.executable and normalized[1] == "-m" and normalized[2] == "pytest"
    return normalized, broad_validation


def _is_diff_check_command(argv: Sequence[str]) -> bool:
    parts = list(argv)
    if parts[:3] == ["git", "diff", "--check"]:
        return True
    if parts[:4] == ["git", "--no-pager", "diff", "--check"]:
        return True
    return False


def _run_process(argv: Sequence[str], *, timeout_seconds: int, total_deadline: float) -> process_runner.ProcessResult:
    return process_runner.run_process(
        argv,
        cwd=ROOT,
        timeout_seconds=timeout_seconds,
        total_deadline=total_deadline,
        heartbeat_seconds=30,
    )


def _process_summary(name: str, result: process_runner.ProcessResult, timeout_seconds: int, *, skipped_duplicate: bool = False) -> CheckResult:
    details: dict[str, Any] = {
        "exit_code": result.exit_code,
        "timeout_seconds": timeout_seconds,
        "duration_ms": result.duration_ms,
        "timed_out": result.timed_out,
        "process_tree_killed": result.process_tree_killed,
        "pid": result.pid,
        "skipped_duplicate": skipped_duplicate,
    }
    if result.status != "PASS":
        details["stdout_lines"] = list(result.stdout_lines)
        details["stderr_lines"] = list(result.stderr_lines)
        details["output_truncated"] = result.output_truncated
    return _make_check(name, result.status, **details)


def _task_metadata_check(task_id: str) -> CheckResult:
    started = time.perf_counter()
    findings = repository_checks.task_metadata([task_id])
    status = "PASS" if not findings else "FAIL"
    return _stage_check(
        "task_metadata_validation",
        started,
        status,
        exit_code=0 if status == "PASS" else 1,
        findings=findings,
    )


def _load_baseline(task_id: str) -> tuple[dict[str, Any], Path]:
    path = _baseline_path(task_id)
    if not path.is_file():
        raise FileNotFoundError(f"baseline does not exist: {path}")
    baseline = _load_json_mapping(path)
    return baseline, path


def _validate_task_commands(
    commands: Sequence[str],
    *,
    deadline: float,
) -> tuple[CheckResult, bool, bool]:
    skipped_duplicate = False
    broad_validation = False
    command_results: list[dict[str, Any]] = []
    for command in commands:
        try:
            argv = _safe_validation_command_argv(command)
        except ValueError as exc:
            return (
                _make_check(
                    "task_validation_commands",
                    "FAIL",
                    skipped=False,
                    blocked_by="unsafe_validation_command",
                    reason=str(exc),
                    commands=command_results,
                    broad_validation=broad_validation,
                    skipped_duplicate=skipped_duplicate,
                    exit_code=1,
                    duration_ms=0,
                    timed_out=False,
                    process_tree_killed=False,
                ),
                skipped_duplicate,
                broad_validation,
            )
        if _is_diff_check_command(argv):
            skipped_duplicate = True
            continue
        argv, is_broad = _normalize_python_launcher(argv)
        broad_validation = broad_validation or is_broad
        if time.monotonic() >= deadline:
            result = _make_check(
                "task_validation_commands",
                "TIMEOUT",
                skipped=False,
                blocked_by="global_timeout",
                broad_validation=broad_validation,
                skipped_duplicate=skipped_duplicate,
                commands=command_results,
                reason="global timeout reached before task validation commands could run",
            )
            return result, skipped_duplicate, broad_validation
        process_result = _run_process(argv, timeout_seconds=TASK_COMMAND_TIMEOUT_SECONDS, total_deadline=deadline)
        command_results.append(
            {
                "command": list(process_result.command),
                "status": process_result.status,
                "exit_code": process_result.exit_code,
                "duration_ms": process_result.duration_ms,
                "timed_out": process_result.timed_out,
                "process_tree_killed": process_result.process_tree_killed,
                "pid": process_result.pid,
                "output_truncated": process_result.output_truncated,
                "stdout_lines": list(process_result.stdout_lines) if process_result.status != "PASS" else [],
                "stderr_lines": list(process_result.stderr_lines) if process_result.status != "PASS" else [],
            }
        )
        if process_result.status != "PASS":
            return (
                _make_check(
                    "task_validation_commands",
                    process_result.status,
                    skipped=False,
                    blocked_by=None,
                    broad_validation=broad_validation,
                    skipped_duplicate=skipped_duplicate,
                    commands=command_results,
                    blocking_command=list(process_result.command),
                    exit_code=process_result.exit_code,
                    duration_ms=process_result.duration_ms,
                    timed_out=process_result.timed_out,
                    process_tree_killed=process_result.process_tree_killed,
                    reason=f"validation command failed: {' '.join(process_result.command)}",
                ),
                skipped_duplicate,
                broad_validation,
            )
    return (
        _make_check(
            "task_validation_commands",
            "PASS",
            skipped=False,
            blocked_by=None,
            broad_validation=broad_validation,
            skipped_duplicate=skipped_duplicate,
            commands=command_results,
            exit_code=0,
            duration_ms=0 if not command_results else sum(item["duration_ms"] for item in command_results),
            timed_out=False,
            process_tree_killed=False,
            reason="validation commands completed",
        ),
        skipped_duplicate,
        broad_validation,
    )


def _build_blocking_check(checks: Sequence[CheckResult]) -> str | None:
    for check in checks:
        if check.status in {"FAIL", "TIMEOUT"}:
            return check.name
    return None


def _summarize_status_checks(checks: Sequence[CheckResult]) -> tuple[str, int]:
    if any(check.status == "TIMEOUT" for check in checks):
        return "TIMEOUT", 3
    if any(check.status == "FAIL" for check in checks):
        return "FAIL", 1
    return "PASS", 0


def _snapshot_paths_changed(snapshot: dict[str, Any], baseline_path: str) -> set[str]:
    return _snapshot_paths(snapshot, exclude=baseline_path)


def run_finalize(task_id: str) -> FinalizeResult:
    started_perf = time.perf_counter()
    deadline = time.monotonic() + GLOBAL_TIMEOUT_SECONDS
    task_context = _load_task_context(task_id)
    baseline, baseline_path = _load_baseline(task_id)
    baseline_rel_path = baseline_path.relative_to(ROOT).as_posix()

    checks: list[CheckResult] = []
    reasons: list[str] = []

    baseline_task = str(baseline.get("task") or "")
    baseline_epic = str(baseline.get("epic") or "")
    baseline_branch = str(baseline.get("branch") or "")
    baseline_head_sha = str(baseline.get("head_sha") or "")
    allowlist = task_context["allowlist"]
    task_validation_commands = task_context["validation_commands"]

    baseline_started = time.perf_counter()
    baseline_errors: list[str] = []
    if baseline_task != task_id:
        baseline_errors.append(f"baseline task is {baseline_task!r}, expected {task_id!r}")
    if baseline_epic != task_context["epic_id"]:
        baseline_errors.append(f"baseline epic is {baseline_epic!r}, expected {task_context['epic_id']!r}")
    if not baseline_branch:
        baseline_errors.append("baseline branch is missing")
    if not baseline_head_sha:
        baseline_errors.append("baseline head SHA is missing")
    baseline_check = _stage_check(
        "baseline",
        baseline_started,
        "FAIL" if baseline_errors else "PASS",
        path=str(baseline_path),
        task=baseline_task,
        epic=baseline_epic,
        branch=baseline_branch,
        head_sha=baseline_head_sha,
        errors=baseline_errors,
    )
    checks.append(baseline_check)
    reasons.extend(baseline_errors)

    snapshot_started = time.perf_counter()
    current = repository_checks.capture_git_snapshot(
        timeout_seconds=MANDATORY_COMMAND_TIMEOUT_SECONDS,
        total_deadline=deadline,
    )
    if str(current.get("status") or "FAIL") != "PASS":
        snapshot_check = _stage_check(
            "git_snapshot",
            snapshot_started,
            str(current.get("status") or "FAIL"),
            **_git_snapshot_stage_details(current),
        )
        checks.append(snapshot_check)
        snapshot_status, snapshot_exit_code = _summarize_status_checks([snapshot_check])
        reasons.append(f"blocking check failed: {snapshot_check.name}")
        return FinalizeResult(
            status=snapshot_status,
            exit_code=snapshot_exit_code,
            task_id=task_id,
            epic_id=task_context["epic_id"],
            branch=current.get("branch") or baseline_branch,
            head_sha=current.get("head_sha") or None,
            duration_ms=_elapsed_ms(started_perf),
            baseline_path=baseline_path.as_posix(),
            allowlist=task_context["allowlist"],
            validation_commands=task_context["validation_commands"],
            checks=tuple(checks),
            reasons=tuple(reasons),
        )

    head_started = time.perf_counter()
    head_errors: list[str] = []
    current_head_sha = str(current.get("head_sha") or "")
    if baseline_head_sha and current_head_sha != baseline_head_sha:
        head_errors.append(f"current HEAD {current_head_sha!r} does not match baseline {baseline_head_sha!r}")
    head_check = _stage_check(
        "head",
        head_started,
        "FAIL" if head_errors else "PASS",
        current=current_head_sha,
        baseline=baseline_head_sha,
        errors=head_errors,
    )
    checks.append(head_check)
    reasons.extend(head_errors)

    branch_started = time.perf_counter()
    branch_errors: list[str] = []
    current_branch = str(current.get("branch") or "")
    if baseline_branch and current_branch != baseline_branch:
        branch_errors.append(f"current branch {current_branch!r} does not match baseline {baseline_branch!r}")
    branch_check = _stage_check(
        "branch",
        branch_started,
        "FAIL" if branch_errors else "PASS",
        current=current_branch,
        baseline=baseline_branch,
        errors=branch_errors,
    )
    checks.append(branch_check)
    reasons.extend(branch_errors)

    baseline_conflicts_started = time.perf_counter()
    baseline_conflicts = _baseline_conflicts(baseline, allowlist)
    baseline_conflicts_check = _stage_check(
        "baseline_conflicts",
        baseline_conflicts_started,
        "PASS" if not baseline_conflicts else "FAIL",
        conflicts=baseline_conflicts,
        allowlist=list(allowlist),
    )
    checks.append(baseline_conflicts_check)
    if baseline_conflicts:
        reasons.append(f"baseline conflicts with pre-existing dirty paths: {', '.join(baseline_conflicts)}")

    allowlist_started = time.perf_counter()
    allowlist_errors: list[str] = []
    if not task_context["epic_id"]:
        allowlist_errors.append("task does not declare an epic")
    if not TASK_ID_PATTERN.fullmatch(task_id):
        allowlist_errors.append(f"invalid task id {task_id!r}")
    allowlist_check = _stage_check(
        "allowlist",
        allowlist_started,
        "PASS" if not allowlist_errors else "FAIL",
        epic=task_context["epic_id"],
        milestone=task_context["milestone_id"],
        implementation_files=list(task_context["implementation_files"]),
        test_files=list(task_context["test_files"]),
        allowlist=list(allowlist),
        validation_commands=list(task_validation_commands),
        errors=allowlist_errors,
    )
    checks.append(allowlist_check)
    reasons.extend(allowlist_errors)

    scope_started = time.perf_counter()
    baseline_paths = _snapshot_paths(baseline, exclude=baseline_rel_path)
    current_paths = _snapshot_paths(current, exclude=baseline_rel_path)
    unexpected_added = sorted(path for path in current_paths - baseline_paths if not _path_allowed(path, allowlist))
    unexpected_removed = sorted(path for path in baseline_paths - current_paths if not _path_allowed(path, allowlist))
    scope_errors: list[str] = []
    if unexpected_added:
        scope_errors.append(f"unexpected added paths: {', '.join(unexpected_added)}")
    if unexpected_removed:
        scope_errors.append(f"unexpected removed paths: {', '.join(unexpected_removed)}")
    scope_check = _stage_check(
        "scope_drift",
        scope_started,
        "PASS" if not scope_errors else "FAIL",
        baseline_paths=sorted(baseline_paths),
        current_paths=sorted(current_paths),
        added_paths=sorted(current_paths - baseline_paths),
        removed_paths=sorted(baseline_paths - current_paths),
        unexpected_added=unexpected_added,
        unexpected_removed=unexpected_removed,
        allowlist=list(allowlist),
        baseline_path=baseline_rel_path,
        errors=scope_errors,
    )
    checks.append(scope_check)
    reasons.extend(scope_errors)

    metadata_check = _task_metadata_check(task_id)
    checks.append(metadata_check)
    if metadata_check.status != "PASS":
        reasons.append(f"blocking check failed: {metadata_check.name}")
        reasons.extend(
            str(finding.get("reason") or "")
            for finding in metadata_check.details.get("findings", [])
            if finding.get("reason")
        )

    diff_started = time.perf_counter()
    diff_result = _run_process(
        ["git", "--no-pager", "diff", "--check"],
        timeout_seconds=MANDATORY_COMMAND_TIMEOUT_SECONDS,
        total_deadline=deadline,
    )
    diff_check = _process_summary("git_diff_check", diff_result, MANDATORY_COMMAND_TIMEOUT_SECONDS)
    diff_check = _stage_check("git_diff_check", diff_started, diff_check.status, **diff_check.details)
    checks.append(diff_check)
    if diff_check.status != "PASS":
        reasons.append("blocking check failed: git_diff_check")

    blocking_check = _build_blocking_check(checks[:-1])
    if metadata_check.status != "PASS":
        blocking_check = metadata_check.name if blocking_check is None else blocking_check
    if diff_check.status != "PASS":
        blocking_check = blocking_check or diff_check.name

    task_command_check: CheckResult
    if blocking_check is not None:
        task_command_check = _make_check(
            "task_validation_commands",
            "FAIL",
            skipped=True,
            blocked_by=blocking_check,
            reason="task validation commands were not executed because a mandatory or earlier check failed",
            commands=[],
            broad_validation=False,
            skipped_duplicate=False,
        )
    else:
        if time.monotonic() >= deadline:
            task_command_check = _make_check(
                "task_validation_commands",
                "TIMEOUT",
                skipped=False,
                blocked_by="global_timeout",
                reason="global timeout reached before task validation commands could run",
                commands=[],
                broad_validation=False,
                skipped_duplicate=False,
            )
        elif task_validation_commands:
            task_command_check, skipped_duplicate, broad_validation = _validate_task_commands(task_validation_commands, deadline=deadline)
            task_command_check = _make_check(
                task_command_check.name,
                task_command_check.status,
                **{**task_command_check.details, "skipped_duplicate": skipped_duplicate, "broad_validation": broad_validation},
            )
        else:
            task_command_check = _make_check(
                "task_validation_commands",
                "PASS",
                skipped=False,
                blocked_by=None,
                reason="no task validation commands declared",
                commands=[],
                broad_validation=False,
                skipped_duplicate=False,
                exit_code=0,
                duration_ms=0,
                timed_out=False,
                process_tree_killed=False,
            )
    checks.append(task_command_check)
    if task_command_check.status != "PASS" and not task_command_check.details.get("skipped"):
        task_reason = task_command_check.details.get("reason")
        if isinstance(task_reason, str) and task_reason:
            reasons.append(task_reason)

    if any(check.status == "TIMEOUT" for check in checks):
        status = "TIMEOUT"
        exit_code = 3
    elif reasons or any(check.status == "FAIL" for check in checks):
        status = "FAIL"
        exit_code = 1
    else:
        status = "PASS"
        exit_code = 0

    return FinalizeResult(
        status=status,
        exit_code=exit_code,
        task_id=task_id,
        epic_id=task_context["epic_id"],
        branch=current_branch,
        head_sha=current_head_sha,
        duration_ms=_elapsed_ms(started_perf),
        baseline_path=baseline_path.as_posix(),
        allowlist=allowlist,
        validation_commands=task_validation_commands,
        checks=tuple(checks),
        reasons=tuple(reasons),
    )


def _payload(result: FinalizeResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "exit_code": result.exit_code,
        "task": result.task_id,
        "epic": result.epic_id,
        "branch": result.branch,
        "head_sha": result.head_sha,
        "duration_ms": result.duration_ms,
        "baseline_path": result.baseline_path,
        "allowlist": list(result.allowlist),
        "validation_commands": list(result.validation_commands),
        "checks": [
            {"name": check.name, "status": check.status, "details": check.details}
            for check in result.checks
        ],
        "reasons": list(result.reasons),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backend.app.tooling.agent_task_finalize")
    parser.add_argument("--task", required=True, type=_task_argument)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code)

    try:
        result = run_finalize(args.task)
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        result = FinalizeResult(
            status="FAIL",
            exit_code=1,
            task_id=args.task,
            epic_id="",
            branch="",
            head_sha=None,
            duration_ms=0,
            baseline_path=_baseline_path(args.task).as_posix(),
            allowlist=(),
            validation_commands=(),
            checks=(CheckResult(name="baseline", status="FAIL", details={"error": str(exc)}),),
            reasons=(str(exc),),
        )
    print(json.dumps(_payload(result), ensure_ascii=False, indent=2))
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())

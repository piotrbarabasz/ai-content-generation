"""Deterministic task finalization checks for agent task completion."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from . import repository_checks
from .task_consistency import _field_value as _task_field_value
from .task_consistency import _iter_task_blocks as _task_iter_blocks

ROOT = Path(__file__).resolve().parents[3]
TASKS_FILE = ROOT / "specs" / "001-ai-content-studio" / "tasks.md"
TASK_RUNS_DIR = ROOT / ".specify" / "runtime" / "task-runs"
TIMEOUT_SECONDS = 20
TASK_ID_PATTERN = re.compile(r"^T\d{3}[A-Z]?$")
FORBIDDEN_SHELL_OPERATORS = ("&&", "||", ";", "|", ">", "<")


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
    baseline_path: str | None
    allowlist: tuple[str, ...]
    validation_commands: tuple[str, ...]
    checks: tuple[CheckResult, ...]
    reasons: tuple[str, ...]


class TaskUsageError(ValueError):
    """Raised when the task selector is invalid."""


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({"GIT_PAGER": "cat", "PAGER": "cat", "TERM": "dumb"})
    return env


def _run_git(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=ROOT,
        shell=False,
        timeout=TIMEOUT_SECONDS,
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )


def _git_stdout(command: Sequence[str]) -> str:
    result = _run_git(command)
    if result.returncode != 0:
        output = (result.stdout or "").strip() or (result.stderr or "").strip()
        raise RuntimeError(f"git command failed: {' '.join(command)}: {output or 'unknown error'}")
    return result.stdout.strip()


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


def _current_snapshot() -> dict[str, Any]:
    return {
        "head_sha": _git_stdout(["git", "rev-parse", "HEAD"]),
        "branch": _git_stdout(["git", "branch", "--show-current"]),
        "tracked": _git_stdout(["git", "diff", "--name-only"]).splitlines(),
        "staged": _git_stdout(["git", "diff", "--cached", "--name-only"]).splitlines(),
        "untracked": _git_stdout(["git", "ls-files", "--others", "--exclude-standard"]).splitlines(),
    }


def _path_allowed(path: str, allowlist: Sequence[str]) -> bool:
    normalized_path = path.replace("\\", "/").strip()
    if not normalized_path:
        return False
    for item in allowlist:
        normalized_item = item.replace("\\", "/").strip().rstrip("/")
        if not normalized_item:
            continue
        if normalized_path == normalized_item:
            return True
        if normalized_path.startswith(f"{normalized_item}/"):
            return True
    return False


def _scoped_paths(snapshot: dict[str, Any], *, exclude: str | None = None) -> set[str]:
    excluded = _normalize_path(exclude) if exclude else None
    paths: set[str] = set()
    for key in ("tracked", "staged", "untracked"):
        for path in snapshot.get(key, []):
            normalized = _normalize_path(str(path))
            if not normalized or normalized == excluded:
                continue
            paths.add(normalized)
    return paths


def _baseline_conflicts(baseline: dict[str, Any], allowlist: Sequence[str]) -> list[str]:
    paths = _scoped_paths(baseline)
    return sorted(path for path in paths if _path_allowed(path, allowlist))


def _make_check(name: str, status: str, **details: Any) -> CheckResult:
    return CheckResult(name=name, status=status, details=details)


def _command_status(status: str) -> str:
    if status == "TIMEOUT":
        return "TIMEOUT"
    if status == "PASS":
        return "PASS"
    return "FAIL"


def _safe_validation_command_argv(command: str) -> list[str]:
    normalized = command.strip()
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


def _run_command_check(name: str, command: Sequence[str]) -> CheckResult:
    run = repository_checks.run_process(list(command))
    status = _command_status(str(run.get("status") or "FAIL"))
    details: dict[str, Any] = {
        "exit_code": run.get("exit_code"),
    }
    if status != "PASS":
        details["output_lines"] = run.get("output_lines", [])
        details["truncated"] = run.get("truncated", False)
    return _make_check(name, status, **details)


def _load_baseline(task_id: str) -> tuple[dict[str, Any], Path]:
    path = _baseline_path(task_id)
    if not path.is_file():
        raise FileNotFoundError(f"baseline does not exist: {path}")
    baseline = _load_json_mapping(path)
    return baseline, path


def _mandatory_checks(task_id: str) -> tuple[list[CheckResult], str | None, str | None]:
    checks = [
        _run_command_check(
            "task_metadata_validation",
            [
                sys.executable,
                "-m",
                "backend.app.tooling.repository_checks",
                "--mode",
                "task-metadata",
                "--tasks",
                task_id,
                "--json",
            ],
        ),
        _run_command_check(
            "git_diff_check",
            ["git", "--no-pager", "diff", "--check"],
        ),
    ]
    blocking_status: str | None = None
    blocking_check: str | None = None
    for check in checks:
        if check.status == "TIMEOUT":
            blocking_status = "TIMEOUT"
            blocking_check = check.name
        elif check.status != "PASS" and blocking_status is None:
            blocking_status = "FAIL"
            blocking_check = check.name
    if blocking_check:
        for check in checks:
            if check.name == blocking_check:
                check.details["blocking_check"] = blocking_check
                break
    return checks, blocking_status, blocking_check


def _task_validation_results(commands: Sequence[str]) -> tuple[CheckResult, str | None, str | None]:
    results: list[dict[str, Any]] = []
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
                    command=command,
                    exit_code=None,
                    commands=[],
                ),
                "FAIL",
                command,
            )
        run = repository_checks.run_process(list(argv))
        results.append(run)
        status = _command_status(str(run.get("status") or "FAIL"))
        if status != "PASS":
            details: dict[str, Any] = {
                "skipped": False,
                "blocked_by": None,
                "command": command,
                "exit_code": run.get("exit_code"),
                "commands": results,
            }
            if status != "PASS":
                details["reason"] = str(run.get("output_lines") or [])
            return _make_check("task_validation_commands", status, **details), status, command
    return (
        _make_check(
            "task_validation_commands",
            "PASS",
            skipped=False,
            blocked_by=None,
            reason="validation commands completed",
            exit_code=0,
            commands=results,
        ),
        "PASS",
        None,
    )


def _skipped_task_validation_check(blocked_by: str, reason: str) -> CheckResult:
    return _make_check(
        "task_validation_commands",
        "FAIL",
        skipped=True,
        blocked_by=blocked_by,
        reason=reason,
        commands=[],
    )


def run_finalize(task_id: str) -> FinalizeResult:
    task_context = _load_task_context(task_id)
    baseline, baseline_path = _load_baseline(task_id)
    current = _current_snapshot()

    reasons: list[str] = []
    checks: list[CheckResult] = []

    baseline_task = str(baseline.get("task") or "")
    baseline_epic = str(baseline.get("epic") or "")
    baseline_branch = str(baseline.get("branch") or "")
    baseline_head_sha = str(baseline.get("head_sha") or "")
    baseline_rel_path = baseline_path.relative_to(ROOT).as_posix()
    baseline_paths = _scoped_paths(baseline, exclude=baseline_rel_path)
    current_paths = _scoped_paths(current, exclude=baseline_rel_path)
    allowlist = task_context["allowlist"]
    task_validation_commands = task_context["validation_commands"]
    baseline_dirty_paths = _scoped_paths(baseline)

    if baseline_task != task_id:
        reasons.append(f"baseline task is {baseline_task!r}, expected {task_id!r}")
    if baseline_epic != task_context["epic_id"]:
        reasons.append(f"baseline epic is {baseline_epic!r}, expected {task_context['epic_id']!r}")
    if not baseline_branch:
        reasons.append("baseline branch is missing")

    checks.append(
        _make_check(
            "baseline",
            "PASS" if not reasons else "FAIL",
            path=str(baseline_path),
            task=baseline_task,
            epic=baseline_epic,
            branch=baseline_branch,
            head_sha=baseline_head_sha,
        )
    )

    if not baseline_head_sha:
        reasons.append("baseline head SHA is missing")
    elif current["head_sha"] != baseline_head_sha:
        reasons.append(f"current HEAD {current['head_sha']!r} does not match baseline {baseline_head_sha!r}")
    checks.append(
        _make_check(
            "head",
            "PASS" if not reasons else "FAIL",
            current=current["head_sha"],
            baseline=baseline_head_sha,
        )
    )

    branch_reasons: list[str] = []
    if not baseline_branch:
        branch_reasons.append("baseline branch is missing")
    elif current["branch"] != baseline_branch:
        branch_reasons.append(f"current branch {current['branch']!r} does not match baseline {baseline_branch!r}")
    checks.append(
        _make_check(
            "branch",
            "PASS" if not branch_reasons else "FAIL",
            current=current["branch"],
            baseline=baseline_branch,
        )
    )
    reasons.extend(branch_reasons)

    baseline_conflicts = _baseline_conflicts(baseline, allowlist)
    if baseline_conflicts:
        reasons.append(f"baseline conflicts with pre-existing dirty paths: {', '.join(baseline_conflicts)}")
    checks.append(
        _make_check(
            "baseline_conflicts",
            "PASS" if not baseline_conflicts else "FAIL",
            conflicts=baseline_conflicts,
            baseline_paths=sorted(baseline_dirty_paths),
            allowlist=list(allowlist),
        )
    )

    allowlist_reasons: list[str] = []
    if not task_context["epic_id"]:
        allowlist_reasons.append("task does not declare an epic")
    elif not TASK_ID_PATTERN.fullmatch(task_id):
        allowlist_reasons.append(f"invalid task id {task_id!r}")
    checks.append(
        _make_check(
            "allowlist",
            "PASS" if not allowlist_reasons else "FAIL",
            epic=task_context["epic_id"],
            milestone=task_context["milestone_id"],
            implementation_files=list(task_context["implementation_files"]),
            test_files=list(task_context["test_files"]),
            allowlist=list(allowlist),
            validation_commands=list(task_validation_commands),
        )
    )
    reasons.extend(allowlist_reasons)

    baseline_only = baseline_paths - current_paths
    current_only = current_paths - baseline_paths
    unexpected_added = sorted(path for path in current_only if not _path_allowed(path, allowlist))
    unexpected_removed = sorted(path for path in baseline_only if not _path_allowed(path, allowlist))
    scope_reasons: list[str] = []
    if unexpected_added:
        scope_reasons.append(f"unexpected added paths: {', '.join(unexpected_added)}")
    if unexpected_removed:
        scope_reasons.append(f"unexpected removed paths: {', '.join(unexpected_removed)}")
    checks.append(
        _make_check(
            "scope_drift",
            "PASS" if not scope_reasons else "FAIL",
            baseline_paths=sorted(baseline_paths),
            current_paths=sorted(current_paths),
            added_paths=sorted(current_only),
            removed_paths=sorted(baseline_only),
            unexpected_added=unexpected_added,
            unexpected_removed=unexpected_removed,
            allowlist=list(allowlist),
            baseline_path=baseline_rel_path,
        )
    )
    reasons.extend(scope_reasons)

    mandatory_checks, mandatory_status, blocking_check = _mandatory_checks(task_id)
    checks.extend(mandatory_checks)
    if blocking_check:
        reasons.append(f"blocking check failed: {blocking_check}")

    if not reasons and mandatory_status is None:
        if task_validation_commands:
            task_command_check, task_command_status, task_command_blocking_command = _task_validation_results(task_validation_commands)
        else:
            task_command_check = _make_check(
                "task_validation_commands",
                "PASS",
                skipped=False,
                blocked_by=None,
                reason="no task validation commands declared",
                commands=[],
            )
            task_command_status = "PASS"
            task_command_blocking_command = None
    else:
        blocked_by = blocking_check or "pre-existing validation failure"
        task_command_check = _skipped_task_validation_check(
            blocked_by,
            "task validation commands were not executed because a mandatory or earlier check failed",
        )
        task_command_status = "FAIL"
        task_command_blocking_command = None

    checks.append(task_command_check)
    if task_command_check.status != "PASS" and not task_command_check.details.get("skipped"):
        task_reason = task_command_check.details.get("reason")
        if isinstance(task_reason, str) and task_reason:
            reasons.append(task_reason)
    if task_command_blocking_command:
        reasons.append(f"blocking task validation command: {task_command_blocking_command}")

    if mandatory_status == "TIMEOUT" or task_command_status == "TIMEOUT":
        exit_code = 3
        status = "TIMEOUT"
    elif mandatory_status == "FAIL" or reasons or task_command_status != "PASS":
        exit_code = 1
        status = "FAIL"
    else:
        exit_code = 0
        status = "PASS"

    return FinalizeResult(
        status=status,
        exit_code=exit_code,
        task_id=task_id,
        epic_id=task_context["epic_id"],
        branch=current["branch"],
        head_sha=current["head_sha"],
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

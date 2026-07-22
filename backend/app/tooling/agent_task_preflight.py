"""Deterministic task preflight runner for agent task launches."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from . import repository_checks, workstream_validation
from .task_consistency import _iter_task_blocks as _task_iter_blocks
from .workstream_validation import _load_yaml_manifest as _load_workstream_manifest

ROOT = Path(__file__).resolve().parents[3]
ACTIVE_EPIC_FILE = ROOT / ".specify" / "runtime" / "active-epic"
WORKSTREAMS_DIR = ROOT / ".specify" / "workstreams"
TASKS_FILE = ROOT / "specs" / "001-ai-content-studio" / "tasks.md"
TASK_RUNS_DIR = ROOT / ".specify" / "runtime" / "task-runs"
TIMEOUT_SECONDS = 20
TASK_ID_PATTERN = re.compile(r"^T\d{3}[A-Z]?$")

DEPENDENCY_REASONS = (
    "unknown dependency task",
    "dependency task does not declare an epic",
    "does not depend on epic",
    "task dependency cycle",
)
OWNERSHIP_REASONS = (
    "task is omitted from all epic manifests",
    "task is present in multiple epic manifests",
    "task is listed in the wrong epic manifest",
    "task is not listed by its epic manifest",
    "unknown epic",
    "task does not declare an epic",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    details: dict[str, Any]


@dataclass(frozen=True)
class PreflightResult:
    status: str
    exit_code: int
    selector: str
    task_id: str
    epic_id: str
    branch: str
    head_sha: str | None
    checks: tuple[CheckResult, ...]
    baseline_path: str | None


class SelectorUsageError(ValueError):
    """Raised when the selector syntax is not supported."""


class TaskValidationError(ValueError):
    """Raised when the selected task is not eligible for preflight."""


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


def _python_version_check(version_info: tuple[int, int, int] | None = None) -> CheckResult:
    version = version_info or sys.version_info[:3]
    ok = (version[0], version[1]) >= (3, 11)
    return CheckResult(
        name="python_version",
        status="PASS" if ok else "FAIL",
        details={
            "current": f"{version[0]}.{version[1]}.{version[2]}",
            "required": "3.11",
        },
    )


def _load_active_epic() -> tuple[str, dict[str, Any], Path]:
    active_epic = ACTIVE_EPIC_FILE.read_text(encoding="utf-8").strip()
    if not active_epic:
        raise ValueError("active epic file is empty")
    epic_path = None
    epic_manifest: dict[str, Any] | None = None
    if not WORKSTREAMS_DIR.is_dir():
        raise FileNotFoundError(f"workstreams directory does not exist: {WORKSTREAMS_DIR}")
    for path in sorted(WORKSTREAMS_DIR.glob("*.yml")):
        manifest = _load_workstream_manifest(path)
        if manifest.get("id") == active_epic:
            epic_path = path
            epic_manifest = manifest
            break
    if epic_path is None or epic_manifest is None:
        raise FileNotFoundError(f"epic manifest does not exist: {active_epic}")
    if epic_manifest.get("status") != "active":
        raise ValueError(f"{epic_path.name}: epic status is {epic_manifest.get('status')!r}, expected 'active'")
    return active_epic, epic_manifest, epic_path


def _task_status_map() -> dict[str, bool]:
    task_states: dict[str, bool] = {}
    if not TASKS_FILE.is_file():
        return task_states
    for task_id, _, lines in _task_iter_blocks(TASKS_FILE):
        header = lines[0][1]
        if header.startswith("- [X]") or header.startswith("- [x]"):
            task_states[task_id] = True
        elif header.startswith("- [ ]"):
            task_states[task_id] = False
    return task_states


def _select_task(selector: str, manifest_tasks: list[str], task_states: dict[str, bool]) -> str:
    if selector != "next":
        if not TASK_ID_PATTERN.fullmatch(selector):
            raise SelectorUsageError("selector must be next or an uppercase task identifier")
        if selector not in manifest_tasks:
            raise TaskValidationError(f"task {selector} does not belong to the active epic")
        if task_states.get(selector) is True:
            raise TaskValidationError(f"task {selector} is already completed")
        return selector

    for task_id in manifest_tasks:
        if task_states.get(task_id) is True:
            continue
        if _task_metadata_has_findings(task_id):
            continue
        return task_id
    raise TaskValidationError("no ready task found for selector 'next'")


def _task_metadata_has_findings(task_id: str) -> bool:
    result = repository_checks.task_metadata([task_id])
    return bool(result)


def _classify_task_metadata_findings(findings: list[dict[str, Any]], keywords: tuple[str, ...]) -> bool:
    for finding in findings:
        reason = str(finding.get("reason") or "").lower()
        if any(keyword in reason for keyword in keywords):
            return True
    return False


def _repository_preflight(task_id: str) -> dict[str, Any]:
    return repository_checks.checks("preflight", [task_id])


def _task_metadata_findings(task_id: str) -> list[dict[str, Any]]:
    return repository_checks.task_metadata([task_id])


def _capture_git_snapshot() -> dict[str, list[str] | str]:
    return {
        "head_sha": _git_stdout(["git", "rev-parse", "HEAD"]),
        "branch": _git_stdout(["git", "branch", "--show-current"]),
        "tracked": _git_stdout(["git", "diff", "--name-only"]).splitlines(),
        "staged": _git_stdout(["git", "diff", "--cached", "--name-only"]).splitlines(),
        "untracked": _git_stdout(["git", "ls-files", "--others", "--exclude-standard"]).splitlines(),
    }


def _baseline_path(task_id: str) -> Path:
    return TASK_RUNS_DIR / task_id / "baseline.json"


def _write_baseline(task_id: str, epic_id: str, branch: str, head_sha: str, snapshot: dict[str, list[str] | str]) -> Path:
    path = _baseline_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "task": task_id,
        "epic": epic_id,
        "branch": branch,
        "head_sha": head_sha,
        "tracked": snapshot["tracked"],
        "staged": snapshot["staged"],
        "untracked": snapshot["untracked"],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _check_from_findings(name: str, findings: list[dict[str, Any]], keywords: tuple[str, ...]) -> CheckResult:
    matched = [finding for finding in findings if any(keyword in str(finding.get("reason") or "").lower() for keyword in keywords)]
    return CheckResult(name=name, status="FAIL" if matched else "PASS", details={"findings": matched})


def run_preflight(selector: str, *, version_info: tuple[int, int, int] | None = None) -> PreflightResult:
    checks: list[CheckResult] = []

    python_check = _python_version_check(version_info)
    checks.append(python_check)
    if python_check.status != "PASS":
        return PreflightResult(
            status="FAIL",
            exit_code=1,
            selector=selector,
            task_id="",
            epic_id="",
            branch="",
            head_sha=None,
            checks=tuple(checks),
            baseline_path=None,
        )

    active_epic, epic_manifest, _ = _load_active_epic()
    branch = str(epic_manifest.get("branch") or "")
    epic_check = CheckResult(
        name="active_epic",
        status="PASS",
        details={"epic": active_epic, "milestone": epic_manifest.get("milestone")},
    )
    checks.append(epic_check)

    current_branch = _git_stdout(["git", "branch", "--show-current"])
    branch_ok = current_branch == branch and current_branch not in {"master", "main"}
    branch_check = CheckResult(
        name="branch",
        status="PASS" if branch_ok else "FAIL",
        details={"current": current_branch, "expected": branch},
    )
    checks.append(branch_check)

    guard_errors = workstream_validation.validate_guard(selector)
    guard_timeout = any("timed out after" in error.lower() for error in guard_errors)
    guard_check = CheckResult(
        name="guard",
        status="FAIL" if guard_errors else "PASS",
        details={"errors": guard_errors},
    )
    checks.append(guard_check)
    if guard_timeout:
        return PreflightResult(
            status="TIMEOUT",
            exit_code=3,
            selector=selector,
            task_id="",
            epic_id=active_epic,
            branch=current_branch,
            head_sha=None,
            checks=tuple(checks),
            baseline_path=None,
        )

    task_states = _task_status_map()
    task_id = _select_task(selector, list(epic_manifest.get("tasks") or []), task_states)
    task_findings = _task_metadata_findings(task_id)
    dependency_check = _check_from_findings("dependency_validation", task_findings, DEPENDENCY_REASONS)
    ownership_check = _check_from_findings("task_ownership", task_findings, OWNERSHIP_REASONS)
    repo_preflight = _repository_preflight(task_id)
    repo_status = repo_preflight.get("status", "FAIL")
    repo_check = CheckResult(
        name="repository_preflight",
        status=str(repo_status),
        details=repo_preflight,
    )
    checks.extend([dependency_check, ownership_check, repo_check])

    if any(check.status == "TIMEOUT" for check in checks):
        return PreflightResult(
            status="TIMEOUT",
            exit_code=3,
            selector=selector,
            task_id=task_id,
            epic_id=active_epic,
            branch=current_branch,
            head_sha=None,
            checks=tuple(checks),
            baseline_path=None,
        )

    if any(check.status == "FAIL" for check in checks):
        return PreflightResult(
            status="FAIL",
            exit_code=1,
            selector=selector,
            task_id=task_id,
            epic_id=active_epic,
            branch=current_branch,
            head_sha=None,
            checks=tuple(checks),
            baseline_path=None,
        )

    snapshot = _capture_git_snapshot()
    baseline_path = _write_baseline(
        task_id=task_id,
        epic_id=active_epic,
        branch=current_branch,
        head_sha=str(snapshot["head_sha"]),
        snapshot=snapshot,
    )
    checks.append(
        CheckResult(
            name="baseline_capture",
            status="PASS",
            details={
                "baseline_path": str(baseline_path),
                "tracked": snapshot["tracked"],
                "staged": snapshot["staged"],
                "untracked": snapshot["untracked"],
            },
        )
    )

    return PreflightResult(
        status="PASS",
        exit_code=0,
        selector=selector,
        task_id=task_id,
        epic_id=active_epic,
        branch=current_branch,
        head_sha=str(snapshot["head_sha"]),
        checks=tuple(checks),
        baseline_path=str(baseline_path),
    )


def _payload(result: PreflightResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "selector": result.selector,
        "task": result.task_id,
        "epic": result.epic_id,
        "branch": result.branch,
        "head_sha": result.head_sha,
        "baseline_path": result.baseline_path,
        "checks": [
            {"name": check.name, "status": check.status, "details": check.details}
            for check in result.checks
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backend.app.tooling.agent_task_preflight")
    parser.add_argument("--selector", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code)

    try:
        result = run_preflight(args.selector)
    except SelectorUsageError as exc:
        payload = {"status": "FAIL", "reason": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"status: FAIL\nreason: {exc}")
        return 2
    except TaskValidationError as exc:
        payload = {"status": "FAIL", "reason": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"status: FAIL\nreason: {exc}")
        return 1
    except ValueError as exc:
        payload = {"status": "FAIL", "reason": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"status: FAIL\nreason: {exc}")
        return 1
    except subprocess.TimeoutExpired:
        payload = {"status": "TIMEOUT", "reason": f"command timed out after {TIMEOUT_SECONDS} seconds"}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("status: TIMEOUT")
            print(f"reason: command timed out after {TIMEOUT_SECONDS} seconds")
        return 3
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        payload = {"status": "FAIL", "reason": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"status: FAIL\nreason: {exc}")
        return 1

    if args.json:
        print(json.dumps(_payload(result), ensure_ascii=False, indent=2))
    else:
        print(f"status: {result.status}")
        print(f"task: {result.task_id}")
        print(f"epic: {result.epic_id}")
        print(f"branch: {result.branch}")
        print(f"head_sha: {result.head_sha}")
        print(f"baseline_path: {result.baseline_path}")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())

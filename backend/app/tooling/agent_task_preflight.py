"""Deterministic task preflight runner for agent task launches."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from . import repository_checks, workstream_validation
from .task_consistency import _iter_task_blocks as _task_iter_blocks

ROOT = Path(__file__).resolve().parents[3]
ACTIVE_EPIC_FILE = ROOT / ".specify" / "runtime" / "active-epic"
WORKSTREAMS_DIR = ROOT / ".specify" / "workstreams"
TASKS_FILE = ROOT / "specs" / "001-ai-content-studio" / "tasks.md"
TASK_RUNS_DIR = ROOT / ".specify" / "runtime" / "task-runs"
GLOBAL_TIMEOUT_SECONDS = 60
SNAPSHOT_TIMEOUT_SECONDS = 20
DIFF_CHECK_TIMEOUT_SECONDS = 20
TASK_ID_PATTERN = re.compile(r"^T\d{3}[A-Z]?$")
BASELINE_SCHEMA_VERSION = 1

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
    duration_ms: int
    checks: tuple[CheckResult, ...]
    baseline_path: str | None


class SelectorUsageError(ValueError):
    """Raised when the selector syntax is not supported."""


class TaskValidationError(ValueError):
    """Raised when the selected task is not eligible for preflight."""


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _git_snapshot_stage_check(snapshot: dict[str, Any]) -> CheckResult:
    status = str(snapshot.get("status") or "FAIL")
    return CheckResult(
        name="git_snapshot",
        status=status,
        details={
            "branch": snapshot.get("branch", ""),
            "head_sha": snapshot.get("head_sha"),
            "tracked": snapshot.get("tracked", []),
            "staged": snapshot.get("staged", []),
            "untracked": snapshot.get("untracked", []),
            "deleted": snapshot.get("deleted", []),
            "renamed": snapshot.get("renamed", []),
            "duration_ms": snapshot.get("duration_ms", 0),
            "reason": snapshot.get("reason", ""),
        },
    )


def _python_version_check(version_info: tuple[int, int, int] | None = None) -> CheckResult:
    version = version_info or sys.version_info[:3]
    ok = (version[0], version[1]) >= (3, 11)
    return CheckResult(
        name="python_version",
        status="PASS" if ok else "FAIL",
        details={
            "current": f"{version[0]}.{version[1]}.{version[2]}",
            "required": "3.11",
            "duration_ms": 0,
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
        manifest = workstream_validation._load_yaml_manifest(path)
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


def _group_findings_by_task(findings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        task_id = str(finding.get("task") or "").strip()
        if not task_id:
            continue
        grouped.setdefault(task_id, []).append(finding)
    return grouped


def _select_task(
    selector: str,
    manifest_tasks: list[str],
    task_states: dict[str, bool],
    findings_by_task: dict[str, list[dict[str, Any]]],
) -> str:
    if selector != "next":
        if not TASK_ID_PATTERN.fullmatch(selector):
            raise SelectorUsageError("selector must be next or an uppercase task identifier")
        if selector not in manifest_tasks:
            raise TaskValidationError(f"task {selector} does not belong to the active epic")
        if task_states.get(selector) is True:
            raise TaskValidationError(f"task {selector} is already completed")
        if findings_by_task.get(selector):
            raise TaskValidationError(f"task {selector} has outstanding metadata findings")
        return selector

    for task_id in manifest_tasks:
        if task_states.get(task_id) is True:
            continue
        if findings_by_task.get(task_id):
            continue
        return task_id
    raise TaskValidationError("no ready task found for selector 'next'")


def _classify_task_findings(findings: list[dict[str, Any]], keywords: tuple[str, ...]) -> bool:
    for finding in findings:
        reason = str(finding.get("reason") or "").lower()
        if any(keyword in reason for keyword in keywords):
            return True
    return False


def _choose_status(errors: list[str]) -> tuple[str, int]:
    if any("timed out after" in error.lower() for error in errors):
        return "TIMEOUT", 3
    if errors:
        return "FAIL", 1
    return "PASS", 0


def _stage_check(name: str, started: float, status: str, **details: Any) -> CheckResult:
    details = dict(details)
    details.setdefault("duration_ms", _elapsed_ms(started))
    return CheckResult(name=name, status=status, details=details)


def _baseline_path(task_id: str) -> Path:
    return TASK_RUNS_DIR / task_id / "baseline.json"


def _write_baseline(
    task_id: str,
    epic_id: str,
    branch: str,
    head_sha: str,
    snapshot: dict[str, Any],
) -> Path:
    path = _baseline_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "task": task_id,
        "epic": epic_id,
        "branch": branch,
        "head_sha": head_sha,
        "tracked": snapshot["tracked"],
        "staged": snapshot["staged"],
        "untracked": snapshot["untracked"],
        "deleted": snapshot["deleted"],
        "renamed": snapshot["renamed"],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _payload(result: PreflightResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "selector": result.selector,
        "task": result.task_id,
        "epic": result.epic_id,
        "branch": result.branch,
        "head_sha": result.head_sha,
        "duration_ms": result.duration_ms,
        "baseline_path": result.baseline_path,
        "checks": [
            {"name": check.name, "status": check.status, "details": check.details}
            for check in result.checks
        ],
    }


def run_preflight(selector: str, *, version_info: tuple[int, int, int] | None = None) -> PreflightResult:
    started_perf = time.perf_counter()
    deadline = time.monotonic() + GLOBAL_TIMEOUT_SECONDS
    checks: list[CheckResult] = []

    python_started = time.perf_counter()
    python_check = _python_version_check(version_info)
    checks.append(
        CheckResult(
            name=python_check.name,
            status=python_check.status,
            details={**python_check.details, "duration_ms": _elapsed_ms(python_started)},
        )
    )
    if python_check.status != "PASS":
        return PreflightResult(
            status="FAIL",
            exit_code=1,
            selector=selector,
            task_id="",
            epic_id="",
            branch="",
            head_sha=None,
            duration_ms=_elapsed_ms(started_perf),
            checks=tuple(checks),
            baseline_path=None,
        )

    active_started = time.perf_counter()
    active_epic, epic_manifest, _ = _load_active_epic()
    active_check = _stage_check(
        "active_epic",
        active_started,
        "PASS",
        epic=active_epic,
        milestone=epic_manifest.get("milestone"),
        branch=epic_manifest.get("branch"),
        status_text=epic_manifest.get("status"),
    )
    checks.append(active_check)

    manifest_started = time.perf_counter()
    manifest_errors = workstream_validation.validate_manifests(WORKSTREAMS_DIR)
    manifest_status, _ = _choose_status(manifest_errors)
    manifest_check = _stage_check(
        "manifest_validation",
        manifest_started,
        manifest_status,
        errors=manifest_errors,
    )
    checks.append(manifest_check)
    if manifest_errors:
        return PreflightResult(
            status=manifest_status,
            exit_code=1 if manifest_status == "FAIL" else 3,
            selector=selector,
            task_id="",
            epic_id=active_epic,
            branch=str(epic_manifest.get("branch") or ""),
            head_sha=None,
            duration_ms=_elapsed_ms(started_perf),
            checks=tuple(checks),
            baseline_path=None,
        )

    consistency_started = time.perf_counter()
    consistency_errors = workstream_validation.validate_task_epic_consistency(TASKS_FILE, WORKSTREAMS_DIR)
    consistency_status, _ = _choose_status(consistency_errors)
    consistency_check = _stage_check(
        "task_epic_consistency",
        consistency_started,
        consistency_status,
        errors=consistency_errors,
    )
    checks.append(consistency_check)
    if consistency_errors:
        return PreflightResult(
            status=consistency_status,
            exit_code=1 if consistency_status == "FAIL" else 3,
            selector=selector,
            task_id="",
            epic_id=active_epic,
            branch=str(epic_manifest.get("branch") or ""),
            head_sha=None,
            duration_ms=_elapsed_ms(started_perf),
            checks=tuple(checks),
            baseline_path=None,
        )

    guard_started = time.perf_counter()
    guard_errors = workstream_validation.validate_active_epic(
        task_selector=selector,
        runtime_file=ACTIVE_EPIC_FILE,
        directory=WORKSTREAMS_DIR,
        tasks_file=TASKS_FILE,
    )
    guard_status, guard_exit_code = _choose_status(guard_errors)
    guard_check = _stage_check(
        "active_epic_guard",
        guard_started,
        guard_status,
        errors=guard_errors,
    )
    checks.append(guard_check)
    if guard_exit_code != 0:
        return PreflightResult(
            status=guard_status,
            exit_code=guard_exit_code,
            selector=selector,
            task_id="",
            epic_id=active_epic,
            branch=str(epic_manifest.get("branch") or ""),
            head_sha=None,
            duration_ms=_elapsed_ms(started_perf),
            checks=tuple(checks),
            baseline_path=None,
        )

    task_states = _task_status_map()
    manifest_tasks = [str(task) for task in (epic_manifest.get("tasks") or []) if isinstance(task, str)]
    task_findings = repository_checks.task_metadata(manifest_tasks)
    findings_by_task = _group_findings_by_task(task_findings)
    task_started = time.perf_counter()
    task_id = _select_task(selector, manifest_tasks, task_states, findings_by_task)
    selected_findings = findings_by_task.get(task_id, [])
    dependency_findings = [finding for finding in selected_findings if any(keyword in str(finding.get("reason") or "").lower() for keyword in DEPENDENCY_REASONS)]
    ownership_findings = [finding for finding in selected_findings if any(keyword in str(finding.get("reason") or "").lower() for keyword in OWNERSHIP_REASONS)]
    task_status = "PASS" if not selected_findings else "FAIL"
    task_check = _stage_check(
        "selected_task_metadata",
        task_started,
        task_status,
        task=task_id,
        findings=selected_findings,
        dependency_findings=dependency_findings,
        ownership_findings=ownership_findings,
    )
    checks.append(task_check)
    if selected_findings:
        return PreflightResult(
            status="FAIL",
            exit_code=1,
            selector=selector,
            task_id=task_id,
            epic_id=active_epic,
            branch=str(epic_manifest.get("branch") or ""),
            head_sha=None,
            duration_ms=_elapsed_ms(started_perf),
            checks=tuple(checks),
            baseline_path=None,
        )

    snapshot_started = time.perf_counter()
    snapshot = repository_checks.capture_git_snapshot(
        timeout_seconds=SNAPSHOT_TIMEOUT_SECONDS,
        total_deadline=deadline,
    )
    snapshot_check = _git_snapshot_stage_check(snapshot)
    snapshot_check = _stage_check(
        snapshot_check.name,
        snapshot_started,
        snapshot_check.status,
        **snapshot_check.details,
    )
    checks.append(snapshot_check)
    if snapshot["status"] != "PASS":
        status, exit_code = _choose_status([str(snapshot.get("reason") or "git snapshot failed")])
        return PreflightResult(
            status=status,
            exit_code=exit_code,
            selector=selector,
            task_id=task_id,
            epic_id=active_epic,
            branch=str(epic_manifest.get("branch") or ""),
            head_sha=snapshot.get("head_sha") or None,
            duration_ms=_elapsed_ms(started_perf),
            checks=tuple(checks),
            baseline_path=None,
        )

    diff_started = time.perf_counter()
    diff_result = repository_checks.process_runner.run_process(
        ["git", "--no-pager", "diff", "--check"],
        cwd=ROOT,
        timeout_seconds=DIFF_CHECK_TIMEOUT_SECONDS,
        total_deadline=deadline,
        heartbeat_seconds=0,
    )
    diff_status = str(diff_result.status)
    diff_check = _stage_check(
        "git_diff_check",
        diff_started,
        diff_status,
        timeout_seconds=DIFF_CHECK_TIMEOUT_SECONDS,
        exit_code=diff_result.exit_code,
        timed_out=diff_result.timed_out,
        process_tree_killed=diff_result.process_tree_killed,
        output_truncated=diff_result.output_truncated,
        stdout_lines=list(diff_result.stdout_lines),
        stderr_lines=list(diff_result.stderr_lines),
        pid=diff_result.pid,
    )
    checks.append(diff_check)
    if diff_status != "PASS":
        return PreflightResult(
            status=diff_status,
            exit_code=3 if diff_status == "TIMEOUT" else 1,
            selector=selector,
            task_id=task_id,
            epic_id=active_epic,
            branch=str(epic_manifest.get("branch") or ""),
            head_sha=snapshot.get("head_sha") or None,
            duration_ms=_elapsed_ms(started_perf),
            checks=tuple(checks),
            baseline_path=None,
        )

    if time.monotonic() >= deadline:
        return PreflightResult(
            status="TIMEOUT",
            exit_code=3,
            selector=selector,
            task_id=task_id,
            epic_id=active_epic,
            branch=str(epic_manifest.get("branch") or ""),
            head_sha=snapshot.get("head_sha") or None,
            duration_ms=_elapsed_ms(started_perf),
            checks=tuple(checks),
            baseline_path=None,
        )

    baseline_path = _write_baseline(
        task_id=task_id,
        epic_id=active_epic,
        branch=str(snapshot["branch"] or epic_manifest.get("branch") or ""),
        head_sha=str(snapshot["head_sha"]),
        snapshot=snapshot,
    )
    baseline_check = _stage_check(
        "baseline_capture",
        time.perf_counter(),
        "PASS",
        baseline_path=str(baseline_path),
        schema_version=BASELINE_SCHEMA_VERSION,
        tracked=snapshot["tracked"],
        staged=snapshot["staged"],
        untracked=snapshot["untracked"],
        deleted=snapshot["deleted"],
        renamed=snapshot["renamed"],
    )
    checks.append(baseline_check)

    return PreflightResult(
        status="PASS",
        exit_code=0,
        selector=selector,
        task_id=task_id,
        epic_id=active_epic,
        branch=str(snapshot["branch"]),
        head_sha=str(snapshot["head_sha"]),
        duration_ms=_elapsed_ms(started_perf),
        checks=tuple(checks),
        baseline_path=str(baseline_path),
    )


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
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
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
        print(f"duration_ms: {result.duration_ms}")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())

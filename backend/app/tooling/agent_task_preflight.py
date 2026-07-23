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
DEPENDENCY_TASK_PATTERN = re.compile(r"T\d{3}[A-Z]?")
BASELINE_SCHEMA_VERSION = 1
NO_DEPENDENCY_VALUES = {"none", "n/a", "[]"}

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
    reason: str | None = None
    declared_dependencies: tuple[str, ...] = ()
    completed_dependencies: tuple[str, ...] = ()
    incomplete_dependencies: tuple[str, ...] = ()
    unknown_dependencies: tuple[str, ...] = ()
    feature_dir: str | None = None
    spec_path: str | None = None
    plan_path: str | None = None
    tasks_path: str | None = None
    data_model_path: str | None = None
    research_path: str | None = None
    quickstart_path: str | None = None
    contracts_dir: str | None = None
    available_docs: tuple[str, ...] = ()


class SelectorUsageError(ValueError):
    """Raised when the selector syntax is not supported."""


class TaskValidationError(ValueError):
    """Raised when the selected task is not eligible for preflight."""


class DependencyReadinessError(ValueError):
    """Raised when explicit task selection fails dependency readiness."""

    def __init__(self, readiness: dict[str, Any]) -> None:
        super().__init__("task dependencies are incomplete")
        self.readiness = readiness


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


def _empty_feature_payload() -> dict[str, Any]:
    return {
        "feature_dir": None,
        "spec_path": None,
        "plan_path": None,
        "tasks_path": None,
        "data_model_path": None,
        "research_path": None,
        "quickstart_path": None,
        "contracts_dir": None,
        "available_docs": (),
    }


def _resolve_repo_path(path_value: str) -> Path:
    candidate = (ROOT / path_value).resolve()
    root = ROOT.resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"feature path escapes repository root: {path_value}")
    return candidate


def _resolve_feature_context(epic_manifest: dict[str, Any]) -> dict[str, Any]:
    feature_value = epic_manifest.get("feature")
    if not isinstance(feature_value, str) or not feature_value.strip():
        raise ValueError("active epic manifest does not declare a feature directory")

    feature_dir = _resolve_repo_path(feature_value.strip())
    if not feature_dir.is_dir():
        raise FileNotFoundError(f"feature directory does not exist: {feature_dir}")

    spec_path = feature_dir / "spec.md"
    plan_path = feature_dir / "plan.md"
    tasks_path = feature_dir / "tasks.md"
    required_paths = (spec_path, plan_path, tasks_path)
    missing = [str(path) for path in required_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("feature directory is missing required files: " + ", ".join(missing))

    data_model_path = feature_dir / "data-model.md"
    if not data_model_path.is_file():
        data_model_path = None
    research_path = feature_dir / "research.md"
    if not research_path.is_file():
        research_path = None
    quickstart_path = feature_dir / "quickstart.md"
    if not quickstart_path.is_file():
        quickstart_path = None
    contracts_dir = feature_dir / "contracts"
    if not contracts_dir.is_dir():
        contracts_dir = None

    available_docs = tuple(
        str(path)
        for path in (data_model_path, research_path, quickstart_path, contracts_dir)
        if path is not None
    )

    return {
        "feature_dir": str(feature_dir),
        "spec_path": str(spec_path),
        "plan_path": str(plan_path),
        "tasks_path": str(tasks_path),
        "data_model_path": str(data_model_path) if data_model_path is not None else None,
        "research_path": str(research_path) if research_path is not None else None,
        "quickstart_path": str(quickstart_path) if quickstart_path is not None else None,
        "contracts_dir": str(contracts_dir) if contracts_dir is not None else None,
        "available_docs": available_docs,
    }


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


def _task_dependency_map() -> dict[str, tuple[str, ...]]:
    dependencies: dict[str, tuple[str, ...]] = {}
    if not TASKS_FILE.is_file():
        return dependencies
    for task_id, _, lines in _task_iter_blocks(TASKS_FILE):
        dependency_text = ""
        for _, line in lines:
            if line.startswith("Dependencies:"):
                dependency_text = line[len("Dependencies:") :].strip()
                break
        if not dependency_text or dependency_text.lower() in NO_DEPENDENCY_VALUES:
            dependencies[task_id] = ()
            continue
        dependency_ids: list[str] = []
        for dependency_id in DEPENDENCY_TASK_PATTERN.findall(dependency_text):
            if dependency_id not in dependency_ids:
                dependency_ids.append(dependency_id)
        dependencies[task_id] = tuple(dependency_ids)
    return dependencies


def _group_findings_by_task(findings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        task_id = str(finding.get("task") or "").strip()
        if not task_id:
            continue
        grouped.setdefault(task_id, []).append(finding)
    return grouped


def _dependency_readiness(
    task_id: str,
    manifest_tasks: list[str],
    task_states: dict[str, bool],
    dependency_map: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    declared = list(dependency_map.get(task_id, ()))
    completed = [dependency for dependency in declared if task_states.get(dependency) is True]
    incomplete = [dependency for dependency in declared if dependency in task_states and task_states.get(dependency) is False]
    unknown = [dependency for dependency in declared if dependency not in task_states and dependency not in manifest_tasks]
    return {
        "task": task_id,
        "declared_dependencies": declared,
        "completed_dependencies": completed,
        "incomplete_dependencies": incomplete,
        "unknown_dependencies": unknown,
    }


def _dependency_failure_reason(readiness: dict[str, Any]) -> str:
    if readiness["unknown_dependencies"]:
        return "unknown dependency task"
    if readiness["incomplete_dependencies"]:
        return "task dependencies are incomplete"
    return "task dependencies are incomplete"


def _is_ready_task(
    task_id: str,
    manifest_tasks: list[str],
    task_states: dict[str, bool],
    findings_by_task: dict[str, list[dict[str, Any]]],
    dependency_map: dict[str, tuple[str, ...]],
) -> bool:
    if task_states.get(task_id) is True:
        return False
    if task_id not in manifest_tasks:
        return False
    if findings_by_task.get(task_id):
        return False
    readiness = _dependency_readiness(task_id, manifest_tasks, task_states, dependency_map)
    return not readiness["incomplete_dependencies"] and not readiness["unknown_dependencies"]


def _select_task(
    selector: str,
    manifest_tasks: list[str],
    task_states: dict[str, bool],
    findings_by_task: dict[str, list[dict[str, Any]]],
    dependency_map: dict[str, tuple[str, ...]],
) -> str:
    if selector != "next":
        if not TASK_ID_PATTERN.fullmatch(selector):
            raise SelectorUsageError("selector must be next or an uppercase task identifier")
        if selector not in manifest_tasks:
            raise TaskValidationError(f"task {selector} does not belong to the active epic")
        if task_states.get(selector) is True:
            raise TaskValidationError(f"task {selector} is already completed")
        readiness = _dependency_readiness(selector, manifest_tasks, task_states, dependency_map)
        if readiness["unknown_dependencies"] or readiness["incomplete_dependencies"]:
            raise DependencyReadinessError(readiness)
        if findings_by_task.get(selector):
            raise TaskValidationError(f"task {selector} has outstanding metadata findings")
        return selector

    for task_id in manifest_tasks:
        if _is_ready_task(task_id, manifest_tasks, task_states, findings_by_task, dependency_map):
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
        "reason": result.reason,
        "declared_dependencies": list(result.declared_dependencies),
        "completed_dependencies": list(result.completed_dependencies),
        "incomplete_dependencies": list(result.incomplete_dependencies),
        "unknown_dependencies": list(result.unknown_dependencies),
        "branch": result.branch,
        "head_sha": result.head_sha,
        "duration_ms": result.duration_ms,
        "baseline_path": result.baseline_path,
        "feature_dir": result.feature_dir,
        "spec_path": result.spec_path,
        "plan_path": result.plan_path,
        "tasks_path": result.tasks_path,
        "data_model_path": result.data_model_path,
        "research_path": result.research_path,
        "quickstart_path": result.quickstart_path,
        "contracts_dir": result.contracts_dir,
        "available_docs": list(result.available_docs),
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
            **_empty_feature_payload(),
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

    feature_started = time.perf_counter()
    feature_payload = _empty_feature_payload()
    try:
        feature_payload = _resolve_feature_context(epic_manifest)
    except (FileNotFoundError, ValueError) as exc:
        feature_check = _stage_check(
            "feature_context",
            feature_started,
            "FAIL",
            feature=epic_manifest.get("feature"),
            error=str(exc),
        )
        checks.append(feature_check)
        return PreflightResult(
            status="FAIL",
            exit_code=1,
            selector=selector,
            task_id="",
            epic_id=active_epic,
            branch=str(epic_manifest.get("branch") or ""),
            head_sha=None,
            duration_ms=_elapsed_ms(started_perf),
            checks=tuple(checks),
            baseline_path=None,
            **feature_payload,
        )

    feature_check = _stage_check(
        "feature_context",
        feature_started,
        "PASS",
        **feature_payload,
    )
    checks.append(feature_check)

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
            **feature_payload,
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
            **feature_payload,
        )

    guard_started = time.perf_counter()
    guard_errors = workstream_validation.validate_active_epic(
        task_selector=selector,
        runtime_file=ACTIVE_EPIC_FILE,
        directory=WORKSTREAMS_DIR,
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
            **feature_payload,
        )

    task_states = _task_status_map()
    dependency_map = _task_dependency_map()
    manifest_tasks = [str(task) for task in (epic_manifest.get("tasks") or []) if isinstance(task, str)]
    task_findings = repository_checks.task_metadata(manifest_tasks)
    findings_by_task = _group_findings_by_task(task_findings)
    task_started = time.perf_counter()
    try:
        task_id = _select_task(selector, manifest_tasks, task_states, findings_by_task, dependency_map)
    except DependencyReadinessError as exc:
        readiness = exc.readiness
        task_check = _stage_check(
            "dependency_readiness",
            task_started,
            "FAIL",
            **readiness,
        )
        checks.append(task_check)
        return PreflightResult(
            status="FAIL",
            exit_code=1,
            selector=selector,
            task_id=str(selector),
            epic_id=active_epic,
            branch=str(epic_manifest.get("branch") or ""),
            head_sha=None,
            duration_ms=_elapsed_ms(started_perf),
            checks=tuple(checks),
            baseline_path=None,
            reason=_dependency_failure_reason(readiness),
            declared_dependencies=tuple(readiness["declared_dependencies"]),
            completed_dependencies=tuple(readiness["completed_dependencies"]),
            incomplete_dependencies=tuple(readiness["incomplete_dependencies"]),
            unknown_dependencies=tuple(readiness["unknown_dependencies"]),
            **feature_payload,
        )
    dependency_readiness = _dependency_readiness(task_id, manifest_tasks, task_states, dependency_map)
    dependency_check = _stage_check(
        "dependency_readiness",
        task_started,
        "PASS",
        **dependency_readiness,
    )
    checks.append(dependency_check)
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
            **feature_payload,
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
            **feature_payload,
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
            **feature_payload,
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
            **feature_payload,
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
        **feature_payload,
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

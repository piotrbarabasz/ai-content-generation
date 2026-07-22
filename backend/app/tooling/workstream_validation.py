"""Validate static milestone and epic manifests without Git or network access."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DIRECTORY = ROOT / ".specify" / "workstreams"
ACTIVE_EPIC_FILE = ROOT / ".specify" / "runtime" / "active-epic"
TASKS_FILE = ROOT / "specs" / "001-ai-content-studio" / "tasks.md"
TIMEOUT_SECONDS = 20
STATUSES = {"planned", "active", "review", "completed", "blocked"}
RISKS = {"low", "medium", "high", "critical"}
TASK_ID_PATTERN = re.compile(r"^T\d{3}[A-Z]?$")
TASK_HEADER_PATTERN = re.compile(r"^- \[(?P<checkbox>[Xx ])\] (?P<task>T\d{3}[A-Z]?)(?=\s|$)")
MILESTONE_PATTERN = re.compile(r"^M\d{3}$")
EPIC_PATTERN = re.compile(r"^E\d{3}$")
PR_POLICY_FIELDS = ("one_pr_per_epic", "merge_requires_human", "auto_merge")
COMMIT_POLICY_FIELDS = ("one_commit_per_task", "commit_requires_human", "auto_commit")


def _load_yaml_manifest(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name}: manifest must be a mapping")
    return loaded


def _policy_error(path: Path, field_name: str, policy: Any, required_fields: tuple[str, ...], bool_requirements: dict[str, bool]) -> list[str]:
    errors: list[str] = []
    if not isinstance(policy, dict):
        errors.append(f"{path.name}: {field_name} must be a mapping")
        return errors
    for field in required_fields:
        if field not in policy:
            errors.append(f"{path.name}: {field_name} missing required field {field}")
    for field, expected in bool_requirements.items():
        if field not in policy:
            continue
        value = policy[field]
        if not isinstance(value, bool):
            errors.append(f"{path.name}: {field_name}.{field} must be boolean")
        elif value is not expected:
            errors.append(f"{path.name}: {field_name}.{field} must be {str(expected).lower()}")
    return errors


def _load_workstream_epics(directory: Path) -> dict[str, dict[str, Any]]:
    epics: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return epics
    for path in sorted(directory.glob("*.yml")):
        try:
            manifest = _load_yaml_manifest(path)
        except (OSError, ValueError):
            continue
        identifier = manifest.get("id")
        if isinstance(identifier, str) and EPIC_PATTERN.fullmatch(identifier):
            epics[identifier] = manifest
    return epics


def _parse_task_dependencies(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    normalized = value.strip().strip("`")
    if not normalized or normalized.lower() in {"none", "n/a", "na", "[]"}:
        return []
    return [item.strip().strip("`") for item in normalized.split(",") if item.strip()]


def _iter_task_blocks(path: Path) -> list[tuple[str, int, list[tuple[int, str]]]]:
    blocks: list[tuple[str, int, list[tuple[int, str]]]] = []
    current_lines: list[tuple[int, str]] = []
    current_task = ""
    current_start = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.rstrip("\n")
            match = TASK_HEADER_PATTERN.match(line)
            if match:
                if current_lines:
                    blocks.append((current_task, current_start, current_lines))
                current_task = match.group("task")
                current_start = line_number
                current_lines = [(line_number, line)]
            elif current_lines:
                current_lines.append((line_number, line))
    if current_lines:
        blocks.append((current_task, current_start, current_lines))
    return blocks


def _field_value(lines: list[tuple[int, str]], field_name: str) -> tuple[int, str] | None:
    for line_number, line in lines:
        if line.startswith(field_name):
            return line_number, line[len(field_name) :].strip()
    return None


def _epic_reaches(epics: dict[str, dict[str, Any]], source: str, target: str) -> bool:
    if source == target:
        return True
    visiting = [source]
    seen: set[str] = set()
    while visiting:
        current = visiting.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        visiting.extend([dependency for dependency in epics.get(current, {}).get("depends_on") or [] if isinstance(dependency, str)])
    return False


def validate_manifests(directory: Path | str = DEFAULT_DIRECTORY) -> list[str]:
    """Return all deterministic validation errors found in *directory*."""
    directory = Path(directory)
    errors: list[str] = []
    if not directory.is_dir():
        return [f"workstreams directory does not exist: {directory}"]
    manifests: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.yml")):
        try:
            manifests.append((path, _load_yaml_manifest(path)))
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    by_id: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path, manifest in manifests:
        identifier = manifest.get("id")
        if not isinstance(identifier, str):
            errors.append(f"{path.name}: missing or invalid id")
        elif identifier in by_id:
            errors.append(f"duplicate manifest id: {identifier}")
        else:
            by_id[identifier] = (path, manifest)
    milestones: dict[str, dict[str, Any]] = {}
    epics: dict[str, dict[str, Any]] = {}
    for path, manifest in manifests:
        identifier = manifest.get("id")
        is_milestone = isinstance(identifier, str) and bool(MILESTONE_PATTERN.fullmatch(identifier))
        is_epic = isinstance(identifier, str) and bool(EPIC_PATTERN.fullmatch(identifier))
        if not (is_milestone or is_epic):
            errors.append(f"{path.name}: id must match M### or E###")
            continue
        required = (
            {"id", "title", "status", "goal", "epics", "completion_criteria"}
            if is_milestone
            else {"id", "title", "milestone", "feature", "base_branch", "branch", "status", "risk", "depends_on", "tasks", "required_checks", "pr_policy", "commit_policy"}
        )
        for field in sorted(required - manifest.keys()):
            errors.append(f"{path.name}: missing required field {field}")
        if manifest.get("status") not in STATUSES:
            errors.append(f"{path.name}: invalid status {manifest.get('status')!r}")
        if is_milestone:
            milestones[identifier] = manifest
        else:
            if manifest.get("risk") not in RISKS:
                errors.append(f"{path.name}: invalid risk {manifest.get('risk')!r}")
            if manifest.get("branch") == manifest.get("base_branch"):
                errors.append(f"{path.name}: branch must differ from base_branch")
            errors.extend(
                _policy_error(
                    path,
                    "pr_policy",
                    manifest.get("pr_policy"),
                    PR_POLICY_FIELDS,
                    {"one_pr_per_epic": True, "merge_requires_human": True, "auto_merge": False},
                )
            )
            errors.extend(
                _policy_error(
                    path,
                    "commit_policy",
                    manifest.get("commit_policy"),
                    COMMIT_POLICY_FIELDS,
                    {"one_commit_per_task": True, "commit_requires_human": True, "auto_commit": False},
                )
            )
            epics[identifier] = manifest
    for epic_id, manifest in sorted(epics.items()):
        path = by_id[epic_id][0]
        if manifest.get("milestone") not in milestones:
            errors.append(f"{path.name}: unknown milestone {manifest.get('milestone')!r}")
        for dependency in manifest.get("depends_on") or []:
            if dependency not in epics:
                errors.append(f"{path.name}: unknown epic dependency {dependency!r}")
        tasks = manifest.get("tasks") or []
        if manifest.get("status") == "active" and not tasks:
            errors.append(f"{path.name}: active epic must have a non-empty tasks list")
        for task in tasks:
            if not isinstance(task, str) or not TASK_ID_PATTERN.fullmatch(task):
                errors.append(f"{path.name}: invalid task id {task!r}")
    owners: dict[str, str] = {}
    for epic_id, manifest in sorted(epics.items()):
        for task in manifest.get("tasks") or []:
            if task in owners and owners[task] != epic_id:
                errors.append(f"task {task} belongs to multiple epics: {owners[task]}, {epic_id}")
            owners[task] = epic_id
    graph = {key: [dep for dep in value.get("depends_on", []) if dep in epics] for key, value in epics.items()}
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(node: str, trail: list[str]) -> None:
        if node in visiting:
            errors.append(f"epic dependency cycle: {' -> '.join(trail + [node])}")
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency, trail + [node])
        visiting.remove(node)
        visited.add(node)
    for epic_id in sorted(graph):
        visit(epic_id, [])
    for milestone_id, manifest in sorted(milestones.items()):
        path = by_id[milestone_id][0]
        for epic_id in manifest.get("epics") or []:
            if epic_id not in epics:
                errors.append(f"{path.name}: unknown epic {epic_id!r}")
            elif epics[epic_id].get("milestone") != milestone_id:
                errors.append(
                    f"{path.name}: epic {epic_id} points to milestone "
                    f"{epics[epic_id].get('milestone')!r}, expected {milestone_id}"
                )
    for epic_id, manifest in sorted(epics.items()):
        milestone_id = manifest.get("milestone")
        if milestone_id in milestones and epic_id not in (milestones[milestone_id].get("epics") or []):
            errors.append(
                f"{by_id[epic_id][0].name}: epic {epic_id} is not listed by "
                f"milestone {milestone_id}"
            )
    return sorted(set(errors))


def validate_task_epic_consistency(
    tasks_file: Path | str = TASKS_FILE,
    directory: Path | str = DEFAULT_DIRECTORY,
) -> list[str]:
    """Validate task-to-epic dependencies and task graph consistency."""
    tasks_file = Path(tasks_file)
    directory = Path(directory)
    errors: list[str] = []
    if not tasks_file.is_file():
        return [f"tasks file does not exist: {tasks_file}"]
    epics = _load_workstream_epics(directory)

    task_blocks = _iter_task_blocks(tasks_file)
    tasks_by_id: dict[str, dict[str, Any]] = {}
    for task_id, start_line, lines in task_blocks:
        epic = _field_value(lines, "Epic:")
        dependencies = _field_value(lines, "Dependencies:")
        if epic is None:
            errors.append(f"{tasks_file.name}:{start_line}: task {task_id} does not declare an epic")
        elif not re.fullmatch(r"E\d{3}", epic[1]):
            errors.append(f"{tasks_file.name}:{epic[0]}: task {task_id} has invalid epic {epic[1]!r}")
        tasks_by_id[task_id] = {
            "start_line": start_line,
            "epic": epic[1] if epic else "",
            "epic_line": epic[0] if epic else start_line,
            "dependencies": _parse_task_dependencies(dependencies[1]) if dependencies else [],
            "dependencies_line": dependencies[0] if dependencies else start_line,
        }

    graph: dict[str, list[str]] = {
        task_id: [dependency for dependency in record["dependencies"] if dependency in tasks_by_id]
        for task_id, record in tasks_by_id.items()
    }

    for task_id, record in tasks_by_id.items():
        source_epic = record["epic"]
        if not source_epic:
            continue
        for dependency in record["dependencies"]:
            dependency_record = tasks_by_id.get(dependency)
            if dependency_record is None:
                errors.append(
                    f"{tasks_file.name}:{record['dependencies_line']}: task {task_id} depends on unknown task {dependency}"
                )
                continue
            dependency_epic = dependency_record["epic"]
            if not dependency_epic:
                errors.append(
                    f"{tasks_file.name}:{dependency_record['start_line']}: task {dependency} does not declare an epic"
                )
                continue
            if dependency_epic == source_epic:
                continue
            if not _epic_reaches(epics, source_epic, dependency_epic):
                errors.append(
                    f"{tasks_file.name}:{record['dependencies_line']}: task {task_id} in epic {source_epic} depends on "
                    f"task {dependency} in epic {dependency_epic}, but epic {source_epic} does not depend on epic {dependency_epic}"
                )

    visiting: list[str] = []
    visiting_set: set[str] = set()
    visited: set[str] = set()
    cycle_errors: list[str] = []

    def visit(node: str) -> None:
        if node in visiting_set:
            cycle_start = visiting.index(node)
            cycle = visiting[cycle_start:] + [node]
            cycle_errors.append(f"{tasks_file.name}:{tasks_by_id[node]['start_line']}: task dependency cycle: {' -> '.join(cycle)}")
            return
        if node in visited:
            return
        visited.add(node)
        visiting.append(node)
        visiting_set.add(node)
        for dependency in graph.get(node, []):
            visit(dependency)
        visiting.pop()
        visiting_set.remove(node)

    for task_id in sorted(graph):
        visit(task_id)
    errors.extend(cycle_errors)
    return errors


def validate_active_epic(
    task_selector: str = "next",
    runtime_file: Path | str = ACTIVE_EPIC_FILE,
    directory: Path | str = DEFAULT_DIRECTORY,
) -> list[str]:
    """Validate the local active-epic and branch context for one loop run."""
    runtime_file = Path(runtime_file)
    directory = Path(directory)
    active_epic = runtime_file.read_text(encoding="utf-8").strip() if runtime_file.is_file() else "<missing>"
    expected_branch = "<unknown>"
    current_branch = "<unknown>"
    errors: list[str] = []
    if runtime_file.is_file() and not active_epic:
        active_epic = "<empty>"
    manifests = {}
    for path in sorted(directory.glob("*.yml")) if directory.is_dir() else []:
        try:
            manifest = _load_yaml_manifest(path)
        except (OSError, ValueError):
            continue
        if isinstance(manifest.get("id"), str):
            manifests[manifest["id"]] = manifest
    manifest = manifests.get(active_epic)
    if manifest:
        expected_branch = str(manifest.get("branch", "<missing>"))
    branch_available = True
    try:
        current_branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=ROOT,
            timeout=TIMEOUT_SECONDS,
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip() or "<detached>"
    except (OSError, subprocess.CalledProcessError) as exc:
        branch_available = False
        errors.append(f"cannot read current Git branch: {exc}")
    except subprocess.TimeoutExpired:
        branch_available = False
        errors.append(f"cannot read current Git branch: command timed out after {TIMEOUT_SECONDS} seconds")
    reason: list[str] = []
    if task_selector != "next" and not TASK_ID_PATTERN.fullmatch(task_selector):
        reason.append(f"invalid task selector {task_selector!r}")
    if active_epic in {"<missing>", "<empty>"}:
        reason.append("active epic does not exist in .specify/runtime/active-epic")
    elif manifest is None:
        reason.append("active epic manifest does not exist")
    else:
        if branch_available and current_branch == "<detached>":
            reason.append("current branch is detached HEAD")
        if branch_available and manifest.get("status") != "active":
            reason.append(f"epic status is {manifest.get('status')!r}, expected 'active'")
        if branch_available and current_branch in {"master", "main"}:
            reason.append("implementation is blocked on the base branch")
        if branch_available and current_branch != expected_branch:
            reason.append("current branch does not match the epic manifest")
        for dependency in manifest.get("depends_on") or []:
            dependency_manifest = manifests.get(dependency)
            if dependency_manifest is None:
                reason.append(f"dependency {dependency} manifest does not exist")
            elif dependency_manifest.get("status") != "completed":
                reason.append(f"dependency {dependency} is not completed")
        if task_selector != "next" and task_selector not in (manifest.get("tasks") or []):
            reason.append("task does not belong to the active epic")
        if task_selector == "next" and not (manifest.get("tasks") or []):
            reason.append("active epic has no tasks available")
    if reason or errors:
        details = reason + errors
        return [
            f"active epic: {active_epic}; expected branch: {expected_branch}; "
            f"current branch: {current_branch}; task selector: {task_selector}; "
            f"reason: {detail}; next step: set a valid active epic and check out its declared branch"
            for detail in details
        ]
    return []


def validate_guard(
    task_selector: str = "next",
    runtime_file: Path | str = ACTIVE_EPIC_FILE,
    directory: Path | str = DEFAULT_DIRECTORY,
    tasks_file: Path | str = TASKS_FILE,
) -> list[str]:
    """Run the full read-only guard pipeline before baseline capture."""
    errors: list[str] = []
    errors.extend(validate_manifests(directory))
    errors.extend(validate_task_epic_consistency(tasks_file=tasks_file, directory=directory))
    errors.extend(validate_active_epic(task_selector, runtime_file, directory))
    return errors


def validate_close_preconditions(
    epic_status: str,
    merge_evidence: bool,
    tasks_complete: bool,
    head_in_base: bool,
) -> list[str]:
    """Return blocking reasons for the bookkeeping-only epic close step."""
    errors: list[str] = []
    if epic_status not in {"active", "review"}:
        errors.append("epic status must be active or review")
    if not merge_evidence:
        errors.append("merge evidence is required before closing the epic")
    if not tasks_complete:
        errors.append("all epic tasks must be completed")
    if not head_in_base:
        errors.append("epic branch HEAD must be part of the base branch history")
    return errors


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--guard":
        errors = validate_guard(sys.argv[2])
    elif len(sys.argv) == 1:
        errors = validate_manifests()
    else:
        print("Usage: python -m backend.app.tooling.workstream_validation [--guard next|T\\d{3}[A-Z]?]")
        return 2
    if errors:
        print("Workstream manifest validation failed:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(f"Workstream manifests are valid: {DEFAULT_DIRECTORY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

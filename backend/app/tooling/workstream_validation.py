"""Validate static milestone and epic manifests without Git or network access."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DIRECTORY = ROOT / ".specify" / "workstreams"
ACTIVE_EPIC_FILE = ROOT / ".specify" / "runtime" / "active-epic"
STATUSES = {"planned", "active", "review", "completed", "blocked"}
RISKS = {"low", "medium", "high", "critical"}
TASK_PATTERN = re.compile(r"^T\d{3}(?:A)?$")
MILESTONE_PATTERN = re.compile(r"^M\d{3}$")
EPIC_PATTERN = re.compile(r"^E\d{3}$")


def _scalar(value: str) -> Any:
    value = value.strip()
    if value in {"[]", ""}:
        return []
    if value in {"true", "false"}:
        return value == "true"
    if value.startswith("[") and value.endswith("]"):
        return [_scalar(item) for item in value[1:-1].split(",") if item.strip()]
    return value.strip('`"')


def _parse_yaml_subset(path: Path) -> dict[str, Any]:
    """Parse mappings, inline lists, and indented scalar lists in our manifests."""
    root: dict[str, Any] = {}
    current_list_key: str | None = None
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        line = raw_line.strip()
        if indent == 0 and ":" in line:
            key, raw_value = line.split(":", 1)
            key = key.strip()
            value = [] if key in {"epics", "completion_criteria", "depends_on", "tasks", "required_checks"} and not raw_value.strip() else ({} if not raw_value.strip() else _scalar(raw_value))
            root[key] = value
            current_list_key = key if isinstance(value, list) else None
        elif indent > 0 and line.startswith("-") and current_list_key:
            root[current_list_key].append(_scalar(line[1:].strip()))
        elif indent > 0 and ":" in line:
            key, raw_value = line.split(":", 1)
            parent = next((value for value in root.values() if isinstance(value, dict)), None)
            if parent is None:
                raise ValueError(f"{path.name}:{line_number}: unsupported YAML structure")
            parent[key.strip()] = _scalar(raw_value)
        else:
            raise ValueError(f"{path.name}:{line_number}: unsupported YAML structure")
    return root


def validate_manifests(directory: Path | str = DEFAULT_DIRECTORY) -> list[str]:
    """Return all deterministic validation errors found in *directory*."""
    directory = Path(directory)
    errors: list[str] = []
    if not directory.is_dir():
        return [f"workstreams directory does not exist: {directory}"]
    manifests: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.yml")):
        try:
            manifests.append((path, _parse_yaml_subset(path)))
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
        required = ({"id", "title", "status", "goal", "epics", "completion_criteria"} if is_milestone else {"id", "title", "milestone", "feature", "base_branch", "branch", "status", "risk", "depends_on", "tasks", "required_checks", "pr_policy", "commit_policy"})
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
            epics[identifier] = manifest
    for epic_id, manifest in sorted(epics.items()):
        path = by_id[epic_id][0]
        if manifest.get("milestone") not in milestones:
            errors.append(f"{path.name}: unknown milestone {manifest.get('milestone')!r}")
        for dependency in manifest.get("depends_on", []):
            if dependency not in epics:
                errors.append(f"{path.name}: unknown epic dependency {dependency!r}")
        tasks = manifest.get("tasks", [])
        if manifest.get("status") == "active" and not tasks:
            errors.append(f"{path.name}: active epic must have a non-empty tasks list")
        for task in tasks:
            if not isinstance(task, str) or not TASK_PATTERN.fullmatch(task):
                errors.append(f"{path.name}: invalid task id {task!r}")
    owners: dict[str, str] = {}
    for epic_id, manifest in sorted(epics.items()):
        for task in manifest.get("tasks", []):
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
        for epic_id in manifest.get("epics", []):
            if epic_id not in epics:
                errors.append(f"{path.name}: unknown epic {epic_id!r}")
    return sorted(set(errors))


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
            manifest = _parse_yaml_subset(path)
        except (OSError, ValueError):
            continue
        if isinstance(manifest.get("id"), str):
            manifests[manifest["id"]] = manifest
    manifest = manifests.get(active_epic)
    if manifest:
        expected_branch = str(manifest.get("branch", "<missing>"))
    try:
        current_branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip() or "<detached>"
    except (OSError, subprocess.CalledProcessError) as exc:
        errors.append(f"cannot read current Git branch: {exc}")
    reason: list[str] = []
    if active_epic in {"<missing>", "<empty>"}:
        reason.append("active epic does not exist in .specify/runtime/active-epic")
    elif manifest is None:
        reason.append("active epic manifest does not exist")
    else:
        if manifest.get("status") != "active":
            reason.append(f"epic status is {manifest.get('status')!r}, expected 'active'")
        if current_branch in {"master", "main"}:
            reason.append("implementation is blocked on the base branch")
        if current_branch != expected_branch:
            reason.append("current branch does not match the epic manifest")
        for dependency in manifest.get("depends_on", []):
            dependency_manifest = manifests.get(dependency)
            if dependency_manifest is None:
                reason.append(f"dependency {dependency} manifest does not exist")
            elif dependency_manifest.get("status") != "completed":
                reason.append(f"dependency {dependency} is not completed")
        if task_selector != "next" and task_selector not in manifest.get("tasks", []):
            reason.append("task does not belong to the active epic")
        if task_selector == "next" and not manifest.get("tasks"):
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
        errors = validate_active_epic(sys.argv[2])
    elif len(sys.argv) == 1:
        errors = validate_manifests()
    else:
        print("Usage: python -m backend.app.tooling.workstream_validation [--guard next|T###]")
        return 2
    if errors:
        print("Workstream manifest validation failed:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(f"Workstream manifests are valid: {DEFAULT_DIRECTORY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

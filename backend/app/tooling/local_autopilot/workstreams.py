"""Workstream selection helpers for the local autopilot."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.tooling import task_consistency, workstream_validation

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DIRECTORY = ROOT / ".specify" / "workstreams"
DEFAULT_TASKS_FILE = ROOT / "specs" / "001-ai-content-studio" / "tasks.md"
EPIC_ID_PATTERN = re.compile(r"^E\d{3}$")
MILESTONE_ID_PATTERN = re.compile(r"^M\d{3}$")


def _load_manifest(path: Path) -> dict[str, Any]:
    return workstream_validation._load_yaml_manifest(path)


def _manifest_path(identifier: str, directory: Path) -> Path:
    for path in sorted(directory.glob("*.yml")):
        manifest = _load_manifest(path)
        if manifest.get("id") == identifier:
            return path
    raise FileNotFoundError(f"workstream manifest does not exist: {identifier}")


def _validate_identifier(pattern: re.Pattern[str], value: str, label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value.strip()):
        raise ValueError(f"{label} must match {pattern.pattern}")
    return value.strip()


def _load_task_blocks(tasks_file: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not tasks_file.is_file():
        return records
    for task_id, start_line, lines in task_consistency._iter_task_blocks(tasks_file):
        header = lines[0][1]
        completed = header.startswith("- [X]") or header.startswith("- [x]")
        dependencies_field = task_consistency._field_value(lines, "Dependencies:")
        dependencies: list[str] = []
        if dependencies_field is not None:
            dependencies = _parse_task_dependencies(dependencies_field[1])
        records[task_id] = {
            "task_id": task_id,
            "line": start_line,
            "completed": completed,
            "dependencies": dependencies,
        }
    return records


def _parse_task_dependencies(value: str) -> list[str]:
    normalized = value.strip().strip("`")
    if not normalized or normalized.lower() in {"none", "n/a", "na", "[]"}:
        return []
    return [item.strip().strip("`") for item in normalized.split(",") if item.strip()]


def list_milestones(directory: Path | str = DEFAULT_DIRECTORY) -> list[dict[str, Any]]:
    directory = Path(directory)
    milestones: list[dict[str, Any]] = []
    if not directory.is_dir():
        return milestones
    for path in sorted(directory.glob("*.yml")):
        manifest = _load_manifest(path)
        identifier = manifest.get("id")
        if isinstance(identifier, str) and MILESTONE_ID_PATTERN.fullmatch(identifier):
            milestones.append(manifest)
    return milestones


def get_milestone(milestone_id: str, directory: Path | str = DEFAULT_DIRECTORY) -> dict[str, Any]:
    milestone_id = _validate_identifier(MILESTONE_ID_PATTERN, milestone_id, "milestone_id")
    directory = Path(directory)
    path = _manifest_path(milestone_id, directory)
    manifest = _load_manifest(path)
    if manifest.get("id") != milestone_id:
        raise ValueError(f"{path.name}: milestone id does not match {milestone_id}")
    return manifest


def list_epics(directory: Path | str = DEFAULT_DIRECTORY, *, milestone_id: str | None = None) -> list[dict[str, Any]]:
    directory = Path(directory)
    epics: list[dict[str, Any]] = []
    if not directory.is_dir():
        return epics
    for path in sorted(directory.glob("*.yml")):
        manifest = _load_manifest(path)
        identifier = manifest.get("id")
        if not (isinstance(identifier, str) and EPIC_ID_PATTERN.fullmatch(identifier)):
            continue
        if milestone_id is not None and manifest.get("milestone") != milestone_id:
            continue
        epics.append(manifest)
    return epics


def get_epic(epic_id: str, directory: Path | str = DEFAULT_DIRECTORY) -> dict[str, Any]:
    epic_id = _validate_identifier(EPIC_ID_PATTERN, epic_id, "epic_id")
    directory = Path(directory)
    path = _manifest_path(epic_id, directory)
    manifest = _load_manifest(path)
    if manifest.get("id") != epic_id:
        raise ValueError(f"{path.name}: epic id does not match {epic_id}")
    return manifest


def validate_dependencies(epic_id: str, directory: Path | str = DEFAULT_DIRECTORY) -> list[str]:
    epic = get_epic(epic_id, directory)
    dependencies = epic.get("depends_on") or []
    if not dependencies:
        return []
    epics_by_id = {manifest["id"]: manifest for manifest in list_epics(directory)}
    errors: list[str] = []
    for dependency in dependencies:
        if dependency not in epics_by_id:
            errors.append(f"unknown epic dependency {dependency!r}")
            continue
        dependency_status = str(epics_by_id[dependency].get("status") or "")
        if dependency_status != "completed":
            errors.append(f"dependency {dependency} is not completed")
    return errors


def activate_epic_with_human_authorization(
    epic_id: str,
    *,
    human_authorized: bool,
    directory: Path | str = DEFAULT_DIRECTORY,
) -> dict[str, Any]:
    epic_id = _validate_identifier(EPIC_ID_PATTERN, epic_id, "epic_id")
    directory = Path(directory)
    path = _manifest_path(epic_id, directory)
    manifest = _load_manifest(path)
    current_status = str(manifest.get("status") or "")
    if current_status == "completed":
        raise ValueError("completed epics cannot be reactivated")
    if current_status == "active":
        return manifest
    if current_status != "planned":
        raise ValueError(f"epic status is {current_status!r}, expected 'planned' or 'active'")
    if not human_authorized:
        raise ValueError("human authorization is required to activate a planned epic")
    updated = _replace_status_line(path, "active")
    return updated


def list_epic_tasks(epic_id: str, tasks_file: Path | str = DEFAULT_TASKS_FILE, directory: Path | str = DEFAULT_DIRECTORY) -> list[str]:
    epic = get_epic(epic_id, directory)
    ordered_tasks = [task_id for task_id in (epic.get("tasks") or []) if isinstance(task_id, str)]
    return ordered_tasks


def next_dependency_ready_task(
    epic_id: str,
    *,
    tasks_file: Path | str = DEFAULT_TASKS_FILE,
    directory: Path | str = DEFAULT_DIRECTORY,
) -> str | None:
    epic = get_epic(epic_id, directory)
    task_order = [task_id for task_id in (epic.get("tasks") or []) if isinstance(task_id, str)]
    tasks_file = Path(tasks_file)
    task_records = _load_task_blocks(tasks_file)
    ready: list[str] = []
    for task_id in task_order:
        record = task_records.get(task_id)
        if record is None or record["completed"]:
            continue
        if _dependencies_complete(record["dependencies"], task_records):
            ready.append(task_id)
    if not ready:
        return None
    if len(ready) > 1:
        raise ValueError(f"ambiguous next task for epic {epic_id}: {', '.join(ready)}")
    return ready[0]


def all_epic_tasks_complete(
    epic_id: str,
    *,
    tasks_file: Path | str = DEFAULT_TASKS_FILE,
    directory: Path | str = DEFAULT_DIRECTORY,
) -> bool:
    epic = get_epic(epic_id, directory)
    task_ids = [task_id for task_id in (epic.get("tasks") or []) if isinstance(task_id, str)]
    if not task_ids:
        return False
    task_records = _load_task_blocks(Path(tasks_file))
    return all(task_records.get(task_id, {}).get("completed") is True for task_id in task_ids)


def next_ready_epic_for_milestone(
    milestone_id: str,
    *,
    tasks_file: Path | str = DEFAULT_TASKS_FILE,
    directory: Path | str = DEFAULT_DIRECTORY,
) -> str | None:
    milestone_id = _validate_identifier(MILESTONE_ID_PATTERN, milestone_id, "milestone_id")
    epics = list_epics(directory, milestone_id=milestone_id)
    for epic in epics:
        epic_id = str(epic.get("id") or "")
        if not epic_id:
            continue
        if str(epic.get("status") or "") != "planned":
            continue
        if validate_dependencies(epic_id, directory):
            continue
        next_task = next_dependency_ready_task(epic_id, tasks_file=tasks_file, directory=directory)
        if next_task is not None:
            return epic_id
    return None


def _dependencies_complete(dependencies: list[str], task_records: dict[str, dict[str, Any]]) -> bool:
    for dependency in dependencies:
        dependency_record = task_records.get(dependency)
        if dependency_record is None or dependency_record.get("completed") is not True:
            return False
    return True


def _replace_status_line(path: Path, new_status: str) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    updated_lines: list[str] = []
    replaced = False
    for line in lines:
        if not replaced and line.startswith("status: "):
            current = line.split(":", 1)[1].strip()
            if current == "completed":
                raise ValueError("completed epics cannot be reactivated")
            updated_lines.append(f"status: {new_status}")
            replaced = True
            continue
        updated_lines.append(line)
    if not replaced:
        raise ValueError(f"{path.name}: missing status field")
    path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    return _load_manifest(path)


__all__ = [
    "activate_epic_with_human_authorization",
    "all_epic_tasks_complete",
    "DEFAULT_DIRECTORY",
    "DEFAULT_TASKS_FILE",
    "get_epic",
    "get_milestone",
    "list_epics",
    "list_epic_tasks",
    "list_milestones",
    "next_dependency_ready_task",
    "next_ready_epic_for_milestone",
    "validate_dependencies",
]

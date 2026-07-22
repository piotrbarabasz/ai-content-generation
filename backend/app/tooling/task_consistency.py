"""Shared task-to-workstream consistency helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

TASK_ID_PATTERN = re.compile(r"^T\d{3}[A-Z]?$")
EPIC_PATTERN = re.compile(r"^E\d{3}$")
MILESTONE_PATTERN = re.compile(r"^M\d{3}$")
TASK_HEADER_PATTERN = re.compile(r"^- \[(?P<checkbox>[Xx ])\] (?P<task>T\d{3}[A-Z]?)(?=\s|$)")


@dataclass(frozen=True)
class ConsistencyIndex:
    tasks_by_id: dict[str, dict[str, Any]]
    epic_to_milestone: dict[str, str]
    manifest_task_owners: dict[str, list[dict[str, Any]]]
    epic_tasks: dict[str, list[dict[str, Any]]]


def _load_yaml_manifest(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name}: manifest must be a mapping")
    return loaded


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


def _manifest_task_entries(path: Path) -> list[tuple[str, int]]:
    try:
        root = yaml.compose(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return []
    if not isinstance(root, MappingNode):
        return []
    for key_node, value_node in root.value:
        if isinstance(key_node, ScalarNode) and key_node.value == "tasks" and isinstance(value_node, SequenceNode):
            entries: list[tuple[str, int]] = []
            for item in value_node.value:
                if isinstance(item, ScalarNode):
                    task_id = str(item.value).strip()
                    if task_id:
                        entries.append((task_id, item.start_mark.line + 1))
            return entries
    return []


def _finding(
    path: Path,
    line: int,
    task: str,
    check: str,
    expected: Any,
    actual: Any,
    reason: str,
) -> dict[str, Any]:
    return {
        "path": str(path),
        "line": line,
        "task": task,
        "check": check,
        "phrase": str(actual) if actual is not None else "",
        "expected": expected,
        "actual": actual,
        "reason": reason,
    }


def format_finding(finding: dict[str, Any]) -> str:
    parts = [f"{finding.get('path', '<unknown>')}:{finding.get('line', 0)}"]
    task = finding.get("task")
    if task:
        parts.append(str(task))
    check = finding.get("check")
    if check:
        parts.append(str(check))
    phrase = finding.get("phrase")
    if phrase:
        parts.append(str(phrase))
    expected = finding.get("expected")
    actual = finding.get("actual")
    if expected is not None:
        parts.append(f"expected={expected}")
    if actual is not None:
        parts.append(f"actual={actual}")
    reason = finding.get("reason")
    if reason:
        parts.append(f"- {reason}")
    return " ".join(parts)


def _selected_task(task_id: str, selected_tasks: set[str] | None) -> bool:
    return not selected_tasks or task_id in selected_tasks


def load_consistency_index(
    tasks_file: Path | str,
    directory: Path | str,
) -> tuple[ConsistencyIndex, list[dict[str, Any]]]:
    tasks_file = Path(tasks_file)
    directory = Path(directory)
    findings: list[dict[str, Any]] = []

    tasks_by_id: dict[str, dict[str, Any]] = {}
    if not tasks_file.is_file():
        findings.append(
            _finding(
                tasks_file,
                0,
                "",
                "tasks file",
                "present",
                "missing",
                f"tasks file does not exist: {tasks_file}",
            )
        )
    else:
        for task_id, start_line, lines in _iter_task_blocks(tasks_file):
            epic = _field_value(lines, "Epic:")
            milestone = _field_value(lines, "Milestone:")
            tasks_by_id[task_id] = {
                "path": tasks_file,
                "line": start_line,
                "milestone": milestone[1].strip() if milestone else "",
                "milestone_line": milestone[0] if milestone else start_line,
                "epic": epic[1].strip() if epic else "",
                "epic_line": epic[0] if epic else start_line,
            }

    epic_to_milestone: dict[str, str] = {}
    manifest_task_owners: dict[str, list[dict[str, Any]]] = {}
    epic_tasks: dict[str, list[dict[str, Any]]] = {}

    if not directory.is_dir():
        findings.append(
            _finding(
                directory,
                0,
                "",
                "workstreams directory",
                "present",
                "missing",
                f"missing workstreams directory: {directory}",
            )
        )
        return ConsistencyIndex(tasks_by_id, epic_to_milestone, manifest_task_owners, epic_tasks), findings

    for path in sorted(directory.glob("*.yml")):
        try:
            manifest = _load_yaml_manifest(path)
        except (OSError, ValueError):
            continue
        identifier = manifest.get("id")
        milestone = manifest.get("milestone")
        if isinstance(identifier, str) and EPIC_PATTERN.fullmatch(identifier) and isinstance(milestone, str):
            epic_to_milestone[identifier] = milestone
            epic_tasks.setdefault(identifier, [])
            for task_id, line in _manifest_task_entries(path):
                entry = {"task": task_id, "epic": identifier, "path": path, "line": line}
                epic_tasks[identifier].append(entry)
                manifest_task_owners.setdefault(task_id, []).append(entry)

    return ConsistencyIndex(tasks_by_id, epic_to_milestone, manifest_task_owners, epic_tasks), findings


def validate_consistency(
    tasks_file: Path | str,
    directory: Path | str,
    selected_tasks: set[str] | None = None,
) -> list[dict[str, Any]]:
    index, findings = load_consistency_index(tasks_file, directory)
    selected_tasks = {task.strip() for task in (selected_tasks or set()) if task.strip()} or None
    known_milestones = {milestone for milestone in index.epic_to_milestone.values() if isinstance(milestone, str)}

    for task_id, record in sorted(index.tasks_by_id.items()):
        if not _selected_task(task_id, selected_tasks):
            continue
        task_path = Path(record["path"])
        task_line = int(record["line"])
        task_epic = str(record.get("epic") or "")
        task_milestone = str(record.get("milestone") or "")
        task_owners = index.manifest_task_owners.get(task_id, [])
        owner_epics = sorted({entry["epic"] for entry in task_owners})

        if not task_epic:
            findings.append(
                _finding(
                    task_path,
                    int(record["epic_line"]),
                    task_id,
                    "Epic",
                    "present",
                    "<missing>",
                    "task.Epic is missing",
                )
            )
        elif task_epic not in index.epic_to_milestone:
            findings.append(
                _finding(
                    task_path,
                    int(record["epic_line"]),
                    task_id,
                    "Epic",
                    "known epic manifest",
                    task_epic,
                    "unknown epic",
                )
            )
        else:
            expected_milestone = index.epic_to_milestone[task_epic]
            if task_milestone and task_milestone != expected_milestone:
                findings.append(
                    _finding(
                        task_path,
                        int(record["milestone_line"]),
                        task_id,
                        "Milestone",
                        expected_milestone,
                        task_milestone,
                        "task.Milestone does not match the epic milestone",
                    )
                )

        if not task_milestone:
            findings.append(
                _finding(
                    task_path,
                    int(record["milestone_line"]),
                    task_id,
                    "Milestone",
                    "present",
                    "<missing>",
                    "task.Milestone is missing",
                )
            )
        elif task_milestone not in known_milestones:
            findings.append(
                _finding(
                    task_path,
                    int(record["milestone_line"]),
                    task_id,
                    "Milestone",
                    "known milestone",
                    task_milestone,
                    "unknown milestone",
                )
            )

        if not task_owners:
            findings.append(
                _finding(
                    task_path,
                    task_line,
                    task_id,
                    "Epic manifest",
                    "one manifest entry",
                    "<missing>",
                    "task is omitted from all epic manifests",
                )
            )
        elif len(owner_epics) > 1:
            findings.append(
                _finding(
                    task_path,
                    task_line,
                    task_id,
                    "Epic manifest",
                    "one epic",
                    ", ".join(owner_epics),
                    "task is present in multiple epic manifests",
                )
            )

        if task_epic and task_epic in index.epic_to_milestone:
            epic_task_ids = {entry["task"] for entry in index.epic_tasks.get(task_epic, [])}
            if task_id not in epic_task_ids:
                findings.append(
                    _finding(
                        task_path,
                        task_line,
                        task_id,
                        "Epic manifest tasks",
                        task_epic,
                        "<missing>",
                        "task is not listed by its epic manifest",
                    )
                )

        for owner in task_owners:
            owner_epic = owner["epic"]
            if task_epic and owner_epic != task_epic:
                findings.append(
                    _finding(
                        task_path,
                        task_line,
                        task_id,
                        "Epic",
                        task_epic,
                        owner_epic,
                        "task is listed in the wrong epic manifest",
                    )
                )

    for epic_id, task_entries in sorted(index.epic_tasks.items()):
        expected_milestone = index.epic_to_milestone.get(epic_id, "")
        for entry in task_entries:
            if not _selected_task(entry["task"], selected_tasks):
                continue
            task_id = entry["task"]
            task_record = index.tasks_by_id.get(task_id)
            if task_record is None:
                findings.append(
                    _finding(
                        Path(entry["path"]),
                        int(entry["line"]),
                        task_id,
                        "task in tasks.md",
                        "present",
                        "<missing>",
                        "manifest references a task that does not exist in tasks.md",
                    )
                )
                continue
            if task_record.get("epic") != epic_id:
                findings.append(
                    _finding(
                        Path(entry["path"]),
                        int(entry["line"]),
                        task_id,
                        "Epic",
                        epic_id,
                        task_record.get("epic") or "<missing>",
                        "task is listed under the wrong epic manifest",
                    )
                )
            task_milestone = str(task_record.get("milestone") or "")
            if expected_milestone and task_milestone and task_milestone != expected_milestone:
                findings.append(
                    _finding(
                        Path(entry["path"]),
                        int(entry["line"]),
                        task_id,
                        "Milestone",
                        expected_milestone,
                        task_milestone,
                        "task.Milestone does not match the epic milestone",
                    )
                )

    return findings

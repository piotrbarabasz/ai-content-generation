"""Bounded, deterministic repository checks for agent workflows."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .workstream_validation import _load_yaml_manifest as _load_workstream_manifest

ROOT = Path(__file__).resolve().parents[3]
TASKS_FILE = ROOT / "specs" / "001-ai-content-studio" / "tasks.md"
WORKSTREAMS_DIR = ROOT / ".specify" / "workstreams"
TIMEOUT_SECONDS = 20
MAX_OUTPUT_LINES = 200
MAX_LINE_LENGTH = 300
TASK_ID_PATTERN = re.compile(r"^T\d{3}[A-Z]?$")
TASK_HEADER_PATTERN = re.compile(r"^- \[(?P<checkbox>[Xx ])\] (?P<task>T\d{3}[A-Z]?)(?=\s|$)")
TASK_MARKER_PATTERN = re.compile(r"^- \[(?P<checkbox>.)\] (?P<task>.+)$")
RISK_VALUES = {"low", "medium", "high", "critical"}
FORBIDDEN_TEST_PHRASES = ("tests later", "test later", "add tests later", "add test later", "phase 10")
DIRECT_TEST_LANGUAGE = re.compile(r"\b(unit|behavioral|behavior|integration|regression|smoke|pytest|test|tests)\b", re.I)


def _normalize(value: str) -> str:
    return value.strip().strip("`").rstrip(".").lower()


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
        or normalized.startswith("none`")
        or normalized.startswith("`none")
        or normalized in {"n/a", "na", "[]"}
    )


def _truncate_lines(value: str) -> tuple[list[str], bool]:
    raw_lines = value.splitlines()
    truncated = len(raw_lines) > MAX_OUTPUT_LINES or any(len(line) > MAX_LINE_LENGTH for line in raw_lines)
    if truncated:
        raw_lines = raw_lines[: MAX_OUTPUT_LINES - 1]
    limited = [line[:MAX_LINE_LENGTH] for line in raw_lines]
    if truncated:
        limited.append("[output truncated]")
    return limited, truncated


def _combine_output(stdout: str, stderr: str) -> str:
    stdout = stdout.rstrip("\n")
    stderr = stderr.rstrip("\n")
    if stdout and stderr:
        return f"{stdout}\n[stderr]\n{stderr}"
    return stdout or stderr


def _command_name(command: list[str]) -> str:
    return "_".join(part.replace("-", "_") for part in command)


def run_process(command: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"GIT_PAGER": "cat", "PAGER": "cat", "TERM": "dumb"})
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            timeout=TIMEOUT_SECONDS,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "TIMEOUT",
            "command": command,
            "exit_code": None,
            "output_lines": [f"command timed out after {TIMEOUT_SECONDS} seconds"],
            "truncated": False,
        }
    except FileNotFoundError:
        return {
            "status": "MISSING",
            "command": command,
            "exit_code": None,
            "output_lines": [f"missing executable: {command[0]}"],
            "truncated": False,
        }
    except OSError as exc:
        return {
            "status": "FAIL",
            "command": command,
            "exit_code": None,
            "output_lines": [f"command failed to start: {exc}"],
            "truncated": False,
        }
    output, truncated = _truncate_lines(_combine_output(result.stdout or "", result.stderr or ""))
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "command": command,
        "exit_code": result.returncode,
        "output_lines": output,
        "truncated": truncated,
    }


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


def _format_findings(findings: list[dict[str, Any]]) -> list[str]:
    formatted: list[str] = []
    for finding in findings:
        formatted.append(
            f"{finding['path']}:{finding['line']} {finding['task']} {finding['phrase']} - {finding['reason']}"
        )
    return formatted


def _report_task_line(findings: list[dict[str, Any]], path: Path, line_number: int, task: str, phrase: str, reason: str) -> None:
    findings.append({"path": str(path), "line": line_number, "task": task, "phrase": phrase, "reason": reason})


def _parse_dependency_list(value: str) -> list[str]:
    if _is_none_value(value):
        return []
    return [dependency.strip().strip("`") for dependency in value.split(",") if dependency.strip()]


def _load_epic_dependencies(directory: Path) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    epic_dependencies: dict[str, list[str]] = {}
    findings: list[dict[str, Any]] = []
    if not directory.is_dir():
        findings.append(
            {
                "path": str(directory),
                "line": 0,
                "task": "",
                "phrase": "",
                "reason": f"missing workstreams directory: {directory}",
            }
        )
        return epic_dependencies, findings
    for path in sorted(directory.glob("*.yml")):
        try:
            manifest = _load_workstream_manifest(path)
        except (OSError, ValueError) as exc:
            findings.append({"path": str(path), "line": 0, "task": "", "phrase": "", "reason": str(exc)})
            continue
        identifier = manifest.get("id")
        if isinstance(identifier, str) and re.fullmatch(r"E\d{3}", identifier):
            depends_on = [dependency for dependency in manifest.get("depends_on", []) if isinstance(dependency, str)]
            epic_dependencies[identifier] = depends_on
    return epic_dependencies, findings


def _epic_reaches(epic_dependencies: dict[str, list[str]], source: str, target: str) -> bool:
    if source == target:
        return True
    visiting: list[str] = [source]
    seen: set[str] = set()
    while visiting:
        current = visiting.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        visiting.extend(epic_dependencies.get(current, []))
    return False


def task_metadata(tasks: list[str] | None = None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not TASKS_FILE.is_file():
        return [
            {
                "path": str(TASKS_FILE),
                "line": 0,
                "task": "",
                "phrase": "",
                "reason": f"missing file: {TASKS_FILE}",
            }
        ]
    selected = {task.strip() for task in (tasks or []) if task.strip()}
    records: dict[str, dict[str, Any]] = {}
    with TASKS_FILE.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.rstrip("\n")
            if not line.startswith("- ["):
                continue
            header = TASK_HEADER_PATTERN.match(line)
            if header:
                continue
            marker = TASK_MARKER_PATTERN.match(line)
            if not marker:
                continue
            checkbox = marker.group("checkbox")
            task_text = marker.group("task").strip()
            task_id = task_text.split(maxsplit=1)[0] if task_text else ""
            if checkbox not in {" ", "X", "x"}:
                _report_task_line(findings, TASKS_FILE, line_number, task_id, checkbox, "invalid checkbox")
            if not TASK_ID_PATTERN.fullmatch(task_id):
                _report_task_line(findings, TASKS_FILE, line_number, task_id, task_id, "invalid task ID")

    for task_id, start_line, lines in _iter_task_blocks(TASKS_FILE):
        if selected and task_id not in selected:
            continue
        header = lines[0][1]
        if not TASK_HEADER_PATTERN.match(header):
            _report_task_line(findings, TASKS_FILE, start_line, task_id, "", "invalid task header")
            continue
        if not TASK_ID_PATTERN.fullmatch(task_id):
            _report_task_line(findings, TASKS_FILE, start_line, task_id, task_id, "invalid task ID")
            continue

        milestone = _field_value(lines, "Milestone:")
        epic = _field_value(lines, "Epic:")
        risk = _field_value(lines, "Risk:")
        implementation = _field_value(lines, "Implementation files:")
        test_files = _field_value(lines, "Test files:")
        dependencies = _field_value(lines, "Dependencies:")
        test_requirements = _field_value(lines, "Test requirements:")

        required_fields = (
            "Milestone:",
            "Risk:",
            "Implementation files:",
            "Test files:",
            "Validation commands:",
            "Acceptance criteria:",
        )
        for field_name in required_fields:
            if _field_value(lines, field_name) is None:
                _report_task_line(findings, TASKS_FILE, start_line, task_id, field_name, "required task field is missing")
        if epic is None:
            _report_task_line(findings, TASKS_FILE, start_line, task_id, "Epic:", "task does not declare an epic")
        if dependencies is None:
            _report_task_line(findings, TASKS_FILE, start_line, task_id, "Dependencies:", "task does not declare dependencies")

        if milestone and not re.fullmatch(r"M\d{3}", milestone[1]):
            _report_task_line(findings, TASKS_FILE, milestone[0], task_id, milestone[1], "invalid milestone")
        if epic and not re.fullmatch(r"E\d{3}", epic[1]):
            _report_task_line(findings, TASKS_FILE, epic[0], task_id, epic[1], "invalid epic")
        if risk and _normalize(risk[1]) not in RISK_VALUES:
            _report_task_line(findings, TASKS_FILE, risk[0], task_id, risk[1], "invalid risk")

        if implementation and test_files and test_requirements:
            implementation_is_real = not _is_none_value(implementation[1])
            test_files_are_none = _is_none_value(test_files[1])
            requirements_text = test_requirements[1]
            requirements_lower = _normalize(requirements_text)
            if implementation_is_real and test_files_are_none:
                phrase = next((phrase for phrase in FORBIDDEN_TEST_PHRASES if phrase in requirements_lower), "")
                if phrase:
                    _report_task_line(
                        findings,
                        TASKS_FILE,
                        test_requirements[0],
                        task_id,
                        phrase,
                        "implementation task defers direct unit or behavioral tests",
                    )
                elif DIRECT_TEST_LANGUAGE.search(requirements_text) and "remediation task" not in requirements_lower:
                    _report_task_line(
                        findings,
                        TASKS_FILE,
                        test_requirements[0],
                        task_id,
                        requirements_text,
                        "test files are none while test requirements still ask for direct tests",
                    )
            if not test_files_are_none and _is_none_value(test_requirements[1]):
                _report_task_line(
                    findings,
                    TASKS_FILE,
                    test_requirements[0],
                    task_id,
                    test_requirements[1],
                    "test files are listed but test requirements say none",
                )

        records[task_id] = {
            "task_id": task_id,
            "start_line": start_line,
            "epic": epic[1].strip() if epic else "",
            "epic_line": epic[0] if epic else start_line,
            "dependencies": _parse_dependency_list(dependencies[1]) if dependencies else [],
            "dependencies_line": dependencies[0] if dependencies else start_line,
        }

    epic_dependencies, manifest_findings = _load_epic_dependencies(WORKSTREAMS_DIR)
    findings.extend(manifest_findings)

    graph: dict[str, list[str]] = {}
    for task_id, record in records.items():
        graph[task_id] = [dependency for dependency in record["dependencies"] if dependency in records]

    for task_id, record in records.items():
        if selected and task_id not in selected:
            continue
        source_epic = record["epic"]
        if not source_epic:
            continue
        for dependency in record["dependencies"]:
            dependency_record = records.get(dependency)
            dependency_line = record["dependencies_line"]
            if dependency_record is None:
                _report_task_line(
                    findings,
                    TASKS_FILE,
                    dependency_line,
                    task_id,
                    dependency,
                    "unknown dependency task",
                )
                continue
            dependency_epic = dependency_record["epic"]
            if not dependency_epic:
                _report_task_line(
                    findings,
                    TASKS_FILE,
                    dependency_line,
                    task_id,
                    dependency,
                    "dependency task does not declare an epic",
                )
                continue
            if dependency_epic == source_epic:
                continue
            if not _epic_reaches(epic_dependencies, source_epic, dependency_epic):
                _report_task_line(
                    findings,
                    TASKS_FILE,
                    dependency_line,
                    task_id,
                    dependency,
                    f"epic {source_epic} does not depend on epic {dependency_epic}",
                )

    visiting: list[str] = []
    visiting_set: set[str] = set()
    visited: set[str] = set()
    reported_cycles: set[tuple[str, ...]] = set()

    def visit(node: str) -> None:
        if node in visiting_set:
            cycle_start = visiting.index(node)
            cycle = visiting[cycle_start:] + [node]
            signature = tuple(cycle)
            if signature not in reported_cycles and (not selected or any(task in selected for task in cycle)):
                reported_cycles.add(signature)
                _report_task_line(
                    findings,
                    TASKS_FILE,
                    records[node]["dependencies_line"],
                    node,
                    " -> ".join(cycle),
                    "task dependency cycle",
                )
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

    return findings



def git_checks() -> list[dict[str, Any]]:
    return [
        run_process(["git", "--no-pager", "diff", "--check"]),
        run_process(["git", "status", "--porcelain=v1", "--untracked-files=all"]),
        run_process(["git", "diff", "--name-only"]),
        run_process(["git", "diff", "--cached", "--name-only"]),
        run_process(["git", "ls-files", "--others", "--exclude-standard"]),
    ]


def checks(mode: str, tasks: list[str] | None = None) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    if mode in {"task-metadata", "preflight", "pre-review"}:
        findings = task_metadata(tasks)
        results.append(
            {
                "name": "task_metadata",
                "status": "FAIL" if findings else "PASS",
                "exit_code": 1 if findings else 0,
                "findings": findings[:MAX_OUTPUT_LINES],
            }
        )
    if mode in {"preflight", "pre-review"}:
        for result in git_checks():
            result["name"] = _command_name(result["command"])
            results.append(result)
    status = "PASS"
    if any(item["status"] == "TIMEOUT" for item in results):
        status = "TIMEOUT"
    elif any(item["status"] == "MISSING" for item in results):
        status = "MISSING"
    elif any(item["status"] != "PASS" for item in results):
        status = "FAIL"
    return {"status": status, "checks": results}


def _print_checks(result: dict[str, Any]) -> None:
    for item in result["checks"]:
        print(f"CHECK: {item.get('name', 'task_metadata')}")
        print(f"STATUS: {item['status']}")
        print(f"EXIT_CODE: {item.get('exit_code')}")
        command = item.get("command", [])
        if command:
            print(f"COMMAND: {' '.join(command)}")
        if "findings" in item:
            print("FINDINGS:")
            for line in _format_findings(item["findings"]):
                print(line)
        if "output_lines" in item:
            print("OUTPUT:")
            for line in item["output_lines"]:
                print(line)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["preflight", "task-metadata", "pre-review"], default="preflight")
    parser.add_argument("--tasks", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    requested_tasks = [item.strip() for item in args.tasks.split(",") if item.strip()]
    result = checks(args.mode, requested_tasks)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_checks(result)
        print(f"STATUS: {result['status']}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

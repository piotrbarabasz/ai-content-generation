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

ROOT = Path(__file__).resolve().parents[3]
TASKS_FILE = ROOT / "specs" / "001-ai-content-studio" / "tasks.md"
TIMEOUT_SECONDS = 20
MAX_LINES = 200
MAX_LINE_LENGTH = 300
TASK_PATTERN = re.compile(r"^T\d{3}[A-Z]?$")
FORBIDDEN = ("tests later", "test later", "add tests later", "add test later", "phase 10")


def _limited(value: str) -> tuple[list[str], bool]:
    lines = value.splitlines()
    shortened = len(lines) > MAX_LINES
    lines = lines[:MAX_LINES]
    result = [line[:MAX_LINE_LENGTH] for line in lines]
    if shortened or any(len(line) > MAX_LINE_LENGTH for line in value.splitlines()[:MAX_LINES]):
        result.append("[output truncated]")
    return result, shortened


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
        return {"status": "TIMEOUT", "command": command, "exit_code": None, "findings": ["command timed out after 20 seconds"]}
    output, _ = _limited((result.stdout or "") + (result.stderr or ""))
    return {"status": "PASS" if result.returncode == 0 else "FAIL", "command": command, "exit_code": result.returncode, "findings": output}


def _task_blocks(text: str) -> list[tuple[str, int, str]]:
    lines = text.splitlines()
    blocks: list[tuple[str, int, str]] = []
    current: list[str] = []
    start = 0
    for number, line in enumerate(lines, 1):
        if re.match(r"^- \[[Xx ]\] T\d{3}[A-Z]?\b", line):
            if current:
                blocks.append((re.match(r"^- \[[Xx ]\] (T\d{3}[A-Z]?)\b", current[0]).group(1), start, "\n".join(current)))
            current = [line]
            start = number
        elif current:
            current.append(line)
    if current:
        blocks.append((re.match(r"^- \[[Xx ]\] (T\d{3}[A-Z]?)\b", current[0]).group(1), start, "\n".join(current)))
    return blocks


def task_metadata(tasks: list[str] | None = None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not TASKS_FILE.is_file():
        return [{"line": 0, "task": "", "phrase": "", "reason": f"missing file: {TASKS_FILE}"}]
    selected = set(tasks or [])
    text = TASKS_FILE.read_text(encoding="utf-8")
    for task, start, block in _task_blocks(text):
        if selected and task not in selected:
            continue
        header = block.splitlines()[0]
        if not re.match(r"^- \[[Xx ]\] T\d{3}[A-Z]?\b", header):
            findings.append({"line": start, "task": task, "phrase": "", "reason": "invalid task header"})
        if not TASK_PATTERN.fullmatch(task):
            findings.append({"line": start, "task": task, "phrase": task, "reason": "invalid task ID"})
        for field in ("Milestone:", "Epic:", "Risk:", "Implementation files:", "Test files:", "Validation commands:", "Acceptance criteria:"):
            if not re.search(rf"(?m)^{re.escape(field)}", block):
                findings.append({"line": start, "task": task, "phrase": field, "reason": "required task field is missing"})
        implementation = re.search(r"(?m)^Implementation files: (.+)$", block)
        tests = re.search(r"(?m)^Test files: (.+)$", block)
        requirements = re.search(r"(?m)^Test requirements: (.+)$", block)
        if implementation and tests and requirements:
            is_implementation = implementation.group(1).strip().lower() not in {"none", "`none`"}
            direct_scope = not re.search(r"integration|regression|smoke|cross[- ]module|workflow scenario", requirements.group(1), re.I)
            requirements_text = requirements.group(1).lower()
            for phrase in FORBIDDEN:
                if phrase in requirements_text and is_implementation and direct_scope:
                    line = start + next((index for index, value in enumerate(block.splitlines()) if phrase in value.lower()), 0)
                    findings.append({"line": line, "task": task, "phrase": phrase, "reason": "implementation task defers direct tests"})
                    break
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
        results.append({"name": "task_metadata", "status": "FAIL" if findings else "PASS", "exit_code": 1 if findings else 0, "findings": findings[:MAX_LINES]})
    if mode in {"preflight", "pre-review"}:
        for result in git_checks():
            result["name"] = "_".join(result["command"])
            results.append(result)
    status = "PASS" if all(item["status"] == "PASS" for item in results) else ("TIMEOUT" if any(item["status"] == "TIMEOUT" for item in results) else "FAIL")
    return {"status": status, "checks": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["preflight", "task-metadata", "pre-review"], default="preflight")
    parser.add_argument("--tasks", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = checks(args.mode, [item.strip() for item in args.tasks.split(",") if item.strip()])
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in result["checks"]:
            print(f"CHECK: {item.get('name', 'task_metadata')}\nSTATUS: {item['status']}\nCOMMAND: {' '.join(item.get('command', []))}\nEXIT_CODE: {item.get('exit_code')}\nFINDINGS: {item.get('findings', [])}")
        print(f"STATUS: {result['status']}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

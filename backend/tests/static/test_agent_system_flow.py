from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - PyYAML is declared by the repo
    yaml = None


ROOT = Path(__file__).resolve().parents[3]
AGENTS_PATH = ROOT / "AGENTS.md"
README_PATH = ROOT / "README.md"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "agent-system-validation.yml"
LOOP_SKILL_PATH = ROOT / ".agents" / "skills" / "speckit-loop" / "SKILL.md"
IMPLEMENT_SKILL_PATH = ROOT / ".agents" / "skills" / "speckit-implement" / "SKILL.md"
EPIC_REVIEW_SKILL_PATH = ROOT / ".agents" / "skills" / "speckit-epic-review" / "SKILL.md"
AGENT_TOML_PATHS = sorted((ROOT / ".codex" / "agents").glob("*.toml"))
RUNNER_PATH = ROOT / "backend" / "app" / "tooling" / "git_hook_runner.py"
TASKS_PATH = ROOT / "specs" / "001-ai-content-studio" / "tasks.md"

FORBIDDEN_COMMANDS = [
    "python -m backend.app.tooling.workstream_validation",
    "python -m backend.app.tooling.repository_checks",
    "git status --short",
    "git diff --name-only",
    "git diff --cached --name-only",
    "git --no-pager diff --check",
    "git ls-files --others",
    ".specify/scripts/powershell/check-prerequisites.ps1",
]


def _task_blocks() -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    task_start = re.compile(r"^- \[(?: |X|x)\] T\d{3}[A-Z]?\b")

    for line in TASKS_PATH.read_text(encoding="utf-8").splitlines():
        if task_start.match(line):
            if current:
                blocks.append(current)
            current = [line]
            continue
        if current:
            current.append(line)

    if current:
        blocks.append(current)

    return blocks


def _line_value(block: list[str], prefix: str) -> str:
    for line in block:
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    raise AssertionError(f"Missing {prefix!r} in block: {block[0]}")


def _task_id(block: list[str]) -> str:
    match = re.match(r"^- \[(?: |X|x)\] (T\d{3}[A-Z]?)\b", block[0])
    assert match is not None
    return match.group(1)


def _expected_validation_command(test_files_line: str) -> str:
    if "none" in test_files_line.lower():
        return "none"
    files = re.findall(r"`([^`]+)`", test_files_line)
    if not files:
        raise AssertionError(f"Could not parse test files from: {test_files_line}")
    return f"python -m pytest {' '.join(files)}"


class AgentSystemFlowTests(unittest.TestCase):
    def test_agent_docs_use_preflight_finalize_and_avoid_direct_validation_commands(self) -> None:
        for path in [
            AGENTS_PATH,
            LOOP_SKILL_PATH,
            IMPLEMENT_SKILL_PATH,
            EPIC_REVIEW_SKILL_PATH,
            *AGENT_TOML_PATHS,
        ]:
            text = path.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN_COMMANDS:
                self.assertNotIn(forbidden, text, f"{forbidden} should not appear in {path}")

        agents = AGENTS_PATH.read_text(encoding="utf-8")
        loop_skill = LOOP_SKILL_PATH.read_text(encoding="utf-8")
        reviewer_skill = EPIC_REVIEW_SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("agent_task_preflight --selector <selector> --json", agents)
        self.assertIn("agent_task_finalize --task <task> --json", agents)
        self.assertIn("agent_task_preflight --selector <selector> --json", loop_skill)
        self.assertIn("FEATURE_DIR", loop_skill)
        self.assertIn("AVAILABLE_DOCS", loop_skill)
        self.assertIn("optional artifact paths", loop_skill)
        self.assertIn("agent_task_finalize --task <task> --json", loop_skill)
        self.assertIn("never part of the happy path", loop_skill)
        happy_path = loop_skill.split("Run this sequence serially on the happy path:")[1].split(
            "Never run two write-capable agents concurrently."
        )[0]
        self.assertNotIn("spec_debugger", happy_path)
        self.assertEqual(happy_path.count("agent_task_finalize --json"), 1)

        self.assertNotIn(".specify/scripts/powershell/check-prerequisites.ps1", agents)
        self.assertNotIn(".specify/scripts/powershell/check-prerequisites.ps1", loop_skill)

    def test_programmer_and_debugger_docs_do_not_allow_full_pytest_runs(self) -> None:
        forbidden_bare_pytest = re.compile(r"^\s*python -m pytest\s*$", re.MULTILINE)
        for path in [
            AGENTS_PATH,
            LOOP_SKILL_PATH,
            IMPLEMENT_SKILL_PATH,
            ROOT / ".codex" / "agents" / "spec-manager.toml",
            ROOT / ".codex" / "agents" / "spec-explorer.toml",
            ROOT / ".codex" / "agents" / "spec-programmer-fast.toml",
            ROOT / ".codex" / "agents" / "spec-programmer-high.toml",
            ROOT / ".codex" / "agents" / "spec-debugger.toml",
            ROOT / ".codex" / "agents" / "spec-reviewer.toml",
        ]:
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, forbidden_bare_pytest, path)

        debugger_text = (ROOT / ".codex" / "agents" / "spec-debugger.toml").read_text(encoding="utf-8")
        self.assertIn("failing task-focused validation commands", debugger_text)
        self.assertIn("full `python -m pytest`", debugger_text)
        self.assertIn("Start with the failing task-focused tests", debugger_text)

        programmer_fast = (ROOT / ".codex" / "agents" / "spec-programmer-fast.toml").read_text(encoding="utf-8")
        programmer_high = (ROOT / ".codex" / "agents" / "spec-programmer-high.toml").read_text(encoding="utf-8")
        self.assertIn("full `python -m pytest`", programmer_fast)
        self.assertIn("full `python -m pytest`", programmer_high)

    def test_workflow_uses_git_hook_runner_ci(self) -> None:
        if yaml is None:  # pragma: no cover - declared dependency should provide PyYAML
            self.fail("PyYAML is required to parse the workflow")

        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.assertEqual(workflow["name"], "agent-system-validation")

        jobs = workflow["jobs"]
        self.assertIn("validate-agent-system", jobs)
        steps = jobs["validate-agent-system"]["steps"]
        checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v4")
        self.assertEqual(checkout["with"]["fetch-depth"], 0)
        self.assertTrue(
            any(
                step.get("run")
                == 'python -m backend.app.tooling.git_hook_runner ci --base-sha "${{ github.event.pull_request.base.sha }}" --head-sha "${{ github.sha }}"'
                for step in steps
            )
        )
        self.assertTrue(
            any(
                step.get("run")
                == 'python -m backend.app.tooling.git_hook_runner ci --base-sha "${{ github.event.before }}" --head-sha "${{ github.sha }}"'
                for step in steps
            )
        )
        self.assertFalse(any("workstream_validation" in str(step) for step in steps))
        self.assertFalse(any("repository_checks" in str(step) for step in steps))
        self.assertFalse(any("git --no-pager diff --check" in str(step) for step in steps))

        readme = README_PATH.read_text(encoding="utf-8")
        self.assertIn("Developer Setup", readme)
        self.assertIn("git config --local --get core.hooksPath", readme)
        self.assertIn(".githooks", readme)
        self.assertIn("hooks run on commit and push", readme)
        self.assertIn("active Python interpreter", readme)
        self.assertIn("Python 3.11+", readme)
        self.assertIn("debugger is not part of the happy path", readme)

    def test_unchecked_tasks_use_task_specific_validation_commands(self) -> None:
        for block in _task_blocks():
            if block[0].startswith("- [X]") or block[0].startswith("- [x]"):
                continue

            task_id = _task_id(block)
            test_files_line = _line_value(block, "Test files: ")
            validation_line = _line_value(block, "Validation commands: ")
            expected = _expected_validation_command(test_files_line)
            actual = validation_line.strip().strip("`")
            self.assertEqual(actual, expected, task_id)
            self.assertNotIn("git diff --check", actual, task_id)
            self.assertNotEqual(actual, "python -m pytest", task_id)

        self.assertIn("Validation commands: none", TASKS_PATH.read_text(encoding="utf-8"))

    def test_runner_retains_full_pytest_for_pre_push_and_ci(self) -> None:
        runner = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn('name="pytest_full"', runner)
        self.assertIn("pre-push", runner)
        self.assertIn("ci", runner)
        self.assertIn("GLOBAL_TIMEOUTS", runner)


if __name__ == "__main__":
    unittest.main()

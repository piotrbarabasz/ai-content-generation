from __future__ import annotations

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
SKILL_PATHS = [
    ROOT / ".agents" / "skills" / "speckit-loop" / "SKILL.md",
    ROOT / ".agents" / "skills" / "speckit-epic-start" / "SKILL.md",
    ROOT / ".agents" / "skills" / "speckit-epic-review" / "SKILL.md",
    ROOT / ".agents" / "skills" / "speckit-epic-pr" / "SKILL.md",
    ROOT / ".agents" / "skills" / "speckit-epic-close" / "SKILL.md",
]
AGENT_TOML_PATHS = [
    ROOT / ".codex" / "agents" / "spec-manager.toml",
    ROOT / ".codex" / "agents" / "spec-explorer.toml",
    ROOT / ".codex" / "agents" / "spec-programmer.toml",
    ROOT / ".codex" / "agents" / "spec-programmer-fast.toml",
    ROOT / ".codex" / "agents" / "spec-programmer-high.toml",
    ROOT / ".codex" / "agents" / "spec-reviewer.toml",
    ROOT / ".codex" / "agents" / "spec-epic-reviewer.toml",
]
FORBIDDEN_COMMANDS = [
    "python -m backend.app.tooling.workstream_validation",
    "python -m backend.app.tooling.repository_checks",
    "git status --short",
    "git diff --name-only",
    "git diff --cached --name-only",
    "git --no-pager diff --check",
    "git ls-files --others",
]


class AgentSystemFlowTests(unittest.TestCase):
    def test_agent_docs_use_preflight_finalize_and_avoid_direct_validation_commands(self) -> None:
        for path in [AGENTS_PATH, *SKILL_PATHS, *AGENT_TOML_PATHS]:
            text = path.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN_COMMANDS:
                self.assertNotIn(forbidden, text, f"{forbidden} should not appear in {path}")

        self.assertIn("agent_task_preflight --json", AGENTS_PATH.read_text(encoding="utf-8"))
        self.assertIn("agent_task_finalize --task <task> --json", AGENTS_PATH.read_text(encoding="utf-8"))
        loop_skill = (ROOT / ".agents" / "skills" / "speckit-loop" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("agent_task_preflight --selector <selector> --json", loop_skill)
        self.assertIn("agent_task_finalize --task <task> --json", loop_skill)
        self.assertIn("agent_task_finalize --task <task> --json", (ROOT / ".codex" / "agents" / "spec-reviewer.toml").read_text(encoding="utf-8"))
        self.assertIn("agent_task_preflight --selector <selector> --json", (ROOT / ".codex" / "agents" / "spec-manager.toml").read_text(encoding="utf-8"))
        self.assertIn("preflight report", (ROOT / ".codex" / "agents" / "spec-explorer.toml").read_text(encoding="utf-8"))
        self.assertIn("finalize report", (ROOT / ".codex" / "agents" / "spec-reviewer.toml").read_text(encoding="utf-8"))
        self.assertIn("finalized task reports", (ROOT / ".codex" / "agents" / "spec-epic-reviewer.toml").read_text(encoding="utf-8"))

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
        self.assertIn("scripts\\setup-dev.ps1", readme)
        self.assertIn("scripts/setup-dev.sh", readme)
        self.assertIn("python -m backend.app.tooling.agent_task_preflight --selector <selector> --json", readme)
        self.assertIn("python -m backend.app.tooling.agent_task_finalize --task <task> --json", readme)


if __name__ == "__main__":
    unittest.main()

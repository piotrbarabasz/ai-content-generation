import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / ".codex" / "config.toml"
MANAGER_PATH = ROOT / ".codex" / "agents" / "spec-manager.toml"
FAST_AGENT_PATH = ROOT / ".codex" / "agents" / "spec-programmer-fast.toml"
HIGH_AGENT_PATH = ROOT / ".codex" / "agents" / "spec-programmer-high.toml"
AGENTS_PATH = ROOT / "AGENTS.md"
SKILL_PATH = ROOT / ".agents" / "skills" / "speckit-loop" / "SKILL.md"


class AgentRiskRoutingTests(unittest.TestCase):
    def test_supported_models_and_workspace_write_programmers(self) -> None:
        config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        agents = config["agents"]

        self.assertIn("spec_programmer_fast", agents)
        self.assertIn("spec_programmer_high", agents)
        self.assertEqual(
            agents["spec_programmer_fast"]["config_file"],
            "agents/spec-programmer-fast.toml",
        )
        self.assertEqual(
            agents["spec_programmer_high"]["config_file"],
            "agents/spec-programmer-high.toml",
        )

        fast = tomllib.loads(FAST_AGENT_PATH.read_text(encoding="utf-8"))
        high = tomllib.loads(HIGH_AGENT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(fast["name"], "spec_programmer_fast")
        self.assertEqual(fast["model"], "gpt-5.4-mini")
        self.assertEqual(fast["model_reasoning_effort"], "medium")
        self.assertEqual(fast["sandbox_mode"], "workspace-write")

        self.assertEqual(high["name"], "spec_programmer_high")
        self.assertEqual(high["model"], "gpt-5.6")
        self.assertEqual(high["model_reasoning_effort"], "high")
        self.assertEqual(high["sandbox_mode"], "workspace-write")

    def test_risk_routing_rules_and_writer_serialization_are_documented(self) -> None:
        manager_text = MANAGER_PATH.read_text(encoding="utf-8")
        agents_text = AGENTS_PATH.read_text(encoding="utf-8")
        skill_text = SKILL_PATH.read_text(encoding="utf-8")

        for token in [
            "RISK_LEVEL",
            "PROGRAMMER_ROUTE",
            "HUMAN_CHECKPOINT_REQUIRED",
            "Route low and medium risk tasks to `spec_programmer_fast`.",
            "Route high risk tasks to `spec_programmer_high`.",
            "Route critical risk tasks to `spec_programmer_high` and stop before any programmer handoff until a human checkpoint is explicitly recorded.",
            "High risk packages must include explicit architecture justification and exact allowlists before programmer handoff.",
            "If `RISK_LEVEL` is missing, stop and do not route.",
        ]:
            self.assertIn(token, manager_text)

        for text in [agents_text, skill_text]:
            self.assertIn("spec_programmer_fast", text)
            self.assertIn("spec_programmer_high", text)
            self.assertIn("Never run two write-capable agents concurrently", text)

        self.assertIn("PROGRAMMER_ROUTE", agents_text)
        self.assertIn("PROGRAMMER_ROUTE", skill_text)
        self.assertIn("critical", agents_text)
        self.assertIn("critical", skill_text)


if __name__ == "__main__":
    unittest.main()

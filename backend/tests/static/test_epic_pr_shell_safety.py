import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL_PATH = ROOT / ".agents" / "skills" / "speckit-epic-pr" / "SKILL.md"
AGENTS_PATH = ROOT / "AGENTS.md"
FORBIDDEN_FULL_DIFF = "git diff <base_branch>...<epic_branch>"
BOUNDED_COMMANDS = [
    "git --no-pager diff --name-only <base_branch>...<epic_branch>",
    "git --no-pager diff --stat <base_branch>...<epic_branch>",
    "git --no-pager log --oneline <base_branch>..<epic_branch>",
]


class EpicPrShellSafetyTests(unittest.TestCase):
    def test_skill_and_agents_do_not_require_full_epic_diff(self) -> None:
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        agents_text = AGENTS_PATH.read_text(encoding="utf-8")

        self.assertNotIn(FORBIDDEN_FULL_DIFF, skill_text)
        self.assertNotIn(FORBIDDEN_FULL_DIFF, agents_text)
        for command in BOUNDED_COMMANDS:
            self.assertIn(command, skill_text)


if __name__ == "__main__":
    unittest.main()

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
REAL_LOOKING_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]


class SecretHygieneTests(unittest.TestCase):
    def test_gitignore_excludes_private_env_and_agent_runtime_paths(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        for pattern in [
            ".env",
            ".env.*",
            ".agents/runs/",
            ".agents/logs/",
            ".agents/tmp/",
            ".agents/cache/",
            ".agents/secrets/",
        ]:
            self.assertIn(pattern, gitignore)

        self.assertIn("!.env.example", gitignore)

    def test_env_example_contains_placeholders_only(self) -> None:
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn("placeholder-only", env_example)
        for pattern in REAL_LOOKING_SECRET_PATTERNS:
            self.assertIsNone(pattern.search(env_example))

    def test_committed_config_has_no_real_looking_api_keys(self) -> None:
        paths = [
            ROOT / ".env.example",
            ROOT / "README.md",
            ROOT / "pyproject.toml",
            ROOT / "backend" / "requirements.txt",
        ]

        for path in paths:
            text = path.read_text(encoding="utf-8")
            for pattern in REAL_LOOKING_SECRET_PATTERNS:
                self.assertIsNone(pattern.search(text), f"Secret-like value in {path}")


if __name__ == "__main__":
    unittest.main()

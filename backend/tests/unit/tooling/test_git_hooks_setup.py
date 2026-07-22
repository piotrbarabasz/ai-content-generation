import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
HOOK_DIR = ROOT / ".githooks"
SCRIPTS_DIR = ROOT / "scripts"


class GitHooksSetupTests(unittest.TestCase):
    def test_hook_files_exist_and_call_runner(self) -> None:
        pre_commit = HOOK_DIR / "pre-commit"
        pre_push = HOOK_DIR / "pre-push"

        self.assertTrue(pre_commit.is_file())
        self.assertTrue(pre_push.is_file())
        self.assertEqual(
            pre_commit.read_text(encoding="utf-8").strip(),
            "#!/bin/sh\npython -m backend.app.tooling.git_hook_runner pre-commit",
        )
        self.assertEqual(
            pre_push.read_text(encoding="utf-8").strip(),
            "#!/bin/sh\npython -m backend.app.tooling.git_hook_runner pre-push",
        )

    def test_install_scripts_set_hooks_path_without_global(self) -> None:
        install_ps1 = SCRIPTS_DIR / "install-git-hooks.ps1"
        install_sh = SCRIPTS_DIR / "install-git-hooks.sh"

        self.assertTrue(install_ps1.is_file())
        self.assertTrue(install_sh.is_file())

        ps1_text = install_ps1.read_text(encoding="utf-8")
        sh_text = install_sh.read_text(encoding="utf-8")
        for text in (ps1_text, sh_text):
            self.assertIn("git config core.hooksPath .githooks", text)
            self.assertNotIn("--global", text)

    def test_setup_scripts_check_python_install_hooks_and_smoke_test_tooling(self) -> None:
        setup_ps1 = SCRIPTS_DIR / "setup-dev.ps1"
        setup_sh = SCRIPTS_DIR / "setup-dev.sh"

        self.assertTrue(setup_ps1.is_file())
        self.assertTrue(setup_sh.is_file())

        ps1_text = setup_ps1.read_text(encoding="utf-8")
        sh_text = setup_sh.read_text(encoding="utf-8")
        for text in (ps1_text, sh_text):
            self.assertIn("pip install -e .", text)
            self.assertIn("install-git-hooks", text)
            self.assertIn("pytest backend/tests/unit/tooling", text)
            self.assertIn("3.11", text)


if __name__ == "__main__":
    unittest.main()

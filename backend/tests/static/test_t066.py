import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOC_PATH = ROOT / "docs" / "tts" / "RUNTIME_PROFILES.md"
SETUP_PATH = ROOT / "scripts" / "setup-tts-runtime.ps1"
CHECK_PATH = ROOT / "scripts" / "check-tts-runtime.ps1"
DEMO_PATH = ROOT / "scripts" / "run-tts-demo.ps1"
GITIGNORE_PATH = ROOT / ".gitignore"


class T066RuntimeProfileTests(unittest.TestCase):
    def test_runtime_profile_doc_defines_the_expected_isolated_envs(self) -> None:
        doc = DOC_PATH.read_text(encoding="utf-8")

        for token in [
            ".venv-ci311",
            ".venv-tts311",
            ".venv-piper311",
            ".venv-xtts311",
            "setup-tts-runtime.ps1",
            "check-tts-runtime.ps1",
            "run-tts-demo.ps1",
            "Do not activate a profile",
            "agent.python",
            "Python 3.11",
            "torch",
            "torchaudio",
            "CUDA visibility",
        ]:
            self.assertIn(token, doc)

    def test_setup_script_uses_explicit_interpreters_and_checks_prerequisites(self) -> None:
        setup = SETUP_PATH.read_text(encoding="utf-8")

        for token in [
            "ProfileNames = @('tts311', 'piper311', 'xtts311')",
            "Get-ProfilePython",
            "Join-Path $RepoRoot \".venv-$ProfileName\"",
            "Scripts\\python.exe",
            "torch.__version__",
            "torchaudio.__version__",
            "torch.cuda.is_available()",
            "ChatterboxV3Provider",
            "ConvertTo-Json",
            "[switch]$RunSmoke",
        ]:
            self.assertIn(token, setup)

        for forbidden in [
            "git config --local agent.python",
            "git config --local core.hooksPath",
            "Activate.ps1",
            "hf_",
            "sk-",
            "password=",
            "token=",
        ]:
            self.assertNotIn(forbidden, setup)

    def test_health_check_script_is_both_human_and_machine_readable(self) -> None:
        check = CHECK_PATH.read_text(encoding="utf-8")

        for token in [
            "Write-Host 'TTS runtime health check'",
            "ValidateSet('all', 'tts311', 'piper311', 'xtts311')",
            "Get-ProfilePython",
            "Get-ProfileRoot",
            "Scripts\\python.exe",
            "ConvertTo-Json",
            "summary",
            "profiles",
            "ci311",
        ]:
            self.assertIn(token, check)

        self.assertNotIn("git config --local agent.python", check)

    def test_demo_script_uses_the_tts_profile_and_explicit_output_dir(self) -> None:
        demo = DEMO_PATH.read_text(encoding="utf-8")

        for token in [
            "ValidateSet('tts311')",
            ".venv-$Profile\\Scripts\\python.exe",
            ".runtime\\tts-demo",
            "app.tooling.tts_smoke",
            "chatterbox_v3",
        ]:
            self.assertIn(token, demo)

        self.assertNotIn("git config --local agent.python", demo)

    def test_gitignore_covers_generated_envs_and_local_reference_audio(self) -> None:
        gitignore = GITIGNORE_PATH.read_text(encoding="utf-8")

        for token in [
            ".venv-tts311/",
            ".venv-ci311/",
            ".venv-piper311/",
            ".venv-xtts311/",
            ".cache/huggingface/",
            "voice-references/",
            "speaker-references/",
            "reference-audio/",
            ".runtime/",
        ]:
            self.assertIn(token, gitignore)

        for secret_pattern in [r"sk-[A-Za-z0-9_-]{20,}", r"ghp_[A-Za-z0-9]{20,}"]:
            self.assertIsNone(re.search(secret_pattern, gitignore))


if __name__ == "__main__":
    unittest.main()

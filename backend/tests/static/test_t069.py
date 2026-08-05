from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = ROOT / "backend" / "app" / "providers" / "piper_catalog.py"
PROVIDER_PATH = ROOT / "backend" / "app" / "providers" / "piper_tts.py"
SETUP_DOC_PATH = ROOT / "docs" / "tts" / "PIPER_SETUP.md"
VOICES_DOC_PATH = ROOT / "docs" / "tts" / "PIPER_VOICES.md"
SETUP_SCRIPT_PATH = ROOT / "scripts" / "setup-piper-runtime.ps1"
CHECK_SCRIPT_PATH = ROOT / "scripts" / "check-piper-runtime.ps1"


class T069PiperCatalogStaticTests(unittest.TestCase):
    def test_required_piper_docs_and_scripts_exist(self) -> None:
        for path in [
            SETUP_DOC_PATH,
            VOICES_DOC_PATH,
            SETUP_SCRIPT_PATH,
            CHECK_SCRIPT_PATH,
        ]:
            self.assertTrue(path.is_file(), msg=f"Missing required Piper task file: {path}")

    def test_catalog_source_records_the_curated_polish_voice_inventory(self) -> None:
        catalog = CATALOG_PATH.read_text(encoding="utf-8")
        setup_doc = SETUP_DOC_PATH.read_text(encoding="utf-8")
        voices_doc = VOICES_DOC_PATH.read_text(encoding="utf-8")
        setup_script = SETUP_SCRIPT_PATH.read_text(encoding="utf-8")
        check_script = CHECK_SCRIPT_PATH.read_text(encoding="utf-8")

        for token in [
            "PIPER_VOICE_KEYS = (",
            '"pl_PL-bass-high"',
            '"pl_PL-darkman-medium"',
            '"pl_PL-gosia-medium"',
            '"pl_PL-mc_speech-medium"',
            '"pl_PL-mls_6892-low"',
            "source_repository=PIPER_VOICE_SOURCE_REPOSITORY",
            'source_revision="834f23262168a7e809179465e4113f23f5a7d1f7"',
            'source_revision="e9ef9dd"',
            'source_revision="441d4ac"',
            'source_revision="5227e41"',
            "download_urls",
            "model_card_url",
            "license_identifier",
            'engine_license_identifier="MIT"',
            'model_license_identifier="CC-BY-4.0"',
        ]:
            self.assertIn(token, catalog)

        for token in [
            "Piper is a local, human-operated runtime",
            ".venv-piper311",
            "engine license",
            "voice-model licenses",
            "scripts\\setup-piper-runtime.ps1 -VoiceKey pl_PL-gosia-medium",
            "scripts\\check-piper-runtime.ps1",
            "verifies each downloaded checksum",
        ]:
            self.assertIn(token, setup_doc)

        for token in [
            "pl_PL-bass-high",
            "pl_PL-darkman-medium",
            "pl_PL-gosia-medium",
            "pl_PL-mc_speech-medium",
            "pl_PL-mls_6892-low",
            "Source repository: `rhasspy/piper-voices`",
            "Engine license identifier: `MIT`",
            "Model license: `CC-BY-4.0`",
        ]:
            self.assertIn(token, voices_doc)

        for token in [
            ".venv-piper311\\Scripts\\python.exe",
            "Get-FileHash",
            "Checksum mismatch",
            "Piper runtime health check",
            "ConvertTo-Json",
            "runtime_root",
            "source_revision",
        ]:
            self.assertIn(token, setup_script + check_script)

        for forbidden in [
            "Activate.ps1",
            "D:\\\\",
            "C:\\\\",
            "/Users/",
            "C:/Users/",
        ]:
            self.assertNotIn(forbidden, setup_doc + voices_doc + setup_script + check_script + catalog)

        for forbidden in [
            "agent.python",
            "git config",
        ]:
            self.assertNotIn(forbidden, setup_script + check_script)

    def test_provider_source_uses_the_catalog_for_effective_identity(self) -> None:
        provider = PROVIDER_PATH.read_text(encoding="utf-8")

        for token in [
            "get_piper_voice_catalog_entry",
            "catalog_identity",
            "_catalog_identity(",
            'kind": "catalog_voice"',
            "Unknown Piper voice",
        ]:
            self.assertIn(token, provider)

    def test_repository_does_not_commit_piper_model_binaries(self) -> None:
        model_files: list[Path] = []
        for base in [ROOT / "backend", ROOT / "docs", ROOT / "scripts", ROOT / "specs"]:
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if path.is_file() and (path.name.endswith(".onnx") or path.name.endswith(".onnx.json")):
                    model_files.append(path)

        self.assertEqual(model_files, [], msg=f"Unexpected committed Piper model files: {model_files}")


if __name__ == "__main__":
    unittest.main()

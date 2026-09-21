"""D025 evidence aggregation remains truthful about external release gates."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "packaging/d025/acceptance.py"
spec = importlib.util.spec_from_file_location("d025_acceptance", MODULE)
acceptance = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(acceptance)


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _junit(path: Path, *, omit: str | None = None) -> Path:
    suite = ET.Element("testsuite")
    for name in acceptance.REQUIRED_TESTS:
        if name != omit:
            ET.SubElement(suite, "testcase", name=name)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _evidence(tmp_path: Path, *, signed=False, language="pl") -> dict:
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"installer")
    runtime, models = tmp_path / "runtime", tmp_path / "models"
    runtime.mkdir()
    models.mkdir()
    (runtime / "runtime.zip").write_bytes(b"runtime")
    (models / "voice.onnx").write_bytes(b"voice")
    installed = _write(tmp_path / "installer.json", {
        "automated_pass": True,
        "project_preserved_after_uninstall": True,
        "installed_smoke": {"signing": {
            "status": "signed" if signed else "unsigned", "release_eligible": signed,
        }},
    })
    audio = _write(tmp_path / "audio.json", {
        "automated_pass": True, "worker_exit": 0, "reopen_pass": True,
        "duration_seconds": 1.25, "section_audio": {"sample_rate": 22050},
        "identity": {"runtime": {"profile": "profile"}, "synthesis": {
            "provider": "piper", "model_variant": "pl_PL-gosia-medium", "language_id": language,
            "voice": {"catalog": {"language_id": "pl_PL"}},
        }},
    })
    preview = _write(tmp_path / "preview.json", {
        "automated_pass": True,
        "render": {"fully_decoded": True, "video_codec": "h264", "audio_codec": "aac",
                   "video_duration": [25, 25]},
        "packaged_qt": {"automated_pass": True, "results": {"pattern.mp4": {"video_frames": 25}}},
    })
    return {"installer": installer, "installer_report": installed, "audio_report": audio,
            "preview_report": preview, "junit_report": _junit(tmp_path / "tests.xml"),
            "runtime_cache": runtime, "model_root": models}


def test_automated_evidence_passes_but_unsigned_manual_pending_blocks_release(tmp_path):
    report = acceptance.assemble(**_evidence(tmp_path))
    assert report["automated_pass"] is True
    assert report["release_pass"] is False
    assert len(report["steps"]) == 16
    assert report["voice"]["catalog_language"] == "pl_PL"
    assert len(report["blockers"]) == 2


def test_signed_clean_windows_manual_evidence_closes_release_gate(tmp_path):
    evidence = _evidence(tmp_path, signed=True)
    manual = {field: "pass" for field in acceptance.MANUAL_FIELDS}
    manual.update({field: "recorded" for field in acceptance.MANUAL_METADATA})
    manual["installer_sha256"] = acceptance._sha256(evidence["installer"])
    evidence["manual_report"] = _write(tmp_path / "manual.json", manual)
    report = acceptance.assemble(**evidence)
    assert report["release_pass"] is True
    assert report["blockers"] == []


def test_manual_evidence_for_another_installer_does_not_close_release_gate(tmp_path):
    evidence = _evidence(tmp_path, signed=True)
    manual = {field: "pass" for field in acceptance.MANUAL_FIELDS}
    manual.update({field: "recorded" for field in acceptance.MANUAL_METADATA})
    manual["installer_sha256"] = "0" * 64
    evidence["manual_report"] = _write(tmp_path / "manual.json", manual)
    report = acceptance.assemble(**evidence)
    assert report["release_pass"] is False
    assert len(report["blockers"]) == 1


def test_missing_behavior_or_wrong_voice_language_is_rejected(tmp_path):
    evidence = _evidence(tmp_path, language="en")
    with pytest.raises(ValueError, match="Polish"):
        acceptance.assemble(**evidence)


def test_missing_required_behavioral_case_is_rejected(tmp_path):
    evidence = _evidence(tmp_path)
    missing = next(iter(acceptance.REQUIRED_TESTS))
    evidence["junit_report"] = _junit(tmp_path / "incomplete.xml", omit=missing)
    with pytest.raises(ValueError, match="missing passing cases"):
        acceptance.assemble(**evidence)

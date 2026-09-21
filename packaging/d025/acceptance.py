"""Assemble truthful D025 evidence from existing packaged and behavioral checks."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import file_digest
import json
from pathlib import Path
import platform
import xml.etree.ElementTree as ET


REQUIRED_TESTS = {
    "test_create_edit_split_merge_reorder_and_reopen",
    "test_mock_consumes_known_schema_deterministically_and_returns_editable_content[False]",
    "test_real_D012_and_D011_audio_retime_retained_plan_and_reopen_without_changing_visuals",
    "test_editing_one_prompt_does_not_touch_other_scene_or_tts",
    "test_import_generation_and_variant_selection_are_scene_local",
    "test_commands_display_persisted_order_offsets_and_reject_bad_range",
    "test_proxy_cache_uses_lower_profile_and_image_change_invalidates_without_tts",
    "test_real_ffmpeg_mp4_probe_full_decode_and_immutable_publication[fit]",
    "test_edit_b_rebuilds_only_b_and_preserves_a_c_checksums",
    "test_restart_recovers_interrupted_exact_job_and_reuses_completed_sections",
    "test_proxy_and_final_use_pinned_timeline_and_reuse_without_other_generation",
}

MANUAL_FIELDS = {
    "clean_windows",
    "installed_app_launch",
    "wav_audible",
    "mp4_picture_and_sound",
    "private_worker",
    "real_output_playback",
    "uninstall_project_preserved",
}

MANUAL_METADATA = {
    "tester",
    "tested_at",
    "windows_edition_build",
    "machine_or_vm",
    "audio_device",
}


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Evidence must be a JSON object: {path.name}")
    return value


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def _passed_tests(path: Path) -> set[str]:
    root = ET.parse(path).getroot()
    passed = set()
    for case in root.iter("testcase"):
        if not any(case.find(kind) is not None for kind in ("failure", "error", "skipped")):
            name = case.get("name")
            if name:
                passed.add(name)
    missing = REQUIRED_TESTS - passed
    if missing:
        raise ValueError("D025 JUnit evidence is missing passing cases: " + ", ".join(sorted(missing)))
    return passed


def _tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def assemble(*, installer: Path, installer_report: Path, audio_report: Path,
             preview_report: Path, junit_report: Path, runtime_cache: Path,
             model_root: Path, manual_report: Path | None = None) -> dict:
    installer = installer.resolve(strict=True)
    installed = _json(installer_report)
    audio = _json(audio_report)
    preview = _json(preview_report)
    passed = _passed_tests(junit_report)
    if not installed.get("automated_pass") or not installed.get("project_preserved_after_uninstall"):
        raise ValueError("D041 installer lifecycle evidence did not pass.")
    identity = audio.get("identity", {}).get("synthesis", {})
    catalog = identity.get("voice", {}).get("catalog", {})
    if (not audio.get("automated_pass") or audio.get("worker_exit") != 0
            or not audio.get("reopen_pass") or identity.get("language_id") != "pl"
            or catalog.get("language_id") != "pl_PL"
            or identity.get("model_variant") != "pl_PL-gosia-medium"):
        raise ValueError("Real Piper evidence does not prove the selected Polish profile.")
    render = preview.get("render", {})
    packaged_qt = preview.get("packaged_qt", {})
    if (not preview.get("automated_pass") or not render.get("fully_decoded")
            or not packaged_qt.get("automated_pass")):
        raise ValueError("Packaged proxy/render evidence did not pass.")

    steps = [
        (1, "Launch installed application without system Python", "installer lifecycle"),
        (2, "Create a project", "project editor integration"),
        (3, "Enter own text", "project editor integration"),
        (4, "Obtain editable structured sections", "structured-script integration"),
        (5, "Edit, split, merge and reorder sections", "project editor integration"),
        (6, "Generate real local audio with the selected supported profile", "real Piper smoke"),
        (7, "Create scenes from text and measured audio boundaries", "scene-plan integration"),
        (8, "Edit independently persisted visual prompts", "scene editor integration"),
        (9, "Import or generate selected images", "scene editor integration"),
        (10, "Inspect Timeline Lite and proxy preview", "timeline/preview integration"),
        (11, "Generate a fully decoded MP4 with narration and visuals", "real FFmpeg integration"),
        (12, "Close the application", "installer lifecycle"),
        (13, "Reopen project media and selections", "project/audio reopen integration"),
        (14, "Edit only section B", "selective-regeneration integration"),
        (15, "Recompute B dependencies while preserving A/C", "selective-regeneration integration"),
        (16, "Encode a new version without regenerating the whole project", "pinned timeline/render integration"),
    ]
    installer_sha256 = _sha256(installer)
    manual = _json(manual_report) if manual_report else {}
    manual_checks_pass = bool(manual) and all(
        manual.get(field) == "pass" for field in MANUAL_FIELDS
    )
    manual_metadata_complete = all(
        isinstance(manual.get(field), str) and bool(manual[field].strip())
        for field in MANUAL_METADATA
    )
    manual_installer_matches = manual.get("installer_sha256") == installer_sha256
    manual_pass = manual_checks_pass and manual_metadata_complete and manual_installer_matches
    signing = installed.get("installed_smoke", {}).get("signing", {})
    signed = signing.get("release_eligible") is True and signing.get("status") == "signed"
    blockers = []
    if not manual_pass:
        blockers.append("clean-Windows manual launch/audio/video/private-worker/uninstall evidence is incomplete")
    if not signed:
        blockers.append("the tested installer is unsigned and not release-eligible")
    video_duration = render.get("video_duration", [0, 1])
    report = {
        "schema_version": 1,
        "task": "D025",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "automated_pass": True,
        "release_pass": not blockers,
        "steps": [
            {"number": number, "description": description, "automated_status": "pass", "evidence": evidence}
            for number, description, evidence in steps
        ],
        "required_tests": sorted(passed & REQUIRED_TESTS),
        "voice": {
            "provider": identity.get("provider"),
            "model": identity.get("model_variant"),
            "requested_language": identity.get("language_id"),
            "catalog_language": catalog.get("language_id"),
            "audio_duration_seconds": audio.get("duration_seconds"),
            "sample_rate": audio.get("section_audio", {}).get("sample_rate"),
            "worker_exit": audio.get("worker_exit"),
            "runtime_profile": audio.get("identity", {}).get("runtime", {}).get("profile"),
        },
        "video": {
            "fully_decoded": render.get("fully_decoded"),
            "codec": render.get("video_codec"),
            "audio_codec": render.get("audio_codec"),
            "duration_seconds": video_duration[0] / video_duration[1],
            "packaged_qt_frames": packaged_qt.get("results", {}).get("pattern.mp4", {}).get("video_frames"),
        },
        "release": {
            "installer_name": installer.name,
            "installer_size_bytes": installer.stat().st_size,
            "installer_sha256": installer_sha256,
            "signing": signing,
            "runtime_download_bytes": _tree_bytes(runtime_cache.resolve(strict=True)),
            "voice_download_bytes": _tree_bytes(model_root.resolve(strict=True)),
        },
        "machine": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "python_used_by_harness": platform.python_version(),
            "clean_windows_attested": manual.get("clean_windows") == "pass",
        },
        "manual": manual,
        "blockers": blockers,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("installer", "installer-report", "audio-report", "preview-report", "junit-report",
                 "runtime-cache", "model-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--manual-report", type=Path)
    parser.add_argument("--require-release-pass", action="store_true")
    args = parser.parse_args()
    report = assemble(installer=args.installer, installer_report=args.installer_report,
                      audio_report=args.audio_report, preview_report=args.preview_report,
                      junit_report=args.junit_report, runtime_cache=args.runtime_cache,
                      model_root=args.model_root, manual_report=args.manual_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("automated_pass", "release_pass", "blockers")}, indent=2))
    if args.require_release_pass and not report["release_pass"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

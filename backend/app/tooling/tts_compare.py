"""Sequential manual comparison runner for TTS provider smoke checks."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.providers.tts_settings import TTSSettings, TTSSettingsError

from . import tts_smoke


class TTSComparisonError(RuntimeError):
    """Raised when the comparison harness cannot prepare or persist outputs."""


@dataclass(frozen=True, slots=True)
class ComparisonProfile:
    """A single provider profile in the comparison manifest."""

    profile_id: str
    label: str
    provider: str
    settings: dict[str, Any]


def default_profiles() -> tuple[ComparisonProfile, ...]:
    """Return the curated default comparison set."""

    return (
        ComparisonProfile(
            profile_id="chatterbox-neutral",
            label="Chatterbox neutral",
            provider="chatterbox_v3",
            settings={"language_id": "pl"},
        ),
        ComparisonProfile(
            profile_id="piper-pl_PL-bass-high",
            label="Piper pl_PL-bass-high",
            provider="piper",
            settings={"language_id": "pl", "model_key": "pl_PL-bass-high"},
        ),
        ComparisonProfile(
            profile_id="piper-pl_PL-darkman-medium",
            label="Piper pl_PL-darkman-medium",
            provider="piper",
            settings={"language_id": "pl", "model_key": "pl_PL-darkman-medium"},
        ),
        ComparisonProfile(
            profile_id="piper-pl_PL-gosia-medium",
            label="Piper pl_PL-gosia-medium",
            provider="piper",
            settings={"language_id": "pl", "model_key": "pl_PL-gosia-medium"},
        ),
        ComparisonProfile(
            profile_id="piper-pl_PL-mc_speech-medium",
            label="Piper pl_PL-mc_speech-medium",
            provider="piper",
            settings={"language_id": "pl", "model_key": "pl_PL-mc_speech-medium"},
        ),
        ComparisonProfile(
            profile_id="piper-pl_PL-mls_6892-low",
            label="Piper pl_PL-mls_6892-low",
            provider="piper",
            settings={"language_id": "pl", "model_key": "pl_PL-mls_6892-low"},
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the parser without importing optional provider dependencies."""

    parser = argparse.ArgumentParser(description=__doc__)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--text", help="Narration text to compare.")
    input_group.add_argument(
        "--input-text-file", type=Path, help="UTF-8 narration text file to compare."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for comparison outputs (default: .runtime/tts-comparison/<timestamp>).",
    )
    parser.add_argument(
        "--profile",
        action="append",
        dest="profiles",
        help="Restrict the comparison to a curated profile id. May be repeated.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Permit replacing existing outputs.")
    return parser


def _normalize_text(text: str) -> str:
    normalized = " ".join(text.split())
    if not normalized:
        raise TTSComparisonError("Comparison text must not be empty.")
    return normalized


def _read_text(args: argparse.Namespace) -> str:
    try:
        return tts_smoke._read_text(args)
    except tts_smoke.TTSSmokeError as exc:
        raise TTSComparisonError(str(exc)) from exc


def _select_profiles(selected: list[str] | None) -> tuple[ComparisonProfile, ...]:
    curated = {profile.profile_id: profile for profile in default_profiles()}
    if not selected:
        return default_profiles()
    profiles: list[ComparisonProfile] = []
    for profile_id in selected:
        try:
            profiles.append(curated[profile_id])
        except KeyError as exc:
            raise TTSComparisonError(f"Unknown comparison profile: {profile_id}.") from exc
    return tuple(profiles)


def _validate_profile(profile: ComparisonProfile) -> TTSSettings:
    try:
        return TTSSettings.from_mapping(profile.settings, provider=profile.provider)
    except TTSSettingsError as exc:
        raise TTSComparisonError(str(exc)) from exc


def _profile_directories(base_output_dir: Path, profile: ComparisonProfile) -> tuple[Path, Path, Path]:
    profile_dir = base_output_dir / "profiles" / profile.profile_id
    output_path = profile_dir / "speech.wav"
    report_path = profile_dir / "report.json"
    return profile_dir, output_path, report_path


def _build_smoke_args(
    *,
    normalized_text: str,
    output_path: Path,
    report_path: Path,
    settings: TTSSettings,
    overwrite: bool,
) -> argparse.Namespace:
    return argparse.Namespace(
        provider=settings.provider,
        text=normalized_text,
        input_text_file=None,
        output=output_path,
        report=report_path,
        language=settings.language_id or "pl",
        device=settings.device,
        audio_prompt=settings.audio_prompt_path,
        model_variant=settings.model_variant,
        overwrite=overwrite,
        model_key=settings.model_key,
        model_path=settings.model_path,
        exaggeration=settings.exaggeration,
        cfg_weight=settings.cfg_weight,
        temperature=settings.temperature,
        repetition_penalty=settings.repetition_penalty,
        min_p=settings.min_p,
        top_p=settings.top_p,
        length_scale=settings.length_scale,
        volume=settings.volume,
        noise_scale=settings.noise_scale,
        noise_w_scale=settings.noise_w_scale,
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Run the curated profiles sequentially and write a summary plus playlist."""

    normalized_text = _normalize_text(_read_text(args))
    base_output_dir = args.output_dir
    if base_output_dir is None:
        base_output_dir = Path(".runtime") / "tts-comparison" / time.strftime("%Y%m%d-%H%M%S")
    base_output_dir = Path(base_output_dir)
    summary_path = base_output_dir / "summary.json"
    playlist_path = base_output_dir / "playlist.m3u8"

    base_output_dir.mkdir(parents=True, exist_ok=True)

    profiles = _select_profiles(getattr(args, "profiles", None))
    profile_reports: list[dict[str, Any]] = []
    playlist_entries: list[tuple[str, str]] = []

    for profile in profiles:
        settings = _validate_profile(profile)
        profile_dir, output_path, report_path = _profile_directories(base_output_dir, profile)
        profile_dir.mkdir(parents=True, exist_ok=True)
        smoke_args = _build_smoke_args(
            normalized_text=normalized_text,
            output_path=output_path,
            report_path=report_path,
            settings=settings,
            overwrite=bool(getattr(args, "overwrite", False)),
        )
        started = time.perf_counter()
        try:
            report = tts_smoke.run(smoke_args)
            elapsed = time.perf_counter() - started
            profile_report: dict[str, Any] = {
                "profile_id": profile.profile_id,
                "label": profile.label,
                "provider": settings.provider,
                "status": "completed",
                "reason": None,
                "normalized_text": normalized_text,
                "output_wav": str(output_path),
                "report_path": str(report_path),
                "generation_seconds": elapsed,
                "report": report,
            }
            playlist_entries.append((profile.label, str(output_path)))
        except Exception as exc:
            profile_report = {
                "profile_id": profile.profile_id,
                "label": profile.label,
                "provider": settings.provider,
                "status": "failed",
                "reason": str(exc),
                "normalized_text": normalized_text,
                "output_wav": str(output_path),
                "report_path": str(report_path),
                "generation_seconds": time.perf_counter() - started,
            }
            _write_json(report_path, profile_report)
        profile_reports.append(profile_report)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "normalized_text": normalized_text,
        "output_dir": str(base_output_dir),
        "summary": {
            "profile_count": len(profile_reports),
            "completed_count": sum(1 for item in profile_reports if item["status"] == "completed"),
            "failed_count": sum(1 for item in profile_reports if item["status"] == "failed"),
        },
        "profiles": profile_reports,
        "playlist_path": str(playlist_path),
        "summary_path": str(summary_path),
    }

    _write_json(summary_path, summary)
    playlist_path.parent.mkdir(parents=True, exist_ok=True)
    playlist_lines = ["#EXTM3U"]
    for label, path in playlist_entries:
        playlist_lines.append(f"#EXTINF:-1,{label}")
        playlist_lines.append(path)
    playlist_path.write_text("\n".join(playlist_lines) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = run(args)
    except (TTSComparisonError, OSError, ValueError, RuntimeError) as exc:
        print(f"tts comparison failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a CLI.
    raise SystemExit(main())

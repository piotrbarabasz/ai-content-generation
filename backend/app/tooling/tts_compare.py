"""Sequential manual comparison runner for TTS provider smoke checks."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.tts_capabilities import TTSCapabilityError
from app.providers.tts_factory import build_tts_provider
from app.providers.tts_settings import TTSSettings, TTSSettingsError
from app.tts.assembly import WavAssemblyError, persist_pcm_wav_atomically
from app.tts.manifest import sanitize_synthesis_identity

from . import tts_smoke


class TTSComparisonError(RuntimeError):
    """Raised when the comparison harness cannot prepare or persist outputs."""


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_MANIFEST_PATH = _REPO_ROOT / "docs" / "tts" / "PROVIDER_COMPARISON.md"
_DEFAULT_INPUT_TEXT_FILE = _REPO_ROOT / "backend" / "tests" / "fixtures" / "narrations" / "story_01_1min.txt"
_COMPARISON_OUTPUT_SUBDIR = Path(".runtime") / "tts-comparison"


@dataclass(frozen=True, slots=True)
class ComparisonProfile:
    """One comparison manifest profile."""

    profile_id: str
    label: str
    provider: str
    settings: dict[str, Any]
    enabled_by_default: bool = True
    requires_approved_reference: bool = False


@dataclass(frozen=True, slots=True)
class ComparisonManifest:
    """Parsed manifest data for comparison runs."""

    source_path: Path
    default_input_text_file: Path
    seed: int | None
    profiles: tuple[ComparisonProfile, ...]
    scoring_template: tuple[str, ...]

    def default_profiles(self) -> tuple[ComparisonProfile, ...]:
        return tuple(profile for profile in self.profiles if profile.enabled_by_default)

    def select_profiles(self, selected: list[str] | None) -> tuple[ComparisonProfile, ...]:
        if not selected:
            return self.profiles
        profile_by_id = {profile.profile_id: profile for profile in self.profiles}
        profiles: list[ComparisonProfile] = []
        seen: set[str] = set()
        for profile_id in selected:
            if profile_id in seen:
                raise TTSComparisonError(f"Duplicate comparison profile selection: {profile_id}.")
            seen.add(profile_id)
            try:
                profiles.append(profile_by_id[profile_id])
            except KeyError as exc:
                raise TTSComparisonError(f"Unknown comparison profile: {profile_id}.") from exc
        return tuple(profiles)


def _default_manifest_path() -> Path:
    return _DEFAULT_MANIFEST_PATH


def _display_repo_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix() if not path.is_absolute() else path.name


def _relative_output_path(path: Path, *, base_output_dir: Path) -> str:
    return path.resolve().relative_to(base_output_dir.resolve()).as_posix()


def _extract_json_block(document: str, *, source_path: Path) -> str:
    marker = "```json"
    start = document.find(marker)
    if start < 0:
        raise TTSComparisonError(f"Comparison manifest is missing a JSON code block: {source_path.name}.")
    block = document[start + len(marker) :]
    end = block.find("```")
    if end < 0:
        raise TTSComparisonError(f"Comparison manifest JSON block is not terminated: {source_path.name}.")
    return block[:end].strip()


def _coerce_string(value: Any, *, field_name: str, source_path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TTSComparisonError(
            f"Comparison manifest field '{field_name}' must be a non-empty string: {source_path.name}."
        )
    return value.strip()


def _coerce_bool(value: Any, *, field_name: str, default: bool, source_path: Path) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise TTSComparisonError(
            f"Comparison manifest field '{field_name}' must be a boolean: {source_path.name}."
        )
    return value


def _load_manifest_payload(path: Path) -> dict[str, Any]:
    try:
        document = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TTSComparisonError(f"Cannot read comparison manifest: {path.name}.") from exc
    try:
        payload = json.loads(_extract_json_block(document, source_path=path))
    except json.JSONDecodeError as exc:
        raise TTSComparisonError(f"Comparison manifest JSON is invalid: {path.name}.") from exc
    if not isinstance(payload, dict):
        raise TTSComparisonError(f"Comparison manifest root must be an object: {path.name}.")
    return payload


def _parse_manifest_profile(item: Any, *, source_path: Path) -> ComparisonProfile:
    if not isinstance(item, dict):
        raise TTSComparisonError(
            f"Comparison manifest profiles must be objects: {source_path.name}."
        )
    profile_id = _coerce_string(item.get("profile_id"), field_name="profile_id", source_path=source_path)
    label = _coerce_string(item.get("label"), field_name="label", source_path=source_path)
    provider = _coerce_string(item.get("provider"), field_name="provider", source_path=source_path)
    settings = item.get("settings")
    if not isinstance(settings, dict):
        raise TTSComparisonError(
            f"Comparison manifest profile '{profile_id}' must define an object settings field: {source_path.name}."
        )
    return ComparisonProfile(
        profile_id=profile_id,
        label=label,
        provider=provider,
        settings=dict(settings),
        enabled_by_default=_coerce_bool(
            item.get("enabled_by_default"),
            field_name="enabled_by_default",
            default=True,
            source_path=source_path,
        ),
        requires_approved_reference=_coerce_bool(
            item.get("requires_approved_reference"),
            field_name="requires_approved_reference",
            default=False,
            source_path=source_path,
        ),
    )


def load_comparison_manifest(path: Path | None = None) -> ComparisonManifest:
    """Load the Markdown manifest that describes the curated comparison set."""

    manifest_path = Path(path) if path is not None else _default_manifest_path()
    payload = _load_manifest_payload(manifest_path)
    version = payload.get("version")
    if version != 1:
        raise TTSComparisonError(
            f"Comparison manifest version must be 1: {manifest_path.name}."
        )

    default_input_text_file_value = payload.get("default_input_text_file")
    if default_input_text_file_value is None:
        default_input_text_file = _DEFAULT_INPUT_TEXT_FILE
    else:
        default_input_text_file = Path(
            _coerce_string(
                default_input_text_file_value,
                field_name="default_input_text_file",
                source_path=manifest_path,
            )
        )
        if not default_input_text_file.is_absolute():
            default_input_text_file = (manifest_path.parent / default_input_text_file).resolve()

    seed_value = payload.get("seed")
    if seed_value is None:
        seed = None
    elif isinstance(seed_value, bool) or not isinstance(seed_value, int):
        raise TTSComparisonError(
            f"Comparison manifest seed must be an integer: {manifest_path.name}."
        )
    else:
        seed = seed_value

    profiles_value = payload.get("profiles")
    if not isinstance(profiles_value, list) or not profiles_value:
        raise TTSComparisonError(
            f"Comparison manifest must define a non-empty profiles array: {manifest_path.name}."
        )
    profiles: list[ComparisonProfile] = []
    seen_profile_ids: set[str] = set()
    for item in profiles_value:
        profile = _parse_manifest_profile(item, source_path=manifest_path)
        if profile.profile_id in seen_profile_ids:
            raise TTSComparisonError(
                f"Comparison manifest contains a duplicate profile id: {profile.profile_id}."
            )
        seen_profile_ids.add(profile.profile_id)
        profiles.append(profile)

    scoring_template_value = payload.get("scoring_template", [])
    if not isinstance(scoring_template_value, list):
        raise TTSComparisonError(
            f"Comparison manifest scoring_template must be an array: {manifest_path.name}."
        )
    scoring_template: list[str] = []
    for item in scoring_template_value:
        scoring_template.append(
            _coerce_string(item, field_name="scoring_template item", source_path=manifest_path)
        )

    return ComparisonManifest(
        source_path=manifest_path,
        default_input_text_file=default_input_text_file,
        seed=seed,
        profiles=tuple(profiles),
        scoring_template=tuple(scoring_template),
    )


def default_profiles() -> tuple[ComparisonProfile, ...]:
    """Return the default curated comparison set without optional evaluation-only profiles."""

    manifest = load_comparison_manifest()
    return tuple(profile for profile in manifest.profiles if profile.enabled_by_default)


def build_parser() -> argparse.ArgumentParser:
    """Build the parser without importing optional provider dependencies."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="Path to the comparison manifest markdown file.")
    input_group = parser.add_mutually_exclusive_group(required=False)
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
    parser.add_argument("--approved-reference", type=Path, help="Approved XTTS reference WAV.")
    parser.add_argument("--overwrite", action="store_true", help="Permit replacing existing outputs.")
    return parser


def _normalize_text(text: str) -> str:
    normalized = " ".join(text.split())
    if not normalized:
        raise TTSComparisonError("Comparison text must not be empty.")
    return normalized


def _read_text(args: argparse.Namespace, *, fallback_input_text_file: Path | None = None) -> str:
    if getattr(args, "text", None) is not None:
        return args.text
    input_text_file = getattr(args, "input_text_file", None) or fallback_input_text_file
    if input_text_file is None:
        raise TTSComparisonError("Comparison text must be supplied with --text or --input-text-file.")
    try:
        return Path(input_text_file).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TTSComparisonError(
            f"Cannot read UTF-8 comparison text file: {Path(input_text_file).name}."
        ) from exc


def _sanitize_reason(
    message: str,
    *,
    manifest_path: Path | None = None,
    input_text_file: Path | None = None,
    approved_reference: Path | None = None,
) -> str:
    scrubbed = message
    for path in (manifest_path, input_text_file, approved_reference):
        if path is None:
            continue
        resolved = str(Path(path).resolve())
        scrubbed = scrubbed.replace(resolved, Path(path).name)
    return scrubbed


def _settings_payload(settings: TTSSettings) -> dict[str, Any]:
    return {
        "provider": settings.provider,
        "usage_policy": settings.usage_policy,
        "device": settings.device,
        "language_id": settings.language_id,
        "model_variant": settings.model_variant,
        "audio_prompt_path": settings.audio_prompt_path,
        "reference_audio_path": settings.reference_audio_path,
        "approved_label": settings.approved_label,
        "model_key": settings.model_key,
        "model_path": settings.model_path,
        "length_scale": settings.length_scale,
        "volume": settings.volume,
        "noise_scale": settings.noise_scale,
        "noise_w_scale": settings.noise_w_scale,
        "exaggeration": settings.exaggeration,
        "cfg_weight": settings.cfg_weight,
        "temperature": settings.temperature,
        "repetition_penalty": settings.repetition_penalty,
        "min_p": settings.min_p,
        "top_p": settings.top_p,
    }


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


def _build_provider_settings(
    profile: ComparisonProfile,
    *,
    approved_reference: Path | None,
) -> dict[str, Any]:
    settings = dict(profile.settings)
    if profile.requires_approved_reference:
        settings["reference_audio_path"] = approved_reference
    return settings


def _build_provider(profile: ComparisonProfile, *, approved_reference: Path | None) -> tuple[TTSSettings, Any]:
    settings = TTSSettings.from_mapping(
        _build_provider_settings(profile, approved_reference=approved_reference),
        provider=profile.provider,
    )
    provider_config = ProviderConfig.create(
        workflow_config_id="tts_compare",
        provider_type=ProviderType.TTS,
        provider_name=settings.provider,
        settings=_settings_payload(settings),
    )
    provider = build_tts_provider(provider_config)
    return settings, provider


def _comparison_voice_config(settings: TTSSettings, seed: int | None) -> dict[str, Any]:
    voice_config: dict[str, Any] = {"language_id": settings.language_id or "pl"}
    if seed is not None:
        voice_config["seed"] = seed
    return voice_config


def _profile_directories(base_output_dir: Path, profile: ComparisonProfile) -> tuple[Path, Path, Path]:
    profile_dir = base_output_dir / "profiles" / profile.profile_id
    output_path = profile_dir / "speech.wav"
    report_path = profile_dir / "report.json"
    return profile_dir, output_path, report_path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _profile_summary(
    profile: ComparisonProfile,
    *,
    status: str,
    reason: str | None,
    normalized_text: str,
    output_wav: Path,
    report_path: Path,
    base_output_dir: Path,
    seed: int | None,
    effective_identity: dict[str, Any] | None = None,
    generation_wall_time_seconds: float | None = None,
    audio_duration_seconds: float | None = None,
    real_time_factor: float | None = None,
    pcm_parameters: dict[str, Any] | None = None,
    checksum_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "profile_id": profile.profile_id,
        "label": profile.label,
        "provider": profile.provider,
        "status": status,
        "reason": reason,
        "normalized_text": normalized_text,
        "seed": seed,
        "effective_synthesis_identity": effective_identity,
        "generation_wall_time_seconds": generation_wall_time_seconds,
        "audio_duration_seconds": audio_duration_seconds,
        "real_time_factor": real_time_factor,
        "pcm_parameters": pcm_parameters,
        "checksum_sha256": checksum_sha256,
        "output_wav": _relative_output_path(output_wav, base_output_dir=base_output_dir),
        "report_path": _relative_output_path(report_path, base_output_dir=base_output_dir),
    }


def _run_direct_profile(
    profile: ComparisonProfile,
    *,
    normalized_text: str,
    seed: int | None,
    approved_reference: Path | None,
    base_output_dir: Path,
    overwrite: bool,
) -> dict[str, Any]:
    profile_dir, output_path, report_path = _profile_directories(base_output_dir, profile)
    profile_dir.mkdir(parents=True, exist_ok=True)
    existing = [path for path in (output_path, report_path) if path.exists()]
    if existing and not overwrite:
        raise TTSComparisonError(
            "Refusing to overwrite existing profile outputs; pass --overwrite: "
            + ", ".join(path.name for path in existing)
        )

    settings: TTSSettings | None = None
    provider: Any | None = None
    effective_identity: dict[str, Any] | None = None
    started = time.perf_counter()
    try:
        settings, provider = _build_provider(profile, approved_reference=approved_reference)
        voice_config = _comparison_voice_config(settings, seed)
        effective_identity = sanitize_synthesis_identity(provider.effective_synthesis_identity(voice_config))
        result = provider.synthesize(normalized_text, voice_config)
        if result.audio_format != "wav":
            raise TTSComparisonError("Provider did not return WAV audio.")
        assembly = persist_pcm_wav_atomically(result.audio_bytes, output_path)
    except (TTSCapabilityError, TTSComparisonError, TTSSettingsError, WavAssemblyError, OSError, ValueError, RuntimeError) as exc:
        generation_wall_time_seconds = round(time.perf_counter() - started, 6)
        reason = _sanitize_reason(
            str(exc),
            approved_reference=approved_reference,
            input_text_file=None,
        )
        report = _profile_summary(
            profile,
            status="failed",
            reason=reason,
            normalized_text=normalized_text,
            output_wav=output_path,
            report_path=report_path,
            base_output_dir=base_output_dir,
            seed=seed,
            effective_identity=effective_identity,
            generation_wall_time_seconds=generation_wall_time_seconds,
        )
        _write_json(report_path, report)
        return report

    generation_wall_time_seconds = round(time.perf_counter() - started, 6)
    audio_duration_seconds = round(assembly.duration_seconds, 6)
    real_time_factor = round(generation_wall_time_seconds / audio_duration_seconds, 6) if audio_duration_seconds > 0 else None
    report = _profile_summary(
        profile,
        status="completed",
        reason=None,
        normalized_text=normalized_text,
        output_wav=output_path,
        report_path=report_path,
        base_output_dir=base_output_dir,
        seed=seed,
        effective_identity=effective_identity,
        generation_wall_time_seconds=generation_wall_time_seconds,
        audio_duration_seconds=audio_duration_seconds,
        real_time_factor=real_time_factor,
        pcm_parameters=assembly.audio_parameters.to_payload(),
        checksum_sha256=assembly.checksum,
    )
    _write_json(report_path, report)
    return report


def _run_direct(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_comparison_manifest(args.manifest)
    selected_profiles = manifest.select_profiles(getattr(args, "profiles", None))
    explicit_input_text_file = getattr(args, "input_text_file", None)
    input_text_file = Path(explicit_input_text_file) if explicit_input_text_file is not None else None
    if input_text_file is None and getattr(args, "text", None) is None:
        input_text_file = manifest.default_input_text_file
    if input_text_file is not None and not input_text_file.is_file():
        raise TTSComparisonError(
            f"Comparison input text file does not exist: {input_text_file.name}."
        )
    approved_reference = getattr(args, "approved_reference", None)
    if approved_reference is not None and not Path(approved_reference).is_file():
        raise TTSComparisonError(
            f"Approved XTTS reference WAV does not exist: {Path(approved_reference).name}."
        )
    normalized_text = _normalize_text(_read_text(args, fallback_input_text_file=input_text_file))
    base_output_dir = args.output_dir
    if base_output_dir is None:
        base_output_dir = _COMPARISON_OUTPUT_SUBDIR / time.strftime("%Y%m%d-%H%M%S")
    base_output_dir = Path(base_output_dir)
    summary_path = base_output_dir / "summary.json"
    playlist_path = base_output_dir / "playlist.m3u8"
    base_output_dir.mkdir(parents=True, exist_ok=True)

    profile_reports: list[dict[str, Any]] = []
    playlist_entries: list[tuple[str, str]] = []
    for profile in selected_profiles:
        if profile.requires_approved_reference and approved_reference is None:
            profile_dir, output_path, report_path = _profile_directories(base_output_dir, profile)
            profile_dir.mkdir(parents=True, exist_ok=True)
            report = _profile_summary(
                profile,
                status="skipped",
                reason="XTTS requires an approved reference WAV.",
                normalized_text=normalized_text,
                output_wav=output_path,
                report_path=report_path,
                base_output_dir=base_output_dir,
                seed=manifest.seed,
                generation_wall_time_seconds=0.0,
            )
            _write_json(report_path, report)
            profile_reports.append(report)
            continue
        report = _run_direct_profile(
            profile,
            normalized_text=normalized_text,
            seed=manifest.seed,
            approved_reference=approved_reference,
            base_output_dir=base_output_dir,
            overwrite=bool(getattr(args, "overwrite", False)),
        )
        profile_reports.append(report)
        if report["status"] == "completed":
            playlist_entries.append((profile.label, report["output_wav"]))

    summary = {
        "manifest_path": _display_repo_path(manifest.source_path),
        "input_text_file": _display_repo_path(input_text_file) if input_text_file is not None else None,
        "normalized_text": normalized_text,
        "seed": manifest.seed,
        "output_dir": _display_repo_path(base_output_dir),
        "summary": {
            "profile_count": len(profile_reports),
            "completed_count": sum(1 for item in profile_reports if item["status"] == "completed"),
            "failed_count": sum(1 for item in profile_reports if item["status"] == "failed"),
            "skipped_count": sum(1 for item in profile_reports if item["status"] == "skipped"),
        },
        "profiles": profile_reports,
        "playlist_path": "playlist.m3u8",
        "summary_path": "summary.json",
        "scoring_template": list(manifest.scoring_template),
    }

    _write_json(summary_path, summary)
    playlist_path.parent.mkdir(parents=True, exist_ok=True)
    playlist_lines = ["#EXTM3U"]
    for label, path in playlist_entries:
        playlist_lines.append(f"#EXTINF:-1,{label}")
        playlist_lines.append(path)
    playlist_path.write_text("\n".join(playlist_lines) + "\n", encoding="utf-8")
    return summary


def _legacy_run(args: argparse.Namespace) -> dict[str, Any]:
    """Backwards-compatible smoke-runner path used by older tests."""

    normalized_text = _normalize_text(tts_smoke._read_text(args))
    base_output_dir = args.output_dir
    if base_output_dir is None:
        base_output_dir = _COMPARISON_OUTPUT_SUBDIR / time.strftime("%Y%m%d-%H%M%S")
    base_output_dir = Path(base_output_dir)
    summary_path = base_output_dir / "summary.json"
    playlist_path = base_output_dir / "playlist.m3u8"

    base_output_dir.mkdir(parents=True, exist_ok=True)

    profiles = default_profiles()
    profile_reports: list[dict[str, Any]] = []
    playlist_entries: list[tuple[str, str]] = []

    for profile in profiles:
        settings = TTSSettings.from_mapping(profile.settings, provider=profile.provider)
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


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Run the comparison harness."""

    if getattr(args, "manifest", None) is not None or getattr(args, "approved_reference", None) is not None:
        return _run_direct(args)
    return _legacy_run(args)


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

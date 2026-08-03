"""Human-operated, offline TTS smoke runner for mock and Chatterbox V3."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.tts.benchmark import build_benchmark_report
from app.tts.assembly import WavAssemblyError, inspect_pcm_wav
from app.tts.manifest import ChunkManifest, SynthesisManifest, sanitize_synthesis_identity


class TTSSmokeError(RuntimeError):
    """An actionable smoke-runner failure."""


_KNOBS = ("exaggeration", "cfg_weight", "temperature", "repetition_penalty", "min_p", "top_p")


def build_parser() -> argparse.ArgumentParser:
    """Build the parser without importing optional provider dependencies."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("mock", "chatterbox_v3"), default="mock")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--text", help="Narration text to synthesize.")
    input_group.add_argument(
        "--input-text-file", type=Path, help="UTF-8 narration text file to synthesize."
    )
    parser.add_argument("--output", required=True, type=Path, help="Destination WAV path.")
    parser.add_argument("--report", type=Path, help="Destination JSON report (default: next to WAV).")
    parser.add_argument("--language", default="pl", help="Chatterbox language id (default: pl).")
    parser.add_argument("--device", default="cpu", help="Chatterbox device (default: cpu).")
    parser.add_argument("--audio-prompt", type=Path, help="Optional local speaker-reference WAV.")
    parser.add_argument("--model-variant", choices=("v3",), default="v3")
    parser.add_argument("--overwrite", action="store_true", help="Permit replacing an existing WAV or report.")
    for knob in _KNOBS:
        parser.add_argument(f"--{knob.replace('_', '-')}", type=float)
    return parser


def _validate_wav(path: Path):
    try:
        parameters, _ = inspect_pcm_wav(path.read_bytes())
    except (OSError, WavAssemblyError) as exc:
        raise TTSSmokeError(f"Output is not a valid WAV file: {path}") from exc
    if parameters.frame_count <= 0:
        raise TTSSmokeError("Output WAV must have a positive sample rate and at least one frame.")
    return parameters


def _create_provider(args: argparse.Namespace) -> Any:
    if args.provider == "mock":
        from app.providers.mock_tts import MockTTSProvider

        return MockTTSProvider("mock")
    from app.providers.chatterbox_v3 import ChatterboxV3Provider

    return ChatterboxV3Provider(
        "chatterbox_v3", device=args.device, language_id=args.language, audio_prompt_path=args.audio_prompt
    )


def _effective_synthesis_identity(
    provider: Any,
    voice_config: dict[str, Any],
    *,
    provider_name: str,
    model_variant: str,
    device: str,
    language: str,
) -> dict[str, Any]:
    """Return a provider identity when supported, otherwise a compatibility fallback."""
    effective_identity = getattr(provider, "effective_synthesis_identity", None)
    if callable(effective_identity):
        identity = effective_identity(voice_config)
        if isinstance(identity, dict):
            return identity
    return {
        "provider": provider_name,
        "model_variant": model_variant,
        "device": device,
        "language_id": language,
    }


def _read_text(args: argparse.Namespace) -> str:
    if args.input_text_file is None:
        return args.text
    try:
        return args.input_text_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TTSSmokeError(
            f"Cannot read UTF-8 input text file: {args.input_text_file}"
        ) from exc


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Generate one WAV and its machine-readable smoke report."""
    text = " ".join(_read_text(args).split())
    if not text:
        raise TTSSmokeError("Synthesis text must not be empty.")
    report_path = args.report or args.output.with_suffix(".json")
    existing = [path for path in (args.output, report_path) if path.exists()]
    if existing and not args.overwrite:
        raise TTSSmokeError("Refusing to overwrite existing file(s); pass --overwrite: " + ", ".join(str(path) for path in existing))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    voice_config: dict[str, Any] = {"language_id": args.language}
    if args.audio_prompt is not None:
        voice_config["audio_prompt_path"] = str(args.audio_prompt)
    voice_config.update({name: getattr(args, name) for name in _KNOBS if getattr(args, name) is not None})
    started = time.perf_counter()
    provider = _create_provider(args)
    effective_identity = _effective_synthesis_identity(
        provider,
        voice_config,
        provider_name=args.provider,
        model_variant=args.model_variant,
        device=args.device,
        language=args.language,
    )
    result = provider.synthesize(text, voice_config)
    generation_seconds = time.perf_counter() - started
    if result.audio_format != "wav":
        raise TTSSmokeError("Provider did not return WAV audio.")
    args.output.write_bytes(result.audio_bytes)
    parameters = _validate_wav(args.output)
    duration_seconds = parameters.duration_seconds
    checksum = hashlib.sha256(result.audio_bytes).hexdigest()
    chunk = ChunkManifest("smoke-0001", 0, "completed", "smoke", "smoke", "smoke", checksum, duration_seconds, parameters)
    manifest = SynthesisManifest(
        config_hash="smoke", chunks={chunk.chunk_id: chunk}, final_status="completed",
        final_checksum=checksum, final_duration_seconds=duration_seconds, final_audio_parameters=parameters,
        effective_synthesis_identity=sanitize_synthesis_identity(effective_identity),
        generated_chunk_count=1,
    )
    report = build_benchmark_report(
        manifest, word_count=len(text.split()), generation_wall_time_seconds=generation_seconds,
    ).to_payload()
    report.update({
        # Keep the legacy field aligned with the effective identity-backed
        # benchmark model; CLI selection must not overwrite reported identity.
        "model_variant": report["model"], "generation_seconds": report["generation_wall_time_seconds"],
        "checksum_sha256": checksum, "voice": result.metadata.get("voice", "builtin"),
        "output_wav": str(args.output),
        "channels": parameters.channels,
        "sample_width": parameters.sample_width,
        "sample_rate": parameters.sample_rate,
        "compression_type": parameters.compression_type,
        "frame_count": parameters.frame_count,
        "duration_seconds": parameters.duration_seconds,
    })
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = run(args)
    except (TTSSmokeError, OSError, ValueError, RuntimeError) as exc:
        print(f"tts smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a CLI.
    raise SystemExit(main())

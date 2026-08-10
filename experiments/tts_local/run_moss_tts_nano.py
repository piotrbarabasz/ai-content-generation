"""Manual Polish quality runner for the official MOSS-TTS-Nano ONNX backend."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sys
import time
import uuid
import wave
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MODEL_ID = "OpenMOSS-Team/MOSS-TTS-Nano-100M-ONNX"
CODEC_ID = "OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX"
DEFAULT_TEXT = ROOT / "experiments/tts_local/benchmark_pl.txt"
DEFAULT_OUTPUT = ROOT / ".runtime/tts-experiments/outputs/moss-tts-nano/benchmark.wav"
DEFAULT_CHECKOUT = ROOT / ".runtime/tts-experiments/upstream/MOSS-TTS-Nano"


class RunnerArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def parse_args() -> argparse.Namespace:
    parser = RunnerArgumentParser(description="Generate the shared Polish benchmark with MOSS-TTS-Nano ONNX.")
    parser.add_argument("--text-file", type=Path, default=DEFAULT_TEXT, help="UTF-8 input text.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Destination WAV.")
    parser.add_argument("--report", type=Path, help="JSON report (default: output with .json suffix).")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cpu")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--reference-audio", type=Path)
    parser.add_argument("--reference-text-file", type=Path)
    parser.add_argument(
        "--upstream-checkout",
        type=Path,
        default=DEFAULT_CHECKOUT,
        help="Official OpenMOSS/MOSS-TTS-Nano checkout installed into this environment.",
    )
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    expanded = path.expanduser()
    return (expanded if expanded.is_absolute() else ROOT / expanded).resolve()


def safe_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return f"<external>/{path.name}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_versions(names: tuple[str, ...]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return versions


def inspect_wav(path: Path) -> tuple[float, int, int]:
    try:
        with wave.open(str(path), "rb") as wav_file:
            rate, channels, frames = wav_file.getframerate(), wav_file.getnchannels(), wav_file.getnframes()
    except (wave.Error, EOFError):
        try:
            import soundfile as sf
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Cannot inspect this WAV encoding: missing package 'soundfile'. Use the moss-nano environment; "
                "see README section 'MOSS-TTS-Nano setup'."
            ) from exc
        info = sf.info(str(path))
        rate, channels, frames = info.samplerate, info.channels, info.frames
    duration = frames / rate if rate else 0.0
    if not path.is_file() or path.stat().st_size == 0 or rate <= 0 or duration <= 0:
        raise RuntimeError("MOSS-TTS-Nano did not create a valid, non-empty WAV.")
    return duration, rate, channels


def write_report(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    output = resolve_path(args.output)
    report = resolve_path(args.report) if args.report else output.with_suffix(".json")
    text_path = resolve_path(args.text_file)
    checkout = resolve_path(args.upstream_checkout)
    reference = resolve_path(args.reference_audio) if args.reference_audio else None
    reference_text = resolve_path(args.reference_text_file) if args.reference_text_file else None
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.wav")
    try:
        if output.suffix.lower() != ".wav" or report == output:
            raise ValueError("--output must be a .wav file and --report must be a different path.")
        if not text_path.is_file():
            raise FileNotFoundError(f"Input text file not found: {safe_path(text_path)}")
        if not text_path.read_text(encoding="utf-8"):
            raise ValueError("Input text file is empty.")
        if not (checkout / "moss_tts_nano/cli.py").is_file():
            raise FileNotFoundError(
                f"Official upstream checkout not found at {safe_path(checkout)}. "
                "See README section 'MOSS-TTS-Nano setup'."
            )
        if reference and not reference.is_file():
            raise FileNotFoundError(f"Reference audio not found: {safe_path(reference)}")
        if reference_text and not reference_text.is_file():
            raise FileNotFoundError(f"Reference transcript not found: {safe_path(reference_text)}")
        if reference_text and not reference:
            raise ValueError("--reference-text-file requires --reference-audio.")
        existing = [path for path in (output, report) if path.exists()]
        if existing and not args.overwrite:
            raise FileExistsError("Refusing to overwrite: " + ", ".join(safe_path(path) for path in existing))
        effective_device = "cuda" if args.device == "cuda" else "cpu"
        output.parent.mkdir(parents=True, exist_ok=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        sys.path.insert(0, str(checkout))
        try:
            from moss_tts_nano.cli import main as upstream_main
        except ModuleNotFoundError as exc:
            missing = exc.name or "moss-tts-nano"
            raise RuntimeError(
                f"Missing package '{missing}'. Use the isolated moss-nano environment and install the official "
                "checkout; see README section 'MOSS-TTS-Nano setup'."
            ) from exc

        upstream_args = [
            "generate",
            "--backend",
            "onnx",
            "--execution-provider",
            effective_device,
            "--text-file",
            str(text_path),
            "--output",
            str(temporary),
            "--seed",
            str(args.seed),
        ]
        if reference:
            upstream_args.extend(("--prompt-speech", str(reference)))
        generation_started = time.perf_counter()
        result = upstream_main(upstream_args)
        generation_seconds = time.perf_counter() - generation_started
        if result not in (None, 0):
            raise RuntimeError(f"Official MOSS-TTS-Nano runner returned exit code {result}.")
        duration, sample_rate, channels = inspect_wav(temporary)
        temporary.replace(output)
        payload: dict[str, Any] = {
            "status": "completed",
            "runner": Path(__file__).name,
            "model_id": f"{MODEL_ID} + {CODEC_ID}",
            "model_revision": None,
            "device_requested": args.device,
            "device_effective": effective_device,
            "input_text_path": safe_path(text_path),
            "input_text_sha256": sha256_file(text_path),
            "reference_audio_used": reference is not None,
            "reference_audio_sha256": sha256_file(reference) if reference else None,
            "reference_text_sha256": sha256_file(reference_text) if reference_text else None,
            "model_load_seconds": 0.0,
            "generation_seconds": generation_seconds,
            "audio_duration_seconds": duration,
            "real_time_factor": generation_seconds / duration,
            "sample_rate_hz": sample_rate,
            "channels": channels,
            "output_wav": safe_path(output),
            "package_versions": package_versions(("moss-tts-nano", "onnxruntime", "numpy", "soundfile")),
            "generation_parameters": {
                "backend": "onnx",
                "execution_provider": effective_device,
                "seed": args.seed,
                "built_in_voice_when_no_reference": "Junhao",
                "reference_transcript_used_by_backend": False,
                "timing_note": "generation_seconds includes upstream model loading because the official CLI does not expose separate timings",
            },
            "post_processing": ["Official runtime text preparation/normalization defaults"],
        }
        write_report(report, payload)
        print(f"Created {safe_path(output)} and {safe_path(report)}")
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        if temporary.exists():
            temporary.unlink()
        detail = (
            f"Missing package '{exc.name}'. Use the isolated moss-nano environment; "
            "see README section 'MOSS-TTS-Nano setup'."
            if isinstance(exc, ModuleNotFoundError)
            else str(exc)
        )
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

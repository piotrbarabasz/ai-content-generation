"""Manual Polish voice-cloning runner for the official OmniVoice package."""

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
MODEL_ID = "k2-fsa/OmniVoice"
DEFAULT_TEXT = ROOT / "experiments/tts_local/benchmark_pl.txt"
DEFAULT_OUTPUT = ROOT / ".runtime/tts-experiments/outputs/omnivoice/benchmark.wav"


class RunnerArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def parse_args() -> argparse.Namespace:
    parser = RunnerArgumentParser(description="Generate the shared Polish benchmark with OmniVoice cloning.")
    parser.add_argument("--text-file", type=Path, default=DEFAULT_TEXT, help="UTF-8 input text.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Destination WAV.")
    parser.add_argument("--report", type=Path, help="JSON report (default: output with .json suffix).")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--seed", type=int, default=42, help="Recorded only; no seed API is documented upstream.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--reference-audio", type=Path, required=True)
    parser.add_argument("--reference-text-file", type=Path, required=True)
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
                "Cannot inspect this WAV encoding: missing package 'soundfile'. Use the omnivoice environment; "
                "see README section 'OmniVoice setup'."
            ) from exc
        info = sf.info(str(path))
        rate, channels, frames = info.samplerate, info.channels, info.frames
    duration = frames / rate if rate else 0.0
    if not path.is_file() or path.stat().st_size == 0 or rate <= 0 or duration <= 0:
        raise RuntimeError("OmniVoice did not create a valid, non-empty WAV.")
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
    reference = resolve_path(args.reference_audio)
    reference_text_path = resolve_path(args.reference_text_file)
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.wav")
    try:
        if output.suffix.lower() != ".wav" or report == output:
            raise ValueError("--output must be a .wav file and --report must be a different path.")
        for label, path in (("Input text", text_path), ("Reference audio", reference), ("Reference transcript", reference_text_path)):
            if not path.is_file():
                raise FileNotFoundError(f"{label} not found: {safe_path(path)}")
        text = text_path.read_text(encoding="utf-8")
        reference_text = reference_text_path.read_text(encoding="utf-8")
        if not text or not reference_text:
            raise ValueError("Input text and exact reference transcript must both be non-empty.")
        existing = [path for path in (output, report) if path.exists()]
        if existing and not args.overwrite:
            raise FileExistsError("Refusing to overwrite: " + ", ".join(safe_path(path) for path in existing))
        output.parent.mkdir(parents=True, exist_ok=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        try:
            import soundfile as sf
            import torch
            from omnivoice import OmniVoice
        except ModuleNotFoundError as exc:
            missing = exc.name or "omnivoice"
            raise RuntimeError(
                f"Missing package '{missing}'. Use the isolated omnivoice environment; "
                "see README section 'OmniVoice setup'."
            ) from exc
        if args.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false. Use --device cpu or fix CUDA.")
        effective_device = "cuda" if (args.device == "cuda" or (args.device == "auto" and torch.cuda.is_available())) else "cpu"
        dtype = torch.float16 if effective_device == "cuda" else torch.float32
        device_map = "cuda:0" if effective_device == "cuda" else "cpu"
        load_started = time.perf_counter()
        model = OmniVoice.from_pretrained(MODEL_ID, device_map=device_map, dtype=dtype)
        model_load_seconds = time.perf_counter() - load_started
        generation_started = time.perf_counter()
        audio = model.generate(text=text, ref_audio=str(reference), ref_text=reference_text)
        generation_seconds = time.perf_counter() - generation_started
        if not audio:
            raise RuntimeError("Official OmniVoice API returned no audio arrays.")
        sf.write(str(temporary), audio[0], 24000)
        duration, sample_rate, channels = inspect_wav(temporary)
        temporary.replace(output)
        payload: dict[str, Any] = {
            "status": "completed",
            "runner": Path(__file__).name,
            "model_id": MODEL_ID,
            "model_revision": None,
            "device_requested": args.device,
            "device_effective": effective_device,
            "input_text_path": safe_path(text_path),
            "input_text_sha256": sha256_file(text_path),
            "reference_audio_used": True,
            "reference_audio_sha256": sha256_file(reference),
            "reference_text_sha256": sha256_file(reference_text_path),
            "model_load_seconds": model_load_seconds,
            "generation_seconds": generation_seconds,
            "audio_duration_seconds": duration,
            "real_time_factor": generation_seconds / duration,
            "sample_rate_hz": sample_rate,
            "channels": channels,
            "output_wav": safe_path(output),
            "package_versions": package_versions(("omnivoice", "torch", "transformers", "soundfile")),
            "generation_parameters": {
                "dtype": "float16" if effective_device == "cuda" else "float32",
                "seed_requested": args.seed,
                "seed_applied": False,
            },
            "post_processing": [],
        }
        write_report(report, payload)
        print(f"Created {safe_path(output)} and {safe_path(report)}")
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        if temporary.exists():
            temporary.unlink()
        detail = (
            f"Missing package '{exc.name}'. Use the isolated omnivoice environment; "
            "see README section 'OmniVoice setup'."
            if isinstance(exc, ModuleNotFoundError)
            else str(exc)
        )
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

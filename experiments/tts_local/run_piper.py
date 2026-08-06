"""Manual Polish runner for the official Piper Python API."""

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
MODEL_ID = "rhasspy/piper-voices:pl_PL-darkman-medium"
DEFAULT_TEXT = ROOT / "experiments/tts_local/benchmark_pl.txt"
DEFAULT_OUTPUT = ROOT / ".runtime/tts-experiments/outputs/piper/benchmark.wav"
DEFAULT_MODEL_ROOT = ROOT / ".runtime/piper/pl_PL-darkman-medium"


class RunnerArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def parse_args() -> argparse.Namespace:
    parser = RunnerArgumentParser(description="Generate the shared Polish benchmark with Piper.")
    parser.add_argument("--text-file", type=Path, default=DEFAULT_TEXT, help="UTF-8 input text.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Destination WAV.")
    parser.add_argument("--report", type=Path, help="JSON report (default: output with .json suffix).")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cpu")
    parser.add_argument("--seed", type=int, default=42, help="Recorded only; Piper has no seed argument.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--length-scale", type=float, default=1.0)
    parser.add_argument("--sentence-silence", type=float, default=0.45)
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
    with wave.open(str(path), "rb") as wav_file:
        rate, channels, frames = wav_file.getframerate(), wav_file.getnchannels(), wav_file.getnframes()
    duration = frames / rate if rate else 0.0
    if not path.is_file() or path.stat().st_size == 0 or rate <= 0 or duration <= 0:
        raise RuntimeError("Piper did not create a valid, non-empty WAV.")
    return duration, rate, channels


def find_model(explicit: Path | None) -> Path:
    if explicit:
        model = resolve_path(explicit)
        if not model.is_file():
            raise FileNotFoundError(f"Piper model not found: {safe_path(model)}")
        return model
    matches = sorted(DEFAULT_MODEL_ROOT.rglob("pl_PL-darkman-medium.onnx")) if DEFAULT_MODEL_ROOT.is_dir() else []
    if not matches:
        raise FileNotFoundError(
            "Piper model pl_PL-darkman-medium was not found under .runtime/piper/pl_PL-darkman-medium. "
            "Pass --model-path or see README section 'Piper setup'."
        )
    if len(matches) > 1:
        raise RuntimeError("Multiple Piper models matched; pass --model-path explicitly: " + ", ".join(safe_path(p) for p in matches))
    return matches[0].resolve()


def write_report(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    output = resolve_path(args.output)
    report = resolve_path(args.report) if args.report else output.with_suffix(".json")
    text_path = resolve_path(args.text_file)
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.wav")
    try:
        if output.suffix.lower() != ".wav" or report == output:
            raise ValueError("--output must be a .wav file and --report must be a different path.")
        if args.length_scale <= 0 or args.sentence_silence < 0:
            raise ValueError("--length-scale must be positive and --sentence-silence must be non-negative.")
        if not text_path.is_file():
            raise FileNotFoundError(f"Input text file not found: {safe_path(text_path)}")
        text = text_path.read_text(encoding="utf-8")
        if not text:
            raise ValueError("Input text file is empty.")
        model_path = find_model(args.model_path)
        config_path = Path(f"{model_path}.json")
        if not config_path.is_file():
            raise FileNotFoundError(f"Piper model config not found: {safe_path(config_path)}")
        existing = [path for path in (output, report) if path.exists()]
        if existing and not args.overwrite:
            raise FileExistsError("Refusing to overwrite: " + ", ".join(safe_path(path) for path in existing))
        output.parent.mkdir(parents=True, exist_ok=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        try:
            from piper import PiperVoice, SynthesisConfig
        except ModuleNotFoundError as exc:
            missing = exc.name or "piper-tts"
            raise RuntimeError(
                f"Missing package '{missing}'. Use the isolated piper environment; see README section 'Piper setup'."
            ) from exc
        effective_device = "cuda" if args.device == "cuda" else "cpu"
        load_started = time.perf_counter()
        voice = PiperVoice.load(model_path, use_cuda=effective_device == "cuda")
        model_load_seconds = time.perf_counter() - load_started
        synthesis_config = SynthesisConfig(length_scale=args.length_scale)
        generation_started = time.perf_counter()
        chunk_count = 0
        with wave.open(str(temporary), "wb") as wav_file:
            for chunk in voice.synthesize(text, syn_config=synthesis_config):
                if chunk_count == 0:
                    wav_file.setframerate(chunk.sample_rate)
                    wav_file.setsampwidth(chunk.sample_width)
                    wav_file.setnchannels(chunk.sample_channels)
                else:
                    silence_frames = round(chunk.sample_rate * args.sentence_silence)
                    wav_file.writeframes(b"\x00" * silence_frames * chunk.sample_width * chunk.sample_channels)
                wav_file.writeframes(chunk.audio_int16_bytes)
                chunk_count += 1
        generation_seconds = time.perf_counter() - generation_started
        if chunk_count == 0:
            raise RuntimeError("Official Piper API produced no sentence chunks.")
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
            "reference_audio_used": False,
            "reference_audio_sha256": None,
            "reference_text_sha256": None,
            "model_load_seconds": model_load_seconds,
            "generation_seconds": generation_seconds,
            "audio_duration_seconds": duration,
            "real_time_factor": generation_seconds / duration,
            "sample_rate_hz": sample_rate,
            "channels": channels,
            "output_wav": safe_path(output),
            "package_versions": package_versions(("piper-tts", "onnxruntime", "numpy")),
            "generation_parameters": {
                "model_path": safe_path(model_path),
                "length_scale": args.length_scale,
                "sentence_silence": args.sentence_silence,
                "sentence_chunks": chunk_count,
                "seed_requested": args.seed,
                "seed_applied": False,
            },
            "post_processing": [
                f"Inserted {args.sentence_silence:.3f}s zero-PCM silence between {chunk_count} official sentence chunks"
            ] if chunk_count > 1 and args.sentence_silence > 0 else [],
        }
        write_report(report, payload)
        print(f"Created {safe_path(output)} and {safe_path(report)}")
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        if temporary.exists():
            temporary.unlink()
        detail = (
            f"Missing package '{exc.name}'. Use the isolated piper environment; "
            "see README section 'Piper setup'."
            if isinstance(exc, ModuleNotFoundError)
            else str(exc)
        )
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

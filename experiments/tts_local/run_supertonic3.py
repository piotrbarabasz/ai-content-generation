"""Manual Polish quality runner for the official Supertonic 3 Python SDK."""

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
MODEL_ID = "Supertone/supertonic-3"
DEFAULT_TEXT = ROOT / "experiments/tts_local/benchmark_pl.txt"
DEFAULT_OUTPUT = ROOT / ".runtime/tts-experiments/outputs/supertonic3/benchmark.wav"


class RunnerArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def parse_args() -> argparse.Namespace:
    parser = RunnerArgumentParser(description="Generate the shared Polish benchmark with Supertonic 3.")
    parser.add_argument("--text-file", type=Path, default=DEFAULT_TEXT, help="UTF-8 input text.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Destination WAV.")
    parser.add_argument("--report", type=Path, help="JSON report (default: output with .json suffix).")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cpu")
    parser.add_argument("--seed", type=int, default=42, help="Recorded only; the SDK has no seed argument.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--voice", default="M1")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--steps", type=int, default=8)
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
            rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            frames = wav_file.getnframes()
    except (wave.Error, EOFError):
        try:
            import soundfile as sf
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Cannot inspect this WAV encoding: missing package 'soundfile'. Use the supertonic3 "
                "environment; see README section 'Supertonic 3 setup'."
            ) from exc
        info = sf.info(str(path))
        rate, channels, frames = info.samplerate, info.channels, info.frames
    duration = frames / rate if rate else 0.0
    if not path.is_file() or path.stat().st_size == 0 or rate <= 0 or duration <= 0:
        raise RuntimeError("Supertonic did not create a valid, non-empty WAV.")
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
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.wav")
    try:
        if output.suffix.lower() != ".wav" or report == output:
            raise ValueError("--output must be a .wav file and --report must be a different path.")
        if not text_path.is_file():
            raise FileNotFoundError(f"Input text file not found: {safe_path(text_path)}")
        text = text_path.read_text(encoding="utf-8")
        if not text:
            raise ValueError("Input text file is empty.")
        existing = [path for path in (output, report) if path.exists()]
        if existing and not args.overwrite:
            raise FileExistsError("Refusing to overwrite: " + ", ".join(safe_path(path) for path in existing))
        if args.device == "cuda":
            raise ValueError(
                "The current official supertonic Python SDK does not expose CUDA device selection; use --device cpu."
            )
        effective_device = "cpu"
        output.parent.mkdir(parents=True, exist_ok=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        try:
            from supertonic import TTS
        except ModuleNotFoundError as exc:
            missing = exc.name or "supertonic"
            raise RuntimeError(
                f"Missing package '{missing}'. Use the isolated supertonic3 environment; "
                "see README section 'Supertonic 3 setup'."
            ) from exc

        load_started = time.perf_counter()
        tts = TTS(auto_download=True)
        style = tts.get_voice_style(voice_name=args.voice)
        model_load_seconds = time.perf_counter() - load_started
        generation_started = time.perf_counter()
        waveform, _ = tts.synthesize(
            text=text,
            voice_style=style,
            total_steps=args.steps,
            speed=args.speed,
            lang="pl",
            verbose=False,
        )
        generation_seconds = time.perf_counter() - generation_started
        tts.save_audio(waveform, str(temporary))
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
            "package_versions": package_versions(("supertonic", "onnxruntime", "numpy", "soundfile")),
            "generation_parameters": {
                "voice": args.voice,
                "speed": args.speed,
                "steps": args.steps,
                "language": "pl",
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
            f"Missing package '{exc.name}'. Use the isolated supertonic3 environment; "
            "see README section 'Supertonic 3 setup'."
            if isinstance(exc, ModuleNotFoundError)
            else str(exc)
        )
        print(f"ERROR: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

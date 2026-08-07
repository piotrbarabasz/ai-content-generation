"""Guarded Windows-friendly runner for first-class MOSS-TTS-v1.5 GGUF inference."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MODEL_ID = "OpenMOSS-Team/MOSS-TTS-v1.5"
CODEC_ID = "OpenMOSS-Team/MOSS-Audio-Tokenizer-ONNX"
RUNTIME_ID = "OpenMOSS/llama.cpp@moss-tts-firstclass"
DEFAULT_TEXT = ROOT / "experiments/tts_local/benchmark_pl.txt"
DEFAULT_OUTPUT = ROOT / ".runtime/tts-experiments/outputs/moss-tts-v15/benchmark.wav"
DEFAULT_MODEL_ROOT = ROOT / ".runtime/tts-experiments/models/moss-tts-v15"
DEFAULT_MODEL_GGUF = DEFAULT_MODEL_ROOT / "gguf/moss_tts_v15_firstclass_f16.gguf"
DEFAULT_TOKENIZER_DIR = DEFAULT_MODEL_ROOT / "hf"
DEFAULT_ONNX_DIR = DEFAULT_MODEL_ROOT / "audio-tokenizer-onnx"
DEFAULT_PROVENANCE = DEFAULT_MODEL_ROOT / "provenance.json"
DEFAULT_RUNTIME = ROOT / ".runtime/tts-experiments/upstream/llama.cpp-moss"
DEFAULT_LANGUAGE = "Polish"
DEFAULT_MAX_NEW_TOKENS = 512
BACKBONE_LAYER_COUNT = 36
TEXT_TEMPERATURE = 1.5
AUDIO_TEMPERATURE = 1.7


class RunnerArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def parse_args() -> argparse.Namespace:
    parser = RunnerArgumentParser(
        description=(
            "Generate the unchanged Polish benchmark with an officially verified first-class "
            "MOSS-TTS-v1.5 GGUF and the OpenMOSS llama.cpp runtime."
        )
    )
    parser.add_argument("--text-file", type=Path, default=DEFAULT_TEXT, help="UTF-8 input text.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Destination WAV.")
    parser.add_argument("--report", type=Path, help="JSON report (default: output with .json suffix).")
    parser.add_argument("--device", choices=("cpu", "cuda", "hybrid"), default="hybrid")
    parser.add_argument(
        "--gpu-layers",
        type=int,
        default=0,
        help="Backbone layers offloaded by llama.cpp; 0 keeps the backbone on CPU.",
    )
    parser.add_argument(
        "--reference-audio",
        type=Path,
        help="Optional 24 kHz mono PCM WAV; the file is validated but never modified.",
    )
    parser.add_argument(
        "--language",
        default=DEFAULT_LANGUAGE,
        help="Official prompt language label (default: Polish).",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=DEFAULT_MAX_NEW_TOKENS,
        help="Maximum generation steps (official first-class default: 512).",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing WAV/report pair.")
    parser.add_argument("--model-gguf", type=Path, default=DEFAULT_MODEL_GGUF, help=argparse.SUPPRESS)
    parser.add_argument("--tokenizer-dir", type=Path, default=DEFAULT_TOKENIZER_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--onnx-dir", type=Path, default=DEFAULT_ONNX_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--runtime-checkout", type=Path, default=DEFAULT_RUNTIME, help=argparse.SUPPRESS)
    parser.add_argument("--llama-bin", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--provenance", type=Path, default=DEFAULT_PROVENANCE, help=argparse.SUPPRESS)
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


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read valid provenance JSON at {safe_path(path)}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Provenance must be a JSON object: {safe_path(path)}")
    return value


def write_report(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def inspect_wav(path: Path) -> tuple[float, int, int]:
    try:
        with wave.open(str(path), "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            frames = wav_file.getnframes()
    except (OSError, EOFError, wave.Error) as exc:
        raise RuntimeError(f"Runtime did not create a readable PCM WAV: {exc}") from exc
    duration = frames / sample_rate if sample_rate else 0.0
    if not path.is_file() or path.stat().st_size == 0 or sample_rate <= 0 or duration <= 0:
        raise RuntimeError("Runtime did not create a valid, non-empty WAV.")
    return duration, sample_rate, channels


def validate_reference(path: Path) -> None:
    if path.suffix.lower() != ".wav":
        raise ValueError("--reference-audio must be a 24 kHz mono WAV for the first-class path.")
    try:
        with wave.open(str(path), "rb") as wav_file:
            rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            frames = wav_file.getnframes()
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError(f"Reference audio must be a readable PCM WAV: {exc}") from exc
    if rate != 24000 or channels != 1 or frames <= 0:
        raise ValueError(
            "Reference audio must be a non-empty 24 kHz mono WAV. The source was not modified. "
            "See the README for an explicit ffmpeg conversion command."
        )


def git_revision(checkout: Path) -> str | None:
    if not (checkout / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]{40}", revision) else None


def system_ram_mb() -> int | None:
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.ullTotalPhys / (1024 * 1024))
        except (AttributeError, OSError):
            return None
    if hasattr(os, "sysconf"):
        try:
            return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 * 1024))
        except (OSError, ValueError):
            return None
    return None


def gpu_telemetry() -> dict[str, Any]:
    telemetry: dict[str, Any] = {
        "gpu_name": None,
        "vram_total_mb": None,
        "vram_used_mb": None,
    }
    executable = shutil.which("nvidia-smi")
    if not executable:
        return telemetry
    result = subprocess.run(
        [
            executable,
            "--query-gpu=name,memory.total,memory.used",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not result.stdout.strip():
        return telemetry
    fields = [field.strip() for field in result.stdout.splitlines()[0].split(",")]
    if len(fields) == 3:
        telemetry["gpu_name"] = fields[0]
        try:
            telemetry["vram_total_mb"] = int(fields[1])
            telemetry["vram_used_mb"] = int(fields[2])
        except ValueError:
            pass
    return telemetry


def find_llama_binary(runtime: Path, device: str, explicit: Path | None) -> Path:
    if explicit is not None:
        return resolve_path(explicit)
    build_name = "build-cpu" if device == "cpu" else "build-cuda"
    basename = "llama-moss-tts.exe" if os.name == "nt" else "llama-moss-tts"
    candidates = (
        runtime / build_name / "bin/Release" / basename,
        runtime / build_name / "bin" / basename,
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def require_onnx_cuda_provider() -> None:
    try:
        import onnxruntime
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "--device cuda requires an ONNX Runtime build with CUDA support; use --device hybrid to keep the codec on CPU."
        ) from exc
    if "CUDAExecutionProvider" not in onnxruntime.get_available_providers():
        raise RuntimeError(
            "--device cuda requested a CUDA audio codec, but CUDAExecutionProvider is unavailable. "
            "Use --device hybrid for a CUDA-offloaded backbone with CPU ONNX audio encode/decode."
        )


def run_component(command: list[str], name: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0 and result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    if result.returncode != 0:
        combined = f"{result.stdout}\n{result.stderr}".strip()
        raise ComponentFailure(name, result.returncode, combined)
    return result


class ComponentFailure(RuntimeError):
    def __init__(self, component: str, returncode: int, output: str) -> None:
        super().__init__(f"{component} failed with exit code {returncode}.")
        self.component = component
        self.returncode = returncode
        self.output = output


def is_cuda_oom(exc: BaseException) -> bool:
    detail = str(exc)
    if isinstance(exc, ComponentFailure):
        detail += "\n" + exc.output
    lowered = detail.lower()
    indicators = (
        "cuda out of memory",
        "cuda memory was exhausted",
        "out of memory on device",
        "cudamalloc",
        "ggml_cuda_error_out_of_memory",
        "cudaerroroutofmemory",
    )
    return any(indicator in lowered for indicator in indicators)


def lower_layer_suggestions(gpu_layers: int) -> str:
    if gpu_layers > 8:
        return "8 or 4"
    if gpu_layers > 4:
        return "4 or 0"
    if gpu_layers > 0:
        return "0"
    return "CPU mode with --device cpu --gpu-layers 0"


def validate_provenance(provenance: dict[str, Any], model_gguf: Path) -> tuple[str, str, str, str]:
    if provenance.get("model_id") != MODEL_ID:
        raise RuntimeError(f"Provenance does not identify the exact required model: {MODEL_ID}")
    if provenance.get("audio_tokenizer_id") != CODEC_ID:
        raise RuntimeError(f"Provenance does not identify the required ONNX audio tokenizer: {CODEC_ID}")
    if provenance.get("conversion_status") != "verified_official_v15":
        reason = provenance.get("conversion_block_reason") or "official v1.5 conversion is not verified"
        raise RuntimeError(
            "Runtime setup is blocked: " + str(reason) + ". Do not substitute MOSS-TTS 1.0 or a community GGUF."
        )
    prepared = provenance.get("prepared_ggufs")
    if not isinstance(prepared, list):
        raise RuntimeError("Provenance has no verified prepared_ggufs entries.")
    resolved_model = model_gguf.resolve()
    entry = next(
        (
            item
            for item in prepared
            if isinstance(item, dict)
            and resolve_path(Path(str(item.get("path", "")))) == resolved_model
            and item.get("source_model_id") == MODEL_ID
        ),
        None,
    )
    if entry is None:
        raise RuntimeError("The selected GGUF is not a verified v1.5 conversion in provenance.json.")
    expected_sha = entry.get("sha256")
    if not isinstance(expected_sha, str) or sha256_file(model_gguf) != expected_sha:
        raise RuntimeError("The selected GGUF does not match its recorded SHA-256 provenance.")
    model_revision = provenance.get("model_revision")
    runtime_revision = provenance.get("runtime_revision")
    quantization = entry.get("quantization")
    if not all(isinstance(value, str) and value for value in (model_revision, runtime_revision, quantization)):
        raise RuntimeError("Provenance is missing model/runtime revision or quantization identity.")
    return model_revision, runtime_revision, quantization, expected_sha


def main() -> int:
    args = parse_args()
    output = resolve_path(args.output)
    report = resolve_path(args.report) if args.report else output.with_suffix(".json")
    text_path = resolve_path(args.text_file)
    reference = resolve_path(args.reference_audio) if args.reference_audio else None
    model_gguf = resolve_path(args.model_gguf)
    tokenizer_dir = resolve_path(args.tokenizer_dir)
    onnx_dir = resolve_path(args.onnx_dir)
    provenance_path = resolve_path(args.provenance)
    runtime = resolve_path(args.runtime_checkout)
    llama_bin = find_llama_binary(runtime, args.device, args.llama_bin)
    build_ref_script = runtime / "tools/tts/moss-tts-build-generation-ref.py"
    decode_script = runtime / "tools/tts/moss-tts-audio-decode.py"
    temporary_output = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.wav")

    try:
        if args.gpu_layers < 0:
            raise ValueError("--gpu-layers must be zero or greater; -1/all-layer offload is intentionally rejected.")
        if args.gpu_layers > BACKBONE_LAYER_COUNT:
            raise ValueError(f"--gpu-layers cannot exceed the v1.5 backbone's {BACKBONE_LAYER_COUNT} layers.")
        if args.max_new_tokens <= 0:
            raise ValueError("--max-new-tokens must be greater than zero.")
        if args.device == "cpu" and args.gpu_layers != 0:
            raise ValueError("--device cpu requires --gpu-layers 0.")
        if args.device == "cuda" and args.gpu_layers == 0:
            raise ValueError("--device cuda requires a positive --gpu-layers value; use cpu or hybrid for zero.")
        if args.device == "cuda":
            require_onnx_cuda_provider()
        if not args.language.strip():
            raise ValueError("--language must be a non-empty official prompt language label.")
        if output.suffix.lower() != ".wav" or report == output:
            raise ValueError("--output must be a .wav file and --report must be a different path.")
        if not text_path.is_file():
            raise FileNotFoundError(f"Input text file not found: {safe_path(text_path)}")
        text = text_path.read_text(encoding="utf-8")
        if not text:
            raise ValueError("Input text file is empty.")
        if reference is not None:
            if not reference.is_file():
                raise FileNotFoundError(f"Reference audio not found: {safe_path(reference)}")
            validate_reference(reference)
        existing = [path for path in (output, report) if path.exists()]
        if existing and not args.overwrite:
            raise FileExistsError("Refusing to overwrite: " + ", ".join(safe_path(path) for path in existing))
        required_files = {
            "verified first-class v1.5 GGUF": model_gguf,
            "tokenizer.json": tokenizer_dir / "tokenizer.json",
            "ONNX encoder": onnx_dir / "encoder.onnx",
            "ONNX encoder data": onnx_dir / "encoder.data",
            "ONNX decoder": onnx_dir / "decoder.onnx",
            "ONNX decoder data": onnx_dir / "decoder.data",
            "provenance": provenance_path,
            "llama-moss-tts": llama_bin,
            "official generation helper": build_ref_script,
            "official audio decoder helper": decode_script,
        }
        missing = [f"{name}: {safe_path(path)}" for name, path in required_files.items() if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                "Missing runtime files:\n  " + "\n  ".join(missing) + "\nRun setup_moss_tts_v15.ps1 explicitly."
            )
        provenance = read_json(provenance_path)
        model_revision, runtime_revision, quantization, model_gguf_sha256 = validate_provenance(provenance, model_gguf)
        actual_runtime_revision = git_revision(runtime)
        if actual_runtime_revision != runtime_revision:
            raise RuntimeError(
                "Runtime checkout revision does not match provenance; rebuild/re-prepare rather than reporting mixed identities."
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        before_gpu = gpu_telemetry()
        generation_started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="moss-tts-v15-") as temporary_dir:
            temporary_root = Path(temporary_dir)
            generation_ref = temporary_root / "generation.ref.bin"
            raw_codes = temporary_root / "raw.codes.bin"
            build_command = [
                sys.executable,
                str(build_ref_script),
                "--tokenizer-dir",
                str(tokenizer_dir),
                "--output-ref",
                str(generation_ref),
                "--language",
                args.language,
                "--text-file",
                str(text_path),
            ]
            if reference is not None:
                build_command.extend(
                    [
                        "--reference-audio",
                        str(reference),
                        "--encoder-onnx",
                        str(onnx_dir / "encoder.onnx"),
                        "--decoder-onnx",
                        str(onnx_dir / "decoder.onnx"),
                    ]
                )
                if args.device in ("cpu", "hybrid"):
                    build_command.append("--cpu-audio-encode")
            run_component(build_command, "official prompt/reference builder")

            inference_command = [
                str(llama_bin),
                "-m",
                str(model_gguf),
                "--generation-input",
                str(generation_ref),
                "--n-gpu-layers",
                str(args.gpu_layers),
                "--max-new-tokens",
                str(args.max_new_tokens),
                "--text-temperature",
                str(TEXT_TEMPERATURE),
                "--audio-temperature",
                str(AUDIO_TEMPERATURE),
                "--dump-raw-codes",
                str(raw_codes),
                "--audio-decoder-script",
                str(decode_script),
                "--audio-encoder-onnx",
                str(onnx_dir / "encoder.onnx"),
                "--audio-decoder-onnx",
                str(onnx_dir / "decoder.onnx"),
                "--wav-out",
                str(temporary_output),
                "--python-bin",
                sys.executable,
            ]
            if args.device in ("cpu", "hybrid"):
                inference_command.append("--audio-decoder-cpu")
            run_component(inference_command, "llama-moss-tts")

        generation_seconds = time.perf_counter() - generation_started
        duration, sample_rate, channels = inspect_wav(temporary_output)
        temporary_output.replace(output)
        after_gpu = gpu_telemetry()
        payload: dict[str, Any] = {
            "status": "completed",
            "runner": Path(__file__).name,
            "model_id": MODEL_ID,
            "model_revision": model_revision,
            "audio_tokenizer_id": CODEC_ID,
            "audio_tokenizer_revision": provenance.get("audio_tokenizer_revision"),
            "runtime": "openmoss-llama-cpp-firstclass",
            "runtime_repository": RUNTIME_ID,
            "runtime_revision": runtime_revision,
            "backend": "hybrid",
            "quantization": quantization,
            "model_gguf_sha256": model_gguf_sha256,
            "device_requested": args.device,
            "gpu_layers_requested": args.gpu_layers,
            "gpu_layers_effective": args.gpu_layers,
            "audio_codec_device": "cpu" if args.device in ("cpu", "hybrid") else "cuda",
            "language": args.language,
            "input_text_path": safe_path(text_path),
            "input_text_sha256": sha256_file(text_path),
            "reference_audio_used": reference is not None,
            "reference_audio_sha256": sha256_file(reference) if reference else None,
            "generation_seconds": generation_seconds,
            "audio_duration_seconds": duration,
            "real_time_factor": generation_seconds / duration,
            "sample_rate_hz": sample_rate,
            "channels": channels,
            "output_wav": safe_path(output),
            "generation_parameters": {
                "max_new_tokens": args.max_new_tokens,
                "text_temperature": TEXT_TEMPERATURE,
                "audio_temperature": AUDIO_TEMPERATURE,
                "text_passed_unchanged": True,
                "automatic_oom_retry": False,
            },
            "hardware": {
                "gpu_name": before_gpu["gpu_name"] or after_gpu["gpu_name"],
                "vram_total_mb": before_gpu["vram_total_mb"] or after_gpu["vram_total_mb"],
                "vram_used_before_mb": before_gpu["vram_used_mb"],
                "vram_used_after_mb": after_gpu["vram_used_mb"],
                "system_ram_mb": system_ram_mb(),
            },
            "post_processing": [],
        }
        write_report(report, payload)
        print(f"Created {safe_path(output)} and {safe_path(report)}")
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        if temporary_output.exists():
            temporary_output.unlink()
        if is_cuda_oom(exc):
            print(
                f"ERROR: CUDA memory was exhausted with --gpu-layers {args.gpu_layers}. "
                f"Retry manually with --gpu-layers {lower_layer_suggestions(args.gpu_layers)}.",
                file=sys.stderr,
            )
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

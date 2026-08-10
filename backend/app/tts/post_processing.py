"""Provider-neutral post-processing for completed PCM WAV narration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from .assembly import WavAssemblyError, inspect_pcm_wav
from .manifest import AudioParameters


_MIN_TEMPO = 0.5
_MAX_TEMPO = 2.0


class AudioPostProcessingError(ValueError):
    """Raised when requested narration post-processing cannot produce valid audio."""


@dataclass(frozen=True, slots=True)
class AudioPostProcessingResult:
    audio_bytes: bytes
    audio_parameters: AudioParameters
    tempo: float
    processor: str
    input_duration_seconds: float
    output_duration_seconds: float
    input_checksum: str
    output_checksum: str

    def evidence(self) -> dict[str, object]:
        return {
            "tempo": self.tempo,
            "processor": self.processor,
            "input_duration_seconds": self.input_duration_seconds,
            "output_duration_seconds": self.output_duration_seconds,
            "input_checksum": self.input_checksum,
            "output_checksum": self.output_checksum,
        }


def validate_tempo(value: object) -> float:
    """Return a finite FFmpeg atempo value without silently coercing invalid input."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Narration post_processing.tempo must be a number.")
    tempo = float(value)
    if not isfinite(tempo):
        raise ValueError("Narration post_processing.tempo must be finite.")
    if not _MIN_TEMPO <= tempo <= _MAX_TEMPO:
        raise ValueError(
            f"Narration post_processing.tempo must be between {_MIN_TEMPO} and {_MAX_TEMPO}."
        )
    return tempo


def tempo_from_voice_config(voice_config: Mapping[str, object] | None) -> float:
    """Read snake_case or camelCase post-processing config, defaulting to no-op."""
    config = dict(voice_config or {})
    value = config.get("post_processing", config.get("postProcessing"))
    if value is None:
        return 1.0
    if not isinstance(value, Mapping):
        raise ValueError("Narration post_processing must be an object.")
    return validate_tempo(value.get("tempo", 1.0))


def process_pcm_wav_tempo(
    audio_bytes: bytes,
    tempo: object = 1.0,
    *,
    process_runner: Callable[..., Any] | None = None,
    ffmpeg_locator: Callable[[str], str | None] | None = None,
) -> AudioPostProcessingResult:
    """Adjust one completed PCM WAV with FFmpeg atempo, or return it unchanged."""
    normalized_tempo = validate_tempo(tempo)
    try:
        input_parameters, _ = inspect_pcm_wav(audio_bytes)
    except WavAssemblyError as exc:
        raise AudioPostProcessingError(
            f"Narration source is not a valid PCM WAV: {exc}"
        ) from exc
    input_checksum = sha256(audio_bytes).hexdigest()
    if normalized_tempo == 1.0:
        return AudioPostProcessingResult(
            audio_bytes=audio_bytes,
            audio_parameters=input_parameters,
            tempo=normalized_tempo,
            processor="none",
            input_duration_seconds=input_parameters.duration_seconds,
            output_duration_seconds=input_parameters.duration_seconds,
            input_checksum=input_checksum,
            output_checksum=input_checksum,
        )

    locate = ffmpeg_locator or shutil.which
    ffmpeg = locate("ffmpeg")
    if not ffmpeg:
        raise AudioPostProcessingError(
            "FFmpeg is required for narration tempo post-processing when tempo != 1.0."
        )
    runner = process_runner or subprocess.run
    with tempfile.TemporaryDirectory(prefix="narration-tempo-") as directory:
        root = Path(directory)
        input_path = root / "source.wav"
        output_path = root / "processed.wav"
        input_path.write_bytes(audio_bytes)
        command = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-filter:a",
            f"atempo={normalized_tempo:g}",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
        try:
            completed = runner(command, capture_output=True, check=False)
        except OSError as exc:
            raise AudioPostProcessingError(f"Unable to execute FFmpeg: {exc}") from exc
        if getattr(completed, "returncode", 1) != 0:
            stderr = getattr(completed, "stderr", b"")
            detail = stderr.decode("utf-8", errors="replace").strip() if isinstance(stderr, bytes) else str(stderr).strip()
            suffix = f": {detail}" if detail else "."
            raise AudioPostProcessingError(f"FFmpeg narration tempo post-processing failed{suffix}")
        try:
            processed = output_path.read_bytes()
            output_parameters, _ = inspect_pcm_wav(processed)
        except (OSError, WavAssemblyError) as exc:
            raise AudioPostProcessingError("FFmpeg produced an invalid PCM WAV output.") from exc

    if output_parameters.channels != input_parameters.channels:
        raise AudioPostProcessingError("FFmpeg changed the narration channel count.")
    if output_parameters.sample_rate != input_parameters.sample_rate:
        raise AudioPostProcessingError("FFmpeg changed the narration sample rate.")
    if output_parameters.sample_width != 2:
        raise AudioPostProcessingError("FFmpeg narration output must be 16-bit PCM WAV audio.")
    expected_duration = input_parameters.duration_seconds / normalized_tempo
    tolerance = max(0.05, expected_duration * 0.05)
    if abs(output_parameters.duration_seconds - expected_duration) > tolerance:
        raise AudioPostProcessingError(
            "FFmpeg narration output duration does not match the requested tempo."
        )
    return AudioPostProcessingResult(
        audio_bytes=processed,
        audio_parameters=output_parameters,
        tempo=normalized_tempo,
        processor="ffmpeg_atempo",
        input_duration_seconds=input_parameters.duration_seconds,
        output_duration_seconds=output_parameters.duration_seconds,
        input_checksum=input_checksum,
        output_checksum=sha256(processed).hexdigest(),
    )

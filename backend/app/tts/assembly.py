"""Compatibility-checked PCM WAV concatenation without re-encoding."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import io
import os
from pathlib import Path
import tempfile
import wave

from .manifest import AudioParameters


class WavAssemblyError(ValueError):
    """Raised when a chunk is not a compatible uncompressed PCM WAV."""


@dataclass(frozen=True, slots=True)
class WavAssemblyResult:
    audio_bytes: bytes
    checksum: str
    duration_seconds: float
    audio_parameters: AudioParameters


def inspect_pcm_wav(audio_bytes: bytes) -> tuple[AudioParameters, bytes]:
    """Validate an uncompressed PCM WAV and return its parameters and frames."""
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as reader:
            parameters = AudioParameters(
                channels=reader.getnchannels(), sample_width=reader.getsampwidth(),
                sample_rate=reader.getframerate(), compression_type=reader.getcomptype(),
                frame_count=reader.getnframes(),
            )
            frames = reader.readframes(parameters.frame_count)
    except (EOFError, wave.Error) as exc:
        raise WavAssemblyError("Chunk is not a readable WAV file.") from exc
    if parameters.channels < 1 or parameters.sample_width < 1 or parameters.sample_rate < 1:
        raise WavAssemblyError("Chunk WAV has invalid audio parameters.")
    if parameters.compression_type != "NONE":
        raise WavAssemblyError("Chunk WAV must use uncompressed PCM audio.")
    expected_bytes = parameters.frame_count * parameters.channels * parameters.sample_width
    if len(frames) != expected_bytes:
        raise WavAssemblyError("Chunk WAV frame data is incomplete.")
    return parameters, frames


def assemble_pcm_wav(chunks: list[bytes] | tuple[bytes, ...], output_path: Path | None = None) -> WavAssemblyResult:
    """Concatenate compatible WAV frame data and optionally persist the result."""
    if not chunks:
        raise WavAssemblyError("At least one WAV chunk is required for assembly.")
    expected: AudioParameters | None = None
    frames: list[bytes] = []
    for index, chunk in enumerate(chunks):
        parameters, chunk_frames = inspect_pcm_wav(chunk)
        compatibility = (parameters.channels, parameters.sample_width, parameters.sample_rate, parameters.compression_type)
        if expected is None:
            expected = parameters
        elif compatibility != (expected.channels, expected.sample_width, expected.sample_rate, expected.compression_type):
            raise WavAssemblyError(f"Chunk {index} WAV parameters are incompatible with the preceding chunks.")
        frames.append(chunk_frames)
    assert expected is not None  # guarded by the non-empty input check
    all_frames = b"".join(frames)
    frame_count = sum(len(chunk_frames) // (expected.channels * expected.sample_width) for chunk_frames in frames)
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(expected.channels)
        writer.setsampwidth(expected.sample_width)
        writer.setframerate(expected.sample_rate)
        writer.writeframes(all_frames)
    audio_bytes = output.getvalue()
    parameters, _ = inspect_pcm_wav(audio_bytes)
    if parameters.frame_count != frame_count:
        raise WavAssemblyError("Assembled WAV frame count does not equal the chunk frame sum.")
    result = WavAssemblyResult(audio_bytes, sha256(audio_bytes).hexdigest(), parameters.duration_seconds, parameters)
    if output_path is not None:
        _write_validated_wav_atomically(output_path, result)
    return result


def persist_pcm_wav_atomically(audio_bytes: bytes, output_path: Path) -> WavAssemblyResult:
    """Validate and atomically publish one PCM WAV payload."""
    parameters, _ = inspect_pcm_wav(audio_bytes)
    result = WavAssemblyResult(
        audio_bytes=audio_bytes,
        checksum=sha256(audio_bytes).hexdigest(),
        duration_seconds=parameters.duration_seconds,
        audio_parameters=parameters,
    )
    _write_validated_wav_atomically(output_path, result)
    return result


def _write_validated_wav_atomically(output_path: Path, result: WavAssemblyResult) -> None:
    """Publish a complete, validated WAV without exposing a partial output."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp", delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(result.audio_bytes)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path = Path(temporary_name)
        persisted = temporary_path.read_bytes()
        parameters, _ = inspect_pcm_wav(persisted)
        if (
            sha256(persisted).hexdigest() != result.checksum
            or parameters != result.audio_parameters
            or parameters.frame_count != result.audio_parameters.frame_count
        ):
            raise WavAssemblyError("Temporary assembled WAV does not match its expected evidence.")
        temporary_path.replace(output_path)
    except OSError as exc:
        raise WavAssemblyError(f"Unable to atomically persist WAV output: {output_path}") from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass

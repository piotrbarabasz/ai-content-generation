"""Deterministic, provider-neutral benchmark reports for TTS synthesis."""

from __future__ import annotations

from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path

from .manifest import SynthesisManifest


_PRECISION = 6


@dataclass(frozen=True, slots=True)
class TTSBenchmarkReport:
    """Stable benchmark evidence derived from a completed or failed manifest."""

    provider: str
    model: str
    device: str
    language: str
    word_count: int
    chunk_count: int
    generation_wall_time_seconds: float
    audio_duration_seconds: float
    real_time_factor: float | None
    sample_rate: int | None
    output_checksum: str | None
    failed_chunk_ids: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        """Return JSON-compatible fields in the benchmark report contract."""
        return {
            "provider": self.provider,
            "model": self.model,
            "device": self.device,
            "language": self.language,
            "word_count": self.word_count,
            "chunk_count": self.chunk_count,
            "generation_wall_time_seconds": self.generation_wall_time_seconds,
            "audio_duration_seconds": self.audio_duration_seconds,
            "real_time_factor": self.real_time_factor,
            "sample_rate": self.sample_rate,
            "output_checksum": self.output_checksum,
            "failed_chunk_ids": list(self.failed_chunk_ids),
        }

    def save(self, path: Path) -> None:
        """Write a deterministic JSON artifact without adding a timestamp."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def build_benchmark_report(
    manifest: SynthesisManifest,
    *,
    provider: str,
    model: str,
    device: str,
    language: str,
    word_count: int,
    generation_wall_time_seconds: float,
) -> TTSBenchmarkReport:
    """Build benchmark evidence by wrapping existing synthesis-manifest data."""
    if word_count < 0:
        raise ValueError("word_count must not be negative.")
    if not isfinite(generation_wall_time_seconds) or generation_wall_time_seconds < 0:
        raise ValueError("generation_wall_time_seconds must be a finite non-negative value.")

    duration = manifest.final_duration_seconds or 0.0
    if not isfinite(duration) or duration < 0:
        raise ValueError("manifest final_duration_seconds must be finite and non-negative.")
    rounded_duration = round(duration, _PRECISION)
    rounded_generation = round(generation_wall_time_seconds, _PRECISION)
    rtf = round(generation_wall_time_seconds / duration, _PRECISION) if duration > 0 else None
    parameters = manifest.final_audio_parameters
    return TTSBenchmarkReport(
        provider=provider,
        model=model,
        device=device,
        language=language,
        word_count=word_count,
        chunk_count=len(manifest.chunks),
        generation_wall_time_seconds=rounded_generation,
        audio_duration_seconds=rounded_duration,
        real_time_factor=rtf,
        sample_rate=parameters.sample_rate if parameters else None,
        output_checksum=manifest.final_checksum,
        failed_chunk_ids=tuple(sorted(manifest.failed_chunk_ids)),
    )

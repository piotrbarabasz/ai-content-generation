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
    voice_mode: str | None
    word_count: int
    chunk_count: int
    generation_wall_time_seconds: float
    audio_duration_seconds: float
    real_time_factor: float | None
    sample_rate: int | None
    output_checksum: str | None
    failed_chunk_ids: tuple[str, ...]
    generated_chunk_count: int
    reused_chunk_count: int
    failed_chunk_count: int
    full_cache_hit: bool

    def to_payload(self) -> dict[str, object]:
        """Return JSON-compatible fields in the benchmark report contract."""
        return {
            "provider": self.provider,
            "model": self.model,
            "device": self.device,
            "language": self.language,
            "voice_mode": self.voice_mode,
            "word_count": self.word_count,
            "chunk_count": self.chunk_count,
            "generation_wall_time_seconds": self.generation_wall_time_seconds,
            "audio_duration_seconds": self.audio_duration_seconds,
            "real_time_factor": self.real_time_factor,
            "sample_rate": self.sample_rate,
            "output_checksum": self.output_checksum,
            "failed_chunk_ids": list(self.failed_chunk_ids),
            "generated_chunk_count": self.generated_chunk_count,
            "reused_chunk_count": self.reused_chunk_count,
            "failed_chunk_count": self.failed_chunk_count,
            "full_cache_hit": self.full_cache_hit,
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
    provider: str | None = None,
    model: str | None = None,
    device: str | None = None,
    language: str | None = None,
    word_count: int,
    generation_wall_time_seconds: float,
) -> TTSBenchmarkReport:
    """Build benchmark evidence from the recorded effective identity and run."""
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
    identity = manifest.effective_synthesis_identity
    voice = identity.get("voice") if isinstance(identity.get("voice"), dict) else {}
    # Legacy manifests did not persist an effective identity.  Keep their
    # direct callers working while all new synthesis evidence is manifest-led.
    effective_provider = _identity_text(identity, "provider") or provider or "unknown"
    effective_model = _identity_text(identity, "model_variant", "model") or model or "unknown"
    effective_device = _identity_text(identity, "device") or device or "unknown"
    effective_language = _identity_text(identity, "language_id", "language") or language or "unknown"
    voice_mode = _identity_text(voice, "mode")
    failed_chunk_ids = tuple(sorted(manifest.failed_chunk_ids))
    return TTSBenchmarkReport(
        provider=effective_provider,
        model=effective_model,
        device=effective_device,
        language=effective_language,
        voice_mode=voice_mode,
        word_count=word_count,
        chunk_count=len(manifest.chunks),
        generation_wall_time_seconds=rounded_generation,
        audio_duration_seconds=rounded_duration,
        real_time_factor=rtf,
        sample_rate=parameters.sample_rate if parameters else None,
        output_checksum=manifest.final_checksum,
        failed_chunk_ids=failed_chunk_ids,
        generated_chunk_count=manifest.generated_chunk_count,
        reused_chunk_count=manifest.reused_chunk_count,
        failed_chunk_count=manifest.failed_chunk_count,
        full_cache_hit=(
            manifest.chunk_count > 0
            and manifest.generated_chunk_count == 0
            and manifest.reused_chunk_count == manifest.chunk_count
            and manifest.failed_chunk_count == 0
        ),
    )


def _identity_text(identity: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = identity.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None

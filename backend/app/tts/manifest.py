"""Persistent, provider-neutral manifests for resumable TTS chunk work."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


def stable_hash(value: object) -> str:
    """Return a stable SHA-256 identity for JSON-compatible configuration."""
    payload = json.dumps(value, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def relative_reference(path: Path, root: Path) -> str:
    """Return a portable reference and reject paths outside the configured root."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("TTS artifact path must be inside its configured runtime root.") from exc


@dataclass(frozen=True, slots=True)
class AudioParameters:
    """PCM WAV parameters recorded for every synthesized chunk."""

    channels: int
    sample_width: int
    sample_rate: int
    compression_type: str
    frame_count: int

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.sample_rate if self.sample_rate else 0.0

    def to_payload(self) -> dict[str, object]:
        return {
            "channels": self.channels,
            "sample_width": self.sample_width,
            "sample_rate": self.sample_rate,
            "compression_type": self.compression_type,
            "frame_count": self.frame_count,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "AudioParameters":
        return cls(
            channels=int(payload["channels"]),
            sample_width=int(payload["sample_width"]),
            sample_rate=int(payload["sample_rate"]),
            compression_type=str(payload["compression_type"]),
            frame_count=int(payload["frame_count"]),
        )


@dataclass(slots=True)
class ChunkManifest:
    """The resume record for one stable technical narration chunk."""

    chunk_id: str
    index: int
    status: str
    input_hash: str
    config_hash: str
    text_hash: str
    wav_checksum: str | None = None
    duration_seconds: float | None = None
    audio_parameters: AudioParameters | None = None
    artifact_ref: str | None = None
    attempts: int = 0
    error: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk_id,
            "index": self.index,
            "status": self.status,
            "input_hash": self.input_hash,
            "config_hash": self.config_hash,
            "text_hash": self.text_hash,
            "wav_checksum": self.wav_checksum,
            "duration_seconds": self.duration_seconds,
            "audio_parameters": self.audio_parameters.to_payload() if self.audio_parameters else None,
            "artifact_ref": self.artifact_ref,
            "attempts": self.attempts,
            "error": self.error,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ChunkManifest":
        audio = payload.get("audio_parameters")
        return cls(
            chunk_id=str(payload["chunk_id"]), index=int(payload["index"]), status=str(payload["status"]),
            input_hash=str(payload["input_hash"]), config_hash=str(payload["config_hash"]),
            text_hash=str(payload["text_hash"]), wav_checksum=_optional_str(payload.get("wav_checksum")),
            duration_seconds=float(payload["duration_seconds"]) if payload.get("duration_seconds") is not None else None,
            audio_parameters=AudioParameters.from_payload(audio) if isinstance(audio, Mapping) else None,
            artifact_ref=_optional_str(payload.get("artifact_ref")), attempts=int(payload.get("attempts", 0)),
            error=_optional_str(payload.get("error")),
        )


@dataclass(slots=True)
class SynthesisManifest:
    """The JSON sidecar for one resumable narration synthesis run."""

    config_hash: str
    chunks: dict[str, ChunkManifest] = field(default_factory=dict)
    final_status: str = "pending"
    final_artifact_ref: str | None = None
    final_checksum: str | None = None
    final_duration_seconds: float | None = None
    final_audio_parameters: AudioParameters | None = None
    failed_chunk_ids: list[str] = field(default_factory=list)
    schema_version: int = 1

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "config_hash": self.config_hash,
            # This is derived instead of persisted as mutable state so the
            # manifest can never report records removed during a resumed run.
            "chunk_count": self.chunk_count,
            "chunks": [self.chunks[key].to_payload() for key in sorted(self.chunks, key=lambda key: self.chunks[key].index)],
            "final_status": self.final_status,
            "final_artifact_ref": self.final_artifact_ref,
            "final_checksum": self.final_checksum,
            "final_duration_seconds": self.final_duration_seconds,
            "final_audio_parameters": self.final_audio_parameters.to_payload() if self.final_audio_parameters else None,
            "failed_chunk_ids": list(self.failed_chunk_ids),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SynthesisManifest":
        records = payload.get("chunks", [])
        chunks = {
            record.chunk_id: record
            for item in records if isinstance(item, Mapping)
            for record in (ChunkManifest.from_payload(item),)
        }
        audio = payload.get("final_audio_parameters")
        return cls(
            config_hash=str(payload["config_hash"]), chunks=chunks,
            final_status=str(payload.get("final_status", "pending")),
            final_artifact_ref=_optional_str(payload.get("final_artifact_ref")),
            final_checksum=_optional_str(payload.get("final_checksum")),
            final_duration_seconds=float(payload["final_duration_seconds"]) if payload.get("final_duration_seconds") is not None else None,
            final_audio_parameters=AudioParameters.from_payload(audio) if isinstance(audio, Mapping) else None,
            failed_chunk_ids=[str(value) for value in payload.get("failed_chunk_ids", [])],
            schema_version=int(payload.get("schema_version", 1)),
        )

    @property
    def chunk_count(self) -> int:
        """Return the number of chunk records currently in this manifest."""
        return len(self.chunks)

    @classmethod
    def load(cls, path: Path, *, config_hash: str) -> "SynthesisManifest":
        if not path.exists():
            return cls(config_hash=config_hash)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("manifest root must be an object")
            manifest = cls.from_payload(payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid TTS synthesis manifest: {path}") from exc
        return manifest if manifest.config_hash == config_hash else cls(config_hash=config_hash)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.to_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None

"""Persistent, provider-neutral manifests for resumable TTS chunk work."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
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
    # These fields describe one invocation, rather than cumulative work kept
    # in the reusable chunk records.  They are reset when a new invocation
    # starts so a resumed run can report what it actually did.
    effective_synthesis_identity: dict[str, Any] = field(default_factory=dict)
    generated_chunk_count: int = 0
    reused_chunk_count: int = 0
    failed_chunk_count: int = 0
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
            "effective_synthesis_identity": self.effective_synthesis_identity,
            "generated_chunk_count": self.generated_chunk_count,
            "reused_chunk_count": self.reused_chunk_count,
            "failed_chunk_count": self.failed_chunk_count,
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
            effective_synthesis_identity=sanitize_synthesis_identity(
                payload.get("effective_synthesis_identity", {})
            ),
            generated_chunk_count=int(payload.get("generated_chunk_count", 0)),
            reused_chunk_count=int(payload.get("reused_chunk_count", 0)),
            failed_chunk_count=int(payload.get("failed_chunk_count", 0)),
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
        payload = json.dumps(self.to_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        # Parse the exact data before replacing an existing manifest.  A
        # uniquely named sibling temp file avoids exposing a truncated JSON
        # sidecar if the process is interrupted mid-write.
        json.loads(payload)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            Path(temporary_name).replace(path)
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def sanitize_synthesis_identity(identity: object) -> dict[str, Any]:
    """Return a JSON-compatible provider identity without local path details."""
    if not isinstance(identity, Mapping):
        raise ValueError("TTS provider effective synthesis identity must be a mapping.")

    hidden = object()

    def sanitize(value: object, *, key: str = "") -> object:
        normalized_key = key.lower()
        if normalized_key == "path" or normalized_key.endswith("_path"):
            return hidden
        if isinstance(value, Path):
            return hidden
        if isinstance(value, Mapping):
            return {
                str(item_key): clean
                for item_key, item_value in value.items()
                if (clean := sanitize(item_value, key=str(item_key))) is not hidden
            }
        if isinstance(value, (list, tuple)):
            return [clean for item in value if (clean := sanitize(item)) is not hidden]
        if isinstance(value, str):
            # Paths may originate on a different operating system, so handle
            # both native and Windows drive-qualified forms without resolving
            # or touching the provider's local files.
            candidate = Path(value)
            if candidate.is_absolute() or (len(value) > 2 and value[1:3] in (":\\", ":/")):
                return hidden
            return value
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)

    sanitized = sanitize(identity)
    assert isinstance(sanitized, dict)
    # Verify exactly the form that will be persisted is JSON-compatible.
    json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sanitized

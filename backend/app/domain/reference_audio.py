"""Immutable reference-audio measurements and append-only approval decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re


_SHA256 = re.compile(r"[0-9a-f]{64}")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}")


def _text(value, label):
    if type(value) is not str or not value.strip():
        raise ValueError(f"Reference audio {label} is required.")


@dataclass(frozen=True, slots=True)
class ReferenceAudioSource:
    artifact_id: str
    project_id: str
    source_name: str
    checksum: str
    size_bytes: int
    duration_seconds: float
    sample_rate: int
    channels: int
    sample_width: int

    def __post_init__(self):
        for name in ("artifact_id", "project_id", "source_name"):
            _text(getattr(self, name), name)
        if any(character in self.source_name for character in "/\\:"):
            raise ValueError("Reference audio source name must not expose a path.")
        if _SHA256.fullmatch(self.checksum) is None:
            raise ValueError("Reference audio checksum must be SHA-256.")
        if (type(self.size_bytes) is not int or self.size_bytes <= 0
                or not isinstance(self.duration_seconds, (int, float)) or self.duration_seconds <= 0
                or any(type(value) is not int or value <= 0
                       for value in (self.sample_rate, self.channels, self.sample_width))):
            raise ValueError("Reference audio measurements are invalid.")

    def to_payload(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_manifest(cls, manifest):
        if manifest.artifact_type != "reference_audio_source":
            raise ValueError("Expected a reference-audio source artifact.")
        value = dict(manifest.metadata["reference_audio"])
        if value.pop("version", None) != 1:
            raise ValueError("Unsupported reference-audio source version.")
        return cls(artifact_id=manifest.artifact_id, checksum=manifest.checksum,
                   size_bytes=manifest.size_bytes, **value)


@dataclass(frozen=True, slots=True)
class ReferenceAudioDecision:
    id: str
    project_id: str
    reference_id: str
    source_checksum: str
    status: str
    label: str
    parent_decision_id: str | None = None

    def __post_init__(self):
        for name in ("id", "project_id", "reference_id"):
            _text(getattr(self, name), name)
        if _SHA256.fullmatch(self.source_checksum) is None:
            raise ValueError("Reference approval must bind the source checksum.")
        if self.status not in {"approved", "rejected"}:
            raise ValueError("Reference audio decision must be approved or rejected.")
        if type(self.label) is not str or _LABEL.fullmatch(self.label.strip()) is None:
            raise ValueError("Reference audio decision requires a safe approval label.")
        if self.parent_decision_id is not None:
            _text(self.parent_decision_id, "parent decision id")
            if self.parent_decision_id == self.id:
                raise ValueError("Reference audio decision cannot be its own parent.")

    def to_payload(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_payload(cls, value):
        data = dict(value)
        if data.pop("version", None) != 1:
            raise ValueError("Unsupported reference-audio decision version.")
        return cls(**data)


@dataclass(frozen=True, slots=True)
class ApprovedReferenceAudioChoice:
    artifact_id: str
    source_name: str
    checksum: str
    approval_label: str
    duration_seconds: float

    def metadata(self):
        return {"checksum": self.checksum, "approval_label": self.approval_label, "approved": True}

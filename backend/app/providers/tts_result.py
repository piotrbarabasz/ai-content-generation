"""Provider-neutral TTS synthesis result model."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar

from app.domain.types import JsonDict


def _coerce_audio_bytes(value: bytes | bytearray | memoryview | None) -> bytes:
    if value is None:
        raise ValueError("TTSSynthesisResult audio_bytes are required.")
    if isinstance(value, bytes):
        audio_bytes = value
    elif isinstance(value, (bytearray, memoryview)):
        audio_bytes = bytes(value)
    else:
        raise TypeError("TTSSynthesisResult audio_bytes must be bytes-like.")
    if not audio_bytes:
        raise ValueError("TTSSynthesisResult audio_bytes cannot be empty.")
    return audio_bytes


def _coerce_sample_rate(value: Any) -> int:
    try:
        sample_rate = int(value)
    except (TypeError, ValueError) as exc:
        raise TypeError("TTSSynthesisResult sample_rate must be an integer.") from exc
    if sample_rate <= 0:
        raise ValueError("TTSSynthesisResult sample_rate must be positive.")
    return sample_rate


def _coerce_duration_seconds(value: Any) -> float:
    try:
        duration_seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError("TTSSynthesisResult duration_seconds must be numeric.") from exc
    if duration_seconds < 0:
        raise ValueError("TTSSynthesisResult duration_seconds cannot be negative.")
    return duration_seconds


def _normalize_audio_format(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("TTSSynthesisResult audio_format must be a string.")
    audio_format = value.strip().lower()
    if audio_format.startswith("."):
        audio_format = audio_format[1:]
    audio_format = {"wave": "wav"}.get(audio_format, audio_format)
    if not audio_format:
        raise ValueError("TTSSynthesisResult audio_format is required.")
    return audio_format


def _coerce_provider_name(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("TTSSynthesisResult provider_name must be a string.")
    provider_name = value.strip()
    if not provider_name:
        raise ValueError("TTSSynthesisResult provider_name is required.")
    return provider_name


def _coerce_metadata(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise TypeError("TTSSynthesisResult metadata must be a mapping.")
    return MappingProxyType(deepcopy(dict(value)))


@dataclass(slots=True, frozen=True)
class TTSSynthesisResult:
    """Immutable provider-neutral synthesis payload."""

    SUPPORTED_AUDIO_FORMATS: ClassVar[frozenset[str]] = frozenset({"aac", "flac", "m4a", "mp3", "ogg", "wav"})

    audio_bytes: bytes = field(repr=False)
    sample_rate: int = 0
    duration_seconds: float = 0.0
    audio_format: str = "wav"
    provider_name: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=True, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "audio_bytes", _coerce_audio_bytes(self.audio_bytes))
        object.__setattr__(self, "sample_rate", _coerce_sample_rate(self.sample_rate))
        object.__setattr__(self, "duration_seconds", _coerce_duration_seconds(self.duration_seconds))
        object.__setattr__(self, "audio_format", _normalize_audio_format(self.audio_format))
        object.__setattr__(self, "provider_name", _coerce_provider_name(self.provider_name))
        if self.audio_format not in self.SUPPORTED_AUDIO_FORMATS:
            supported = ", ".join(sorted(self.SUPPORTED_AUDIO_FORMATS))
            raise ValueError(
                f"TTSSynthesisResult audio_format must be one of: {supported}."
            )
        object.__setattr__(self, "metadata", _coerce_metadata(self.metadata))

    def to_payload(self) -> JsonDict:
        """Return a JSON-friendly copy of the synthesis result."""

        return {
            "audio_bytes": bytes(self.audio_bytes),
            "sample_rate": self.sample_rate,
            "duration_seconds": self.duration_seconds,
            "audio_format": self.audio_format,
            "provider_name": self.provider_name,
            "metadata": deepcopy(dict(self.metadata)),
        }

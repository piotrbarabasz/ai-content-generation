"""Typed configuration for TTS provider composition."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class TTSSettingsError(ValueError):
    """Raised when TTS-specific provider settings are invalid."""


_PROVIDERS = frozenset({"mock", "chatterbox_v3"})
_FIELDS = frozenset(
    {
        "provider",
        "device",
        "language_id",
        "model_variant",
        "audio_prompt_path",
        "exaggeration",
        "cfg_weight",
        "temperature",
        "repetition_penalty",
        "min_p",
        "top_p",
    }
)
_NUMERIC_FIELDS = frozenset(
    {"exaggeration", "cfg_weight", "temperature", "repetition_penalty", "min_p", "top_p"}
)


@dataclass(frozen=True, slots=True)
class TTSSettings:
    """Validated settings shared by the supported TTS provider constructors."""

    provider: str = "mock"
    device: str = "cpu"
    language_id: str | None = None
    model_variant: str = "v3"
    audio_prompt_path: str | Path | None = None
    exaggeration: float | None = None
    cfg_weight: float | None = None
    temperature: float | None = None
    repetition_penalty: float | None = None
    min_p: float | None = None
    top_p: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or self.provider not in _PROVIDERS:
            raise TTSSettingsError("Unsupported TTS provider; use 'mock' or 'chatterbox_v3'.")
        if not isinstance(self.device, str) or not self.device.strip():
            raise TTSSettingsError("TTS device must be a non-empty string.")
        if self.language_id is not None and not isinstance(self.language_id, str):
            raise TTSSettingsError("TTS language_id must be a string or null.")
        if self.model_variant != "v3":
            raise TTSSettingsError("TTS model_variant must be 'v3'.")
        if self.audio_prompt_path is not None and not isinstance(self.audio_prompt_path, (str, Path)):
            raise TTSSettingsError("TTS audio_prompt_path must be a path string or null.")
        for field_name in _NUMERIC_FIELDS:
            value = getattr(self, field_name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise TTSSettingsError(f"TTS {field_name} must be numeric or null.")

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any] | None,
        *,
        provider: str = "mock",
    ) -> "TTSSettings":
        """Create settings from ProviderConfig settings without accepting unknown keys."""

        if values is None:
            values = {}
        if not isinstance(values, Mapping):
            raise TTSSettingsError("TTS provider settings must be an object.")
        unknown = sorted(set(values) - _FIELDS)
        if unknown:
            raise TTSSettingsError(f"Unknown TTS provider settings: {', '.join(unknown)}.")
        configured_provider = values.get("provider", provider)
        if configured_provider != provider:
            raise TTSSettingsError("TTS settings provider must match ProviderConfig provider_name.")
        return cls(provider=configured_provider, **{key: value for key, value in values.items() if key != "provider"})

"""Typed configuration for TTS provider composition."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class TTSSettingsError(ValueError):
    """Raised when TTS-specific provider settings are invalid."""


_PROVIDERS = frozenset({"mock", "chatterbox_v3", "piper"})
_FIELDS = frozenset(
    {
        "provider",
        "usage_policy",
        "device",
        "language_id",
        "model_variant",
        "audio_prompt_path",
        "model_key",
        "model_path",
        "length_scale",
        "volume",
        "noise_scale",
        "noise_w_scale",
        "exaggeration",
        "cfg_weight",
        "temperature",
        "repetition_penalty",
        "min_p",
        "top_p",
    }
)
_NUMERIC_FIELDS = frozenset(
    {
        "exaggeration",
        "cfg_weight",
        "temperature",
        "repetition_penalty",
        "min_p",
        "top_p",
        "length_scale",
        "volume",
        "noise_scale",
        "noise_w_scale",
    }
)

_PIPER_POSITIVE_FIELDS = frozenset({"length_scale", "volume"})
_PIPER_NON_NEGATIVE_FIELDS = frozenset({"noise_scale", "noise_w_scale"})


@dataclass(frozen=True, slots=True)
class TTSSettings:
    """Validated settings shared by the supported TTS provider constructors."""

    provider: str = "mock"
    usage_policy: str = "production"
    device: str = "cpu"
    language_id: str | None = None
    model_variant: str = "v3"
    audio_prompt_path: str | Path | None = None
    model_key: str | None = None
    model_path: str | Path | None = None
    length_scale: float | None = None
    volume: float | None = None
    noise_scale: float | None = None
    noise_w_scale: float | None = None
    exaggeration: float | None = None
    cfg_weight: float | None = None
    temperature: float | None = None
    repetition_penalty: float | None = None
    min_p: float | None = None
    top_p: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or self.provider not in _PROVIDERS:
            raise TTSSettingsError("Unsupported TTS provider; use 'mock', 'chatterbox_v3' or 'piper'.")
        if not isinstance(self.usage_policy, str):
            raise TTSSettingsError("TTS usage_policy must be a string.")
        normalized_usage_policy = self.usage_policy.strip().lower()
        if normalized_usage_policy not in {"production", "evaluation_only"}:
            raise TTSSettingsError(
                "TTS usage_policy must be 'production' or 'evaluation_only'."
            )
        object.__setattr__(self, "usage_policy", normalized_usage_policy)
        if not isinstance(self.device, str) or not self.device.strip():
            raise TTSSettingsError("TTS device must be a non-empty string.")
        if self.language_id is not None and not isinstance(self.language_id, str):
            raise TTSSettingsError("TTS language_id must be a string or null.")
        if self.model_variant != "v3":
            raise TTSSettingsError("TTS model_variant must be 'v3'.")
        if self.audio_prompt_path is not None and not isinstance(self.audio_prompt_path, (str, Path)):
            raise TTSSettingsError("TTS audio_prompt_path must be a path string or null.")
        if self.provider == "piper":
            if self.audio_prompt_path is not None:
                raise TTSSettingsError("TTS audio_prompt_path is not supported by Piper.")
            if (self.model_key is None) == (self.model_path is None):
                raise TTSSettingsError(
                    "Piper TTS settings must include exactly one of model_key or model_path."
                )
            if self.model_key is not None:
                if not isinstance(self.model_key, str) or not self.model_key.strip():
                    raise TTSSettingsError("Piper model_key must be a non-empty string.")
                object.__setattr__(self, "model_key", self.model_key.strip())
            if self.model_path is not None:
                if not isinstance(self.model_path, (str, Path)):
                    raise TTSSettingsError("Piper model_path must be a path string or null.")
                model_path = Path(self.model_path)
                if not model_path.is_file():
                    raise TTSSettingsError("Piper model_path must point to an existing file.")
                object.__setattr__(self, "model_path", model_path)
            for field_name in ("length_scale", "volume", "noise_scale", "noise_w_scale"):
                value = getattr(self, field_name)
                if value is not None:
                    _validate_piper_numeric(field_name, value)
        else:
            if self.model_key is not None or self.model_path is not None:
                raise TTSSettingsError("TTS model_key and model_path are only supported by Piper.")
            if self.audio_prompt_path is not None and self.provider != "chatterbox_v3":
                raise TTSSettingsError("TTS audio_prompt_path is only supported by Chatterbox V3.")
            for field_name in ("length_scale", "volume", "noise_scale", "noise_w_scale"):
                if getattr(self, field_name) is not None:
                    raise TTSSettingsError(f"TTS {field_name} is only supported by Piper.")
        for field_name in _NUMERIC_FIELDS:
            value = getattr(self, field_name)
            if value is not None:
                _validate_numeric_value(field_name, value)

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


def _validate_numeric_value(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TTSSettingsError(f"TTS {field_name} must be numeric or null.")


def _validate_piper_numeric(field_name: str, value: object) -> None:
    _validate_numeric_value(field_name, value)
    if field_name in _PIPER_POSITIVE_FIELDS and float(value) <= 0:
        raise TTSSettingsError(f"Piper {field_name} must be greater than zero.")
    if field_name in _PIPER_NON_NEGATIVE_FIELDS and float(value) < 0:
        raise TTSSettingsError(f"Piper {field_name} must be greater than or equal to zero.")

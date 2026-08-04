"""Provider capability and usage-policy contracts for TTS providers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domain.types import JsonDict


class TTSCapabilityError(ValueError):
    """Raised when a TTS request is incompatible with provider capabilities."""


_SUPPORTED_USAGE_POLICIES = frozenset({"production", "evaluation_only"})
_LANGUAGE_KEYS = ("language_id", "languageId", "language")
_VOICE_MODE_KEYS = ("voice_mode", "voiceMode")
_REFERENCE_AUDIO_KEYS = ("audio_prompt_path", "audioPromptPath")
_SPEAKING_RATE_KEYS = ("speaking_rate", "speakingRate", "rate", "speed")


def _normalize_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TTSCapabilityError(f"TTS {field_name} must be a string.")
    normalized = value.strip().lower()
    if not normalized:
        raise TTSCapabilityError(f"TTS {field_name} is required.")
    return normalized


def _normalize_options(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    normalized = tuple(sorted(dict.fromkeys(_normalize_text(value, field_name=field_name) for value in values)))
    if not normalized:
        raise TTSCapabilityError(f"TTS {field_name} cannot be empty.")
    return normalized


def _normalize_usage_policy(value: Any) -> str:
    normalized = _normalize_text(value, field_name="usage_policy")
    if normalized not in _SUPPORTED_USAGE_POLICIES:
        supported = ", ".join(sorted(_SUPPORTED_USAGE_POLICIES))
        raise TTSCapabilityError(f"TTS usage_policy must be one of: {supported}.")
    return normalized


def _resolve_config_value(
    voice_config: Mapping[str, Any] | None,
    keys: tuple[str, ...],
) -> Any | None:
    if voice_config is None:
        return None
    for key in keys:
        if key not in voice_config:
            continue
        value = voice_config[key]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def resolve_language_id(
    voice_config: Mapping[str, Any] | None,
    *,
    default_language_id: str | None = None,
) -> str | None:
    """Resolve the effective language identifier from a request payload."""

    value = _resolve_config_value(voice_config, _LANGUAGE_KEYS)
    if value is None:
        if default_language_id is None:
            return None
        return _normalize_text(default_language_id, field_name="language_id")
    return _normalize_text(value, field_name="language_id")


def resolve_voice_mode(
    voice_config: Mapping[str, Any] | None,
    *,
    default_voice_mode: str,
) -> str:
    """Resolve the effective voice mode from a request payload."""

    value = _resolve_config_value(voice_config, _VOICE_MODE_KEYS)
    if value is None:
        return _normalize_text(default_voice_mode, field_name="voice_mode")
    return _normalize_text(value, field_name="voice_mode")


def request_uses_speaking_rate(voice_config: Mapping[str, Any] | None) -> bool:
    """Return whether a request asks for speaking-rate control."""

    return _resolve_config_value(voice_config, _SPEAKING_RATE_KEYS) is not None


def request_uses_reference_audio(voice_config: Mapping[str, Any] | None) -> bool:
    """Return whether a request includes a reference-audio input."""

    return _resolve_config_value(voice_config, _REFERENCE_AUDIO_KEYS) is not None


@dataclass(frozen=True, slots=True)
class TTSCapabilities:
    """Deterministic capability metadata for a TTS provider."""

    provider_name: str
    supported_languages: tuple[str, ...]
    voice_modes: tuple[str, ...]
    reference_audio_required: bool
    speaking_rate_supported: bool
    usage_policy: str = "production"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_name",
            _normalize_text(self.provider_name, field_name="provider_name"),
        )
        object.__setattr__(
            self,
            "supported_languages",
            _normalize_options(self.supported_languages, field_name="supported_languages"),
        )
        object.__setattr__(
            self,
            "voice_modes",
            _normalize_options(self.voice_modes, field_name="voice_modes"),
        )
        object.__setattr__(
            self,
            "usage_policy",
            _normalize_usage_policy(self.usage_policy),
        )
        if not isinstance(self.reference_audio_required, bool):
            raise TTSCapabilityError("TTS reference_audio_required must be a boolean.")
        if not isinstance(self.speaking_rate_supported, bool):
            raise TTSCapabilityError("TTS speaking_rate_supported must be a boolean.")
        if self.reference_audio_required and "reference" not in self.voice_modes:
            raise TTSCapabilityError(
                "TTS reference_audio_required cannot be true without a reference voice mode."
            )

    def to_payload(self) -> JsonDict:
        """Return JSON-friendly capability metadata."""

        return {
            "provider_name": self.provider_name,
            "supported_languages": list(self.supported_languages),
            "voice_modes": list(self.voice_modes),
            "reference_audio_required": self.reference_audio_required,
            "speaking_rate_supported": self.speaking_rate_supported,
            "usage_policy": self.usage_policy,
        }

    def supports_language(self, language_id: str | None) -> bool:
        """Return whether the provider supports a language identifier."""

        if language_id is None:
            return True
        normalized = _normalize_text(language_id, field_name="language_id")
        return "*" in self.supported_languages or normalized in self.supported_languages

    def supports_voice_mode(self, voice_mode: str | None) -> bool:
        """Return whether the provider supports a voice mode."""

        if voice_mode is None:
            return True
        normalized = _normalize_text(voice_mode, field_name="voice_mode")
        return "*" in self.voice_modes or normalized in self.voice_modes

    def allows_usage_policy(self, usage_policy: str | None) -> bool:
        """Return whether the provider can be used in a deployment policy mode."""

        if usage_policy is None:
            return True
        normalized = _normalize_usage_policy(usage_policy)
        return normalized == "evaluation_only" or self.usage_policy == "production"

    def validate_request(
        self,
        *,
        language_id: str | None = None,
        voice_mode: str | None = None,
        reference_audio_present: bool = False,
        speaking_rate_requested: bool = False,
        usage_policy: str | None = None,
    ) -> None:
        """Raise a provider-neutral error when a request exceeds the capability contract."""

        if not self.allows_usage_policy(usage_policy):
            raise TTSCapabilityError(
                f"TTS provider '{self.provider_name}' is evaluation-only and cannot be used "
                "in production mode."
            )
        if not self.supports_language(language_id):
            supported = ", ".join(self.supported_languages)
            raise TTSCapabilityError(
                f"TTS provider '{self.provider_name}' does not support language_id "
                f"'{_normalize_text(language_id, field_name='language_id')}'. Supported languages: {supported}."
            )
        if not self.supports_voice_mode(voice_mode):
            supported = ", ".join(self.voice_modes)
            raise TTSCapabilityError(
                f"TTS provider '{self.provider_name}' does not support voice mode "
                f"'{_normalize_text(voice_mode, field_name='voice_mode')}'. Supported voice modes: {supported}."
            )
        if self.reference_audio_required and not reference_audio_present:
            raise TTSCapabilityError(
                f"TTS provider '{self.provider_name}' requires reference audio."
            )
        if voice_mode == "reference" and not reference_audio_present:
            raise TTSCapabilityError(
                f"TTS provider '{self.provider_name}' voice mode 'reference' requires reference audio."
            )
        if reference_audio_present and not self.supports_voice_mode("reference"):
            supported = ", ".join(self.voice_modes)
            raise TTSCapabilityError(
                f"TTS provider '{self.provider_name}' does not support reference audio. "
                f"Supported voice modes: {supported}."
            )
        if speaking_rate_requested and not self.speaking_rate_supported:
            raise TTSCapabilityError(
                f"TTS provider '{self.provider_name}' does not support speaking-rate control."
            )

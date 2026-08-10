"""Provider-neutral TTS catalog contracts and deterministic lookup helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
import math
import re

from app.domain.types import JsonDict
from app.providers.tts_capabilities import TTSCapabilities


class TTSCatalogError(ValueError):
    """Raised when a provider-neutral TTS catalog contract is invalid."""


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$")
_VOICE_MODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|auth|bearer|credential|password|passwd|private|secret|token)",
    flags=re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?:^bearer\s+\S+$|^(?:sk|pk|ghp|gho|xox[baprs])[-_][A-Za-z0-9_-]{6,}$|^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$)",
    flags=re.IGNORECASE,
)
_PATH_KEYWORDS = ("path", "file", "dir", "directory", "location", "uri", "url")


def _normalize_identifier(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TTSCatalogError(f"TTS {field_name} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise TTSCatalogError(f"TTS {field_name} is required.")
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise TTSCatalogError(f"TTS {field_name} must be a stable identifier.")
    return normalized


def _normalize_display_name(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TTSCatalogError(f"TTS {field_name} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise TTSCatalogError(f"TTS {field_name} is required.")
    return normalized


def _normalize_language_tag(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TTSCatalogError(f"TTS {field_name} must be a string.")
    normalized = value.strip().replace("_", "-").lower()
    if not normalized:
        raise TTSCatalogError(f"TTS {field_name} is required.")
    if not _LANGUAGE_RE.fullmatch(normalized):
        raise TTSCatalogError(f"TTS {field_name} must be a normalized language tag.")
    return normalized


def _normalize_voice_mode(value: Any) -> str:
    if not isinstance(value, str):
        raise TTSCatalogError("TTS voice_mode must be a string.")
    normalized = value.strip().lower()
    if not normalized:
        raise TTSCatalogError("TTS voice_mode is required.")
    if not _VOICE_MODE_RE.fullmatch(normalized):
        raise TTSCatalogError("TTS voice_mode must be a stable identifier.")
    return normalized


def _normalize_usage_policy(value: Any) -> str:
    if not isinstance(value, str):
        raise TTSCatalogError("TTS usage_policy must be a string.")
    normalized = value.strip().lower()
    if normalized not in {"production", "evaluation_only"}:
        raise TTSCatalogError("TTS usage_policy must be 'production' or 'evaluation_only'.")
    return normalized


def _normalize_unique_languages(values: Sequence[Any], *, field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        language = _normalize_language_tag(value, field_name=field_name)
        if language in seen:
            continue
        seen.add(language)
        normalized.append(language)
    if not normalized:
        raise TTSCatalogError(f"TTS {field_name} cannot be empty.")
    return tuple(sorted(normalized))


def _is_absolute_path_like(value: str) -> bool:
    return bool(
        value.startswith(("/", "\\"))
        or value.startswith("~/")
        or re.fullmatch(r"[A-Za-z]:[\\/].*", value) is not None
        or value.startswith("\\\\")
    )


def _is_path_like(value: str) -> bool:
    return _is_absolute_path_like(value) or "/" in value or "\\" in value


def _looks_like_secret_value(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return False
    return bool(_SECRET_VALUE_RE.fullmatch(normalized) or _SECRET_KEY_RE.search(normalized))


def _freeze_metadata(value: Any, *, field_name: str = "public_metadata", key_path: tuple[str, ...] = ()) -> Any:
    if value is None:
        return MappingProxyType({})
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TTSCatalogError(f"TTS {field_name} must be JSON-safe.")
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if _is_path_like(normalized):
            joined = ".".join(key_path) if key_path else field_name
            raise TTSCatalogError(f"TTS {joined} cannot contain path-like values.")
        if _looks_like_secret_value(normalized):
            joined = ".".join(key_path) if key_path else field_name
            raise TTSCatalogError(f"TTS {joined} cannot contain secret-bearing values.")
        return normalized
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise TTSCatalogError(f"TTS {field_name} keys must be strings.")
            key = raw_key.strip()
            if not key:
                raise TTSCatalogError(f"TTS {field_name} keys cannot be blank.")
            if _SECRET_KEY_RE.search(key):
                joined = ".".join((*key_path, key)) if key_path else key
                raise TTSCatalogError(f"TTS {joined} cannot contain secret-bearing fields.")
            if key.lower() in _PATH_KEYWORDS and isinstance(raw_value, str) and _is_path_like(raw_value.strip()):
                joined = ".".join((*key_path, key)) if key_path else key
                raise TTSCatalogError(f"TTS {joined} cannot contain path-like values.")
            frozen[key] = _freeze_metadata(raw_value, field_name=field_name, key_path=(*key_path, key))
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_metadata(item, field_name=field_name, key_path=key_path)
            for item in value
        )
    raise TTSCatalogError(f"TTS {field_name} must be JSON-safe.")


def _thaw_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_metadata(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_metadata(item) for item in value]
    return value


def _require_fields(payload: Mapping[str, Any], required: set[str], *, field_name: str) -> None:
    missing = sorted(required - set(payload))
    if missing:
        raise TTSCatalogError(f"TTS {field_name} is missing required fields: {', '.join(missing)}.")


def _reject_unknown_fields(payload: Mapping[str, Any], allowed: set[str], *, field_name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise TTSCatalogError(f"TTS {field_name} contains unknown fields: {', '.join(unknown)}.")


@dataclass(frozen=True, slots=True)
class TTSVoiceDescriptor:
    """Immutable catalog voice descriptor."""

    id: str
    display_name: str
    provider_id: str
    model_id: str
    voice_mode: str
    supported_languages: tuple[str, ...]
    preview_supported: bool
    reference_audio_required: bool
    public_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _normalize_identifier(self.id, field_name="voice id"))
        object.__setattr__(self, "display_name", _normalize_display_name(self.display_name, field_name="voice display_name"))
        object.__setattr__(self, "provider_id", _normalize_identifier(self.provider_id, field_name="voice provider_id"))
        object.__setattr__(self, "model_id", _normalize_identifier(self.model_id, field_name="voice model_id"))
        object.__setattr__(self, "voice_mode", _normalize_voice_mode(self.voice_mode))
        object.__setattr__(
            self,
            "supported_languages",
            _normalize_unique_languages(self.supported_languages, field_name="voice supported_languages"),
        )
        if not isinstance(self.preview_supported, bool):
            raise TTSCatalogError("TTS preview_supported must be a boolean.")
        if not isinstance(self.reference_audio_required, bool):
            raise TTSCatalogError("TTS reference_audio_required must be a boolean.")
        object.__setattr__(self, "public_metadata", _freeze_metadata(self.public_metadata))

    def supports_language(self, language: str | None) -> bool:
        if language is None:
            return True
        return _normalize_language_tag(language, field_name="language") in self.supported_languages

    def to_payload(self) -> JsonDict:
        payload: JsonDict = {
            "id": self.id,
            "display_name": self.display_name,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "voice_mode": self.voice_mode,
            "supported_languages": list(self.supported_languages),
            "preview_supported": self.preview_supported,
            "reference_audio_required": self.reference_audio_required,
        }
        if self.public_metadata:
            payload["public_metadata"] = _thaw_metadata(self.public_metadata)
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "TTSVoiceDescriptor":
        if not isinstance(payload, Mapping):
            raise TTSCatalogError("TTS voice payload must be an object.")
        allowed = {
            "id",
            "display_name",
            "provider_id",
            "model_id",
            "voice_mode",
            "supported_languages",
            "preview_supported",
            "reference_audio_required",
            "public_metadata",
        }
        _reject_unknown_fields(payload, allowed, field_name="voice payload")
        _require_fields(
            payload,
            {
                "id",
                "display_name",
                "provider_id",
                "model_id",
                "voice_mode",
                "supported_languages",
                "preview_supported",
                "reference_audio_required",
            },
            field_name="voice payload",
        )
        return cls(
            id=payload["id"],
            display_name=payload["display_name"],
            provider_id=payload["provider_id"],
            model_id=payload["model_id"],
            voice_mode=payload["voice_mode"],
            supported_languages=tuple(payload["supported_languages"]),
            preview_supported=payload["preview_supported"],
            reference_audio_required=payload["reference_audio_required"],
            public_metadata=payload.get("public_metadata") or {},
        )


@dataclass(frozen=True, slots=True)
class TTSModelDescriptor:
    """Immutable catalog model descriptor."""

    id: str
    display_name: str
    provider_id: str
    supported_languages: tuple[str, ...]
    voices: tuple[TTSVoiceDescriptor, ...]
    runtime_required: bool
    asset_required: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _normalize_identifier(self.id, field_name="model id"))
        object.__setattr__(self, "display_name", _normalize_display_name(self.display_name, field_name="model display_name"))
        object.__setattr__(self, "provider_id", _normalize_identifier(self.provider_id, field_name="model provider_id"))
        object.__setattr__(
            self,
            "supported_languages",
            _normalize_unique_languages(self.supported_languages, field_name="model supported_languages"),
        )
        if not isinstance(self.voices, tuple):
            object.__setattr__(self, "voices", tuple(self.voices))
        if not self.voices:
            raise TTSCatalogError("TTS model voices cannot be empty.")
        if not isinstance(self.runtime_required, bool):
            raise TTSCatalogError("TTS runtime_required must be a boolean.")
        if not isinstance(self.asset_required, bool):
            raise TTSCatalogError("TTS asset_required must be a boolean.")
        voice_ids: set[str] = set()
        for voice in self.voices:
            if not isinstance(voice, TTSVoiceDescriptor):
                raise TTSCatalogError("TTS model voices must be TTSVoiceDescriptor instances.")
            if voice.provider_id != self.provider_id:
                raise TTSCatalogError("TTS model voice provider_id must match the model provider_id.")
            if voice.model_id != self.id:
                raise TTSCatalogError("TTS model voice model_id must match the model id.")
            if not set(voice.supported_languages).issubset(self.supported_languages):
                raise TTSCatalogError("TTS model voice supported_languages must be a subset of the model languages.")
            if voice.id in voice_ids:
                raise TTSCatalogError("TTS model voices must have unique ids within a model.")
            voice_ids.add(voice.id)
        if not set(self.supported_languages):
            raise TTSCatalogError("TTS model supported_languages cannot be empty.")

    def supports_language(self, language: str | None) -> bool:
        if language is None:
            return True
        return _normalize_language_tag(language, field_name="language") in self.supported_languages

    def filter(self, *, language: str | None = None) -> TTSModelDescriptor | None:
        if language is None:
            return self
        normalized_language = _normalize_language_tag(language, field_name="language")
        if normalized_language not in self.supported_languages:
            return None
        voices = tuple(voice for voice in self.voices if voice.supports_language(normalized_language))
        if not voices:
            return None
        if voices == self.voices:
            return self
        return TTSModelDescriptor(
            id=self.id,
            display_name=self.display_name,
            provider_id=self.provider_id,
            supported_languages=self.supported_languages,
            voices=voices,
            runtime_required=self.runtime_required,
            asset_required=self.asset_required,
        )

    def to_payload(self) -> JsonDict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "provider_id": self.provider_id,
            "supported_languages": list(self.supported_languages),
            "runtime_required": self.runtime_required,
            "asset_required": self.asset_required,
            "voices": [voice.to_payload() for voice in self.voices],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "TTSModelDescriptor":
        if not isinstance(payload, Mapping):
            raise TTSCatalogError("TTS model payload must be an object.")
        allowed = {
            "id",
            "display_name",
            "provider_id",
            "supported_languages",
            "runtime_required",
            "asset_required",
            "voices",
        }
        _reject_unknown_fields(payload, allowed, field_name="model payload")
        _require_fields(
            payload,
            {
                "id",
                "display_name",
                "provider_id",
                "supported_languages",
                "runtime_required",
                "asset_required",
                "voices",
            },
            field_name="model payload",
        )
        return cls(
            id=payload["id"],
            display_name=payload["display_name"],
            provider_id=payload["provider_id"],
            supported_languages=tuple(payload["supported_languages"]),
            voices=tuple(TTSVoiceDescriptor.from_payload(voice) for voice in payload["voices"]),
            runtime_required=payload["runtime_required"],
            asset_required=payload["asset_required"],
        )


@dataclass(frozen=True, slots=True)
class TTSProviderDescriptor:
    """Immutable catalog provider descriptor."""

    id: str
    display_name: str
    usage_policy: str
    supported_languages: tuple[str, ...]
    capabilities: TTSCapabilities
    models: tuple[TTSModelDescriptor, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _normalize_identifier(self.id, field_name="provider id"))
        object.__setattr__(self, "display_name", _normalize_display_name(self.display_name, field_name="provider display_name"))
        object.__setattr__(self, "usage_policy", _normalize_usage_policy(self.usage_policy))
        object.__setattr__(
            self,
            "supported_languages",
            _normalize_unique_languages(self.supported_languages, field_name="provider supported_languages"),
        )
        if not isinstance(self.capabilities, TTSCapabilities):
            raise TTSCatalogError("TTS provider capabilities must be TTSCapabilities.")
        if self.capabilities.provider_name != self.id:
            raise TTSCatalogError("TTS provider capabilities provider_name must match the provider id.")
        if tuple(self.capabilities.supported_languages) != tuple(self.supported_languages):
            raise TTSCatalogError("TTS provider capabilities supported_languages must match the provider languages.")
        if self.capabilities.usage_policy != self.usage_policy:
            raise TTSCatalogError("TTS provider capabilities usage_policy must match the provider usage_policy.")
        if not isinstance(self.models, tuple):
            object.__setattr__(self, "models", tuple(self.models))
        if not self.models:
            raise TTSCatalogError("TTS provider models cannot be empty.")
        model_ids: set[str] = set()
        for model in self.models:
            if not isinstance(model, TTSModelDescriptor):
                raise TTSCatalogError("TTS provider models must be TTSModelDescriptor instances.")
            if model.provider_id != self.id:
                raise TTSCatalogError("TTS model provider_id must match the provider id.")
            if not set(model.supported_languages).issubset(self.supported_languages):
                raise TTSCatalogError("TTS model supported_languages must be a subset of the provider languages.")
            if model.id in model_ids:
                raise TTSCatalogError("TTS provider models must have unique ids within a provider.")
            model_ids.add(model.id)

    def supports_language(self, language: str | None) -> bool:
        if language is None:
            return True
        return _normalize_language_tag(language, field_name="language") in self.supported_languages

    def allows_usage_policy(self, usage_policy: str | None) -> bool:
        return self.capabilities.allows_usage_policy(usage_policy)

    def filter(self, *, language: str | None = None, usage_policy: str | None = None) -> TTSProviderDescriptor | None:
        if not self.allows_usage_policy(usage_policy):
            return None
        normalized_language = None if language is None else _normalize_language_tag(language, field_name="language")
        if normalized_language is not None and normalized_language not in self.supported_languages:
            return None
        models = tuple(
            model.filter(language=normalized_language)
            for model in self.models
        )
        filtered_models = tuple(model for model in models if model is not None)
        if not filtered_models:
            return None
        if filtered_models == self.models:
            return self
        return TTSProviderDescriptor(
            id=self.id,
            display_name=self.display_name,
            usage_policy=self.usage_policy,
            supported_languages=self.supported_languages,
            capabilities=self.capabilities,
            models=filtered_models,
        )

    def get_model(self, model_id: str) -> TTSModelDescriptor:
        normalized = _normalize_identifier(model_id, field_name="model id")
        for model in self.models:
            if model.id == normalized:
                return model
        raise TTSCatalogError(f"Unknown TTS model '{model_id}'.")

    def get_voice(self, model_id: str, voice_id: str) -> TTSVoiceDescriptor:
        model = self.get_model(model_id)
        normalized = _normalize_identifier(voice_id, field_name="voice id")
        for voice in model.voices:
            if voice.id == normalized:
                return voice
        raise TTSCatalogError(f"Unknown TTS voice '{voice_id}'.")

    def to_payload(self) -> JsonDict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "usage_policy": self.usage_policy,
            "supported_languages": list(self.supported_languages),
            "capabilities": self.capabilities.to_payload(),
            "models": [model.to_payload() for model in self.models],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "TTSProviderDescriptor":
        if not isinstance(payload, Mapping):
            raise TTSCatalogError("TTS provider payload must be an object.")
        allowed = {
            "id",
            "display_name",
            "usage_policy",
            "supported_languages",
            "capabilities",
            "models",
        }
        _reject_unknown_fields(payload, allowed, field_name="provider payload")
        _require_fields(
            payload,
            {
                "id",
                "display_name",
                "usage_policy",
                "supported_languages",
                "capabilities",
                "models",
            },
            field_name="provider payload",
        )
        return cls(
            id=payload["id"],
            display_name=payload["display_name"],
            usage_policy=payload["usage_policy"],
            supported_languages=tuple(payload["supported_languages"]),
            capabilities=TTSCapabilities(**payload["capabilities"]),
            models=tuple(TTSModelDescriptor.from_payload(model) for model in payload["models"]),
        )


@dataclass(frozen=True, slots=True)
class TTSCatalog:
    """Immutable provider-neutral catalog of TTS providers, models and voices."""

    providers: tuple[TTSProviderDescriptor, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.providers, tuple):
            object.__setattr__(self, "providers", tuple(self.providers))
        provider_ids: set[str] = set()
        for provider in self.providers:
            if not isinstance(provider, TTSProviderDescriptor):
                raise TTSCatalogError("TTS catalog providers must be TTSProviderDescriptor instances.")
            if provider.id in provider_ids:
                raise TTSCatalogError("TTS provider ids must be globally unique.")
            provider_ids.add(provider.id)

    def filter(self, *, language: str | None = None, usage_policy: str | None = None) -> TTSCatalog:
        normalized_language = None if language is None else _normalize_language_tag(language, field_name="language")
        normalized_usage_policy = None if usage_policy is None else _normalize_usage_policy(usage_policy)
        providers = tuple(
            provider.filter(language=normalized_language, usage_policy=normalized_usage_policy)
            for provider in self.providers
        )
        filtered = tuple(provider for provider in providers if provider is not None)
        if not filtered:
            return TTSCatalog(providers=tuple())
        if filtered == self.providers:
            return self
        return TTSCatalog(providers=filtered)

    def get_provider(self, provider_id: str) -> TTSProviderDescriptor:
        normalized = _normalize_identifier(provider_id, field_name="provider id")
        for provider in self.providers:
            if provider.id == normalized:
                return provider
        raise TTSCatalogError(f"Unknown TTS provider '{provider_id}'.")

    def get_model(self, provider_id: str, model_id: str) -> TTSModelDescriptor:
        return self.get_provider(provider_id).get_model(model_id)

    def get_voice(self, provider_id: str, model_id: str, voice_id: str) -> TTSVoiceDescriptor:
        return self.get_provider(provider_id).get_voice(model_id, voice_id)

    def to_payload(self) -> JsonDict:
        return {"providers": [provider.to_payload() for provider in self.providers]}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "TTSCatalog":
        if not isinstance(payload, Mapping):
            raise TTSCatalogError("TTS catalog payload must be an object.")
        allowed = {"providers"}
        _reject_unknown_fields(payload, allowed, field_name="catalog payload")
        _require_fields(payload, {"providers"}, field_name="catalog payload")
        return cls(
            providers=tuple(TTSProviderDescriptor.from_payload(provider) for provider in payload["providers"]),
        )


__all__ = [
    "TTSCatalog",
    "TTSCatalogError",
    "TTSModelDescriptor",
    "TTSProviderDescriptor",
    "TTSVoiceDescriptor",
]

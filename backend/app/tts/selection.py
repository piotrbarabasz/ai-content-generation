"""Canonical provider-neutral mapping from TTS catalog choices to workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

from app.domain.types import JsonDict
from app.providers.tts_settings import TTSSettings, TTSSettingsError

from .catalog import TTSCatalog, TTSCatalogError
from .post_processing import validate_tempo


class TTSSelectionError(ValueError):
    """Raised when a catalog selection cannot form a safe workflow mapping."""


_OPAQUE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_APPROVAL_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$")
_MODEL_SETTING = {
    "mock": "model_variant",
    "chatterbox_v3": "model_variant",
    "piper": "model_key",
    "xtts_v2_eval": "model_variant",
}
_SETTING_KEYS = {
    "mock": frozenset(
        {"device", "exaggeration", "cfg_weight", "temperature", "repetition_penalty", "min_p", "top_p"}
    ),
    "chatterbox_v3": frozenset(
        {"device", "exaggeration", "cfg_weight", "temperature", "repetition_penalty", "min_p", "top_p"}
    ),
    "piper": frozenset(
        {"device", "length_scale", "volume", "noise_scale", "noise_w_scale"}
    ),
    "xtts_v2_eval": frozenset({"device"}),
}


@dataclass(frozen=True, slots=True)
class WorkflowTTSMapping:
    """Transient result containing only existing WorkflowConfig fields."""

    provider_config: JsonDict
    voice_config: JsonDict
    language: str

    def to_payload(self) -> JsonDict:
        return {
            "provider_config": self.provider_config,
            "voice_config": self.voice_config,
            "language": self.language,
        }


def map_catalog_selection(
    *,
    catalog: TTSCatalog,
    provider: str,
    model: str,
    voice: str,
    language: str,
    tempo: object = 1.0,
    synthesis_settings: Mapping[str, Any] | None = None,
    reference_audio_artifact_id: str | None = None,
    reference_audio_metadata: Mapping[str, Any] | None = None,
    usage_policy: str | None = "production",
    enabled: bool = True,
    require_reference_metadata: bool = True,
) -> WorkflowTTSMapping:
    """Validate one catalog choice and translate it to canonical workflow fields."""

    if not isinstance(catalog, TTSCatalog):
        raise TypeError("TTS selection catalog must be a TTSCatalog instance.")
    if not isinstance(enabled, bool):
        raise TTSSelectionError("TTS provider enabled must be a boolean.")
    normalized_language = _normalize_language(language)
    normalized_tempo = validate_tempo(tempo)
    try:
        provider_descriptor = catalog.get_provider(provider)
        model_descriptor = provider_descriptor.get_model(model)
        voice_descriptor = provider_descriptor.get_voice(model_descriptor.id, voice)
        if not provider_descriptor.supports_language(normalized_language):
            raise TTSSelectionError("The selected TTS provider does not support the workflow language.")
        if not model_descriptor.supports_language(normalized_language):
            raise TTSSelectionError("The selected TTS model does not support the workflow language.")
        if not voice_descriptor.supports_language(normalized_language):
            raise TTSSelectionError("The selected TTS voice does not support the workflow language.")
    except TTSCatalogError as exc:
        raise TTSSelectionError(str(exc)) from exc

    effective_policy = provider_descriptor.usage_policy if usage_policy is None else _normalize_policy(usage_policy)
    artifact_id = _normalize_reference_id(reference_audio_artifact_id)
    uses_reference = voice_descriptor.voice_mode == "reference"
    if uses_reference and artifact_id is None:
        raise TTSSelectionError("The selected TTS voice requires an approved reference-audio artifact id.")
    if artifact_id is not None and not uses_reference:
        raise TTSSelectionError("The selected TTS voice does not accept reference audio.")
    metadata = _normalize_reference_metadata(
        reference_audio_metadata,
        required=uses_reference and require_reference_metadata,
    )
    if metadata is not None and artifact_id is None:
        raise TTSSelectionError("TTS reference-audio metadata requires an artifact id.")
    try:
        provider_descriptor.capabilities.validate_request(
            language_id=normalized_language,
            voice_mode=voice_descriptor.voice_mode,
            reference_audio_present=artifact_id is not None,
            usage_policy=effective_policy,
        )
    except ValueError as exc:
        raise TTSSelectionError(str(exc)) from exc

    settings = TTSSettings.normalize_mapping(synthesis_settings)
    allowed_settings = _SETTING_KEYS.get(provider_descriptor.id)
    model_setting = _MODEL_SETTING.get(provider_descriptor.id)
    if allowed_settings is None or model_setting is None:
        raise TTSSelectionError(f"TTS provider '{provider_descriptor.id}' has no workflow mapping.")
    foreign = sorted(set(settings) - allowed_settings)
    if foreign:
        raise TTSSelectionError(
            f"Settings are not declared for TTS provider '{provider_descriptor.id}': {', '.join(foreign)}."
        )

    provider_settings: JsonDict = {
        "provider": provider_descriptor.id,
        "usage_policy": effective_policy,
        model_setting: model_descriptor.id,
        **settings,
    }
    validation_settings = dict(provider_settings)
    validation_settings["language_id"] = normalized_language
    if uses_reference:
        if provider_descriptor.id in {"mock", "chatterbox_v3"}:
            validation_settings["audio_prompt_path"] = "approved-reference.wav"
        elif provider_descriptor.id == "xtts_v2_eval":
            validation_settings["reference_audio_path"] = "approved-reference.wav"
            validation_settings["approved_label"] = (
                metadata["approval_label"] if metadata is not None else "approved"
            )
    try:
        TTSSettings.from_mapping(validation_settings, provider=provider_descriptor.id)
    except TTSSettingsError as exc:
        raise TTSSelectionError(str(exc)) from exc

    voice_config: JsonDict = {
        "voice_id": voice_descriptor.id,
        "voice_mode": voice_descriptor.voice_mode,
        "post_processing": {"tempo": normalized_tempo},
    }
    if artifact_id is not None:
        voice_config["reference_audio_artifact_id"] = artifact_id
    if metadata is not None:
        voice_config["reference_audio_metadata"] = metadata

    return WorkflowTTSMapping(
        provider_config={
            "tts": {
                "providerName": provider_descriptor.id,
                "enabled": enabled,
                "settings": provider_settings,
            }
        },
        voice_config=voice_config,
        language=normalized_language,
    )


def validate_workflow_tts_mapping(
    *,
    catalog: TTSCatalog,
    provider_config: Mapping[str, Any],
    voice_config: Mapping[str, Any],
    language: str,
) -> None:
    """Reject non-canonical or stale persisted production TTS selections."""

    raw_tts = provider_config.get("tts")
    if raw_tts is None:
        return
    if not isinstance(raw_tts, Mapping):
        raise TTSSelectionError("Workflow providerConfig.tts must be an object.")
    has_camel_name = "providerName" in raw_tts
    has_snake_name = "provider_name" in raw_tts
    if has_camel_name and has_snake_name:
        raise TTSSelectionError(
            "Workflow providerConfig.tts cannot contain both providerName and provider_name."
        )
    provider_name = raw_tts.get("providerName", raw_tts.get("provider_name"))
    if has_snake_name and provider_name != "mock":
        raise TTSSelectionError(
            "Workflow providerConfig.tts must use providerName for non-mock providers."
        )
    if provider_name == "mock":
        return
    unknown_provider_fields = sorted(set(raw_tts) - {"providerName", "enabled", "settings"})
    if unknown_provider_fields:
        raise TTSSelectionError(
            "Workflow providerConfig.tts contains unknown fields: " + ", ".join(unknown_provider_fields) + "."
        )
    settings = TTSSettings.normalize_mapping(raw_tts.get("settings"))
    model_setting = _MODEL_SETTING.get(str(provider_name))
    if model_setting is None or model_setting not in settings:
        raise TTSSelectionError("Workflow TTS selection is missing its catalog model id.")
    model = settings.pop(model_setting)
    configured_provider = settings.pop("provider", provider_name)
    if configured_provider != provider_name:
        raise TTSSelectionError("Workflow TTS settings provider does not match providerName.")
    usage_policy = settings.pop("usage_policy", "production")
    voice_id = voice_config.get("voice_id", voice_config.get("voiceId"))
    voice_mode = voice_config.get("voice_mode", voice_config.get("voiceMode"))
    post_processing = voice_config.get("post_processing", voice_config.get("postProcessing"))
    if not isinstance(post_processing, Mapping):
        raise TTSSelectionError("Workflow voiceConfig.postProcessing is required.")
    metadata = voice_config.get(
        "reference_audio_metadata", voice_config.get("referenceAudioMetadata")
    )
    mapped = map_catalog_selection(
        catalog=catalog,
        provider=str(provider_name),
        model=str(model),
        voice=str(voice_id),
        language=language,
        tempo=post_processing.get("tempo"),
        synthesis_settings=settings,
        reference_audio_artifact_id=voice_config.get(
            "reference_audio_artifact_id", voice_config.get("referenceAudioArtifactId")
        ),
        reference_audio_metadata=metadata if isinstance(metadata, Mapping) else None,
        usage_policy=str(usage_policy),
        enabled=raw_tts.get("enabled", True),
    )
    expected_tts = mapped.provider_config["tts"]
    actual_tts = {
        "providerName": provider_name,
        "enabled": raw_tts.get("enabled", True),
        "settings": TTSSettings.normalize_mapping(raw_tts.get("settings")),
    }
    if actual_tts != expected_tts or dict(voice_config) != mapped.voice_config:
        raise TTSSelectionError("Workflow TTS selection is not in canonical form.")


def _normalize_language(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TTSSelectionError("TTS selection language is required.")
    return value.strip().replace("_", "-").lower()


def _normalize_policy(value: object) -> str:
    if not isinstance(value, str):
        raise TTSSelectionError("TTS usage policy must be a string.")
    normalized = value.strip().lower()
    if normalized not in {"production", "evaluation_only"}:
        raise TTSSelectionError("TTS usage policy must be 'production' or 'evaluation_only'.")
    return normalized


def _normalize_reference_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _OPAQUE_REFERENCE_RE.fullmatch(value.strip()) is None:
        raise TTSSelectionError("TTS referenceAudioArtifactId must be an opaque identifier.")
    return value.strip()


def _normalize_reference_metadata(
    value: Mapping[str, Any] | None,
    *,
    required: bool,
) -> JsonDict | None:
    if value is None:
        if required:
            raise TTSSelectionError("Approved TTS reference-audio metadata is required.")
        return None
    if not isinstance(value, Mapping):
        raise TTSSelectionError("TTS reference-audio metadata must be an object.")
    aliases = {"approvalLabel": "approval_label"}
    normalized = {aliases.get(key, key): item for key, item in value.items()}
    unknown = sorted(set(normalized) - {"checksum", "approval_label", "approved"})
    if unknown:
        raise TTSSelectionError("Unknown TTS reference-audio metadata: " + ", ".join(unknown) + ".")
    checksum = normalized.get("checksum")
    approval_label = normalized.get("approval_label")
    if not isinstance(checksum, str) or _SHA256_RE.fullmatch(checksum.strip().lower()) is None:
        raise TTSSelectionError("TTS reference-audio metadata requires a SHA-256 checksum.")
    if (
        not isinstance(approval_label, str)
        or _APPROVAL_LABEL_RE.fullmatch(approval_label.strip()) is None
    ):
        raise TTSSelectionError("TTS reference-audio metadata requires an approval label.")
    if normalized.get("approved") is not True:
        raise TTSSelectionError("TTS reference audio must be approved.")
    return {
        "checksum": checksum.strip().lower(),
        "approval_label": approval_label.strip(),
        "approved": True,
    }


__all__ = [
    "TTSSelectionError",
    "WorkflowTTSMapping",
    "map_catalog_selection",
    "validate_workflow_tts_mapping",
]

"""Composition helpers for configured TTS providers."""

from __future__ import annotations

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig

from .chatterbox_v3 import ChatterboxV3Provider
from .interfaces import TTSProvider
from .mock_tts import MockTTSProvider
from .registry import ProviderRegistry, ProviderRegistryError
from .tts_capabilities import TTSCapabilityError
from .tts_settings import TTSSettings, TTSSettingsError


class TTSFactoryError(ValueError):
    """Raised when a ProviderConfig cannot compose a supported TTS provider."""


def build_tts_provider(
    provider_config: ProviderConfig,
    *,
    registry: ProviderRegistry | None = None,
) -> TTSProvider:
    """Create and register the configured TTS provider without loading optional runtimes."""

    if not isinstance(provider_config, ProviderConfig):
        raise TTSFactoryError("TTS provider configuration must be a ProviderConfig instance.")
    if provider_config.provider_type is not ProviderType.TTS:
        raise TTSFactoryError("ProviderConfig must have provider_type 'tts'.")
    try:
        settings = TTSSettings.from_mapping(
            provider_config.settings, provider=provider_config.provider_name
        )
    except TTSSettingsError as exc:
        raise TTSFactoryError(str(exc)) from exc

    if settings.provider == "mock":
        provider: TTSProvider = MockTTSProvider(settings.provider)
    elif settings.provider == "chatterbox_v3":
        provider = ChatterboxV3Provider(
            settings.provider,
            device=settings.device,
            language_id=settings.language_id or "pl",
            audio_prompt_path=settings.audio_prompt_path,
            exaggeration=settings.exaggeration,
            cfg_weight=settings.cfg_weight,
            temperature=settings.temperature,
            repetition_penalty=settings.repetition_penalty,
            min_p=settings.min_p,
            top_p=settings.top_p,
        )
    else:  # TTSSettings validates this branch; retain an actionable factory boundary.
        raise TTSFactoryError(f"Unknown TTS provider: {settings.provider}.")

    capabilities = provider.capabilities()
    try:
        capabilities.validate_request(
            language_id=settings.language_id
            or getattr(provider, "language_id", None)
            or None,
            voice_mode=(
                "reference"
                if settings.audio_prompt_path is not None
                else capabilities.voice_modes[0]
            ),
            reference_audio_present=settings.audio_prompt_path is not None,
            usage_policy=settings.usage_policy,
        )
    except TTSCapabilityError as exc:
        raise TTSFactoryError(str(exc)) from exc

    target_registry = registry or ProviderRegistry()
    try:
        target_registry.register(provider)
        resolved = target_registry.resolve_from_config(provider_config)
    except ProviderRegistryError as exc:
        raise TTSFactoryError(str(exc)) from exc
    if not isinstance(resolved, TTSProvider):
        raise TTSFactoryError("Configured provider does not implement TTSProvider.")
    return resolved

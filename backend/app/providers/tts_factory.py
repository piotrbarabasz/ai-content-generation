"""Composition helpers for configured TTS providers."""

from __future__ import annotations

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig

from .chatterbox_v3 import ChatterboxV3Provider
from .interfaces import TTSProvider
from .mock_tts import MockTTSProvider
from .registry import ProviderRegistry, ProviderRegistryError
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
        )
    else:  # TTSSettings validates this branch; retain an actionable factory boundary.
        raise TTSFactoryError(f"Unknown TTS provider: {settings.provider}.")

    target_registry = registry or ProviderRegistry()
    try:
        target_registry.register(provider)
        resolved = target_registry.resolve_from_config(provider_config)
    except ProviderRegistryError as exc:
        raise TTSFactoryError(str(exc)) from exc
    if not isinstance(resolved, TTSProvider):
        raise TTSFactoryError("Configured provider does not implement TTSProvider.")
    return resolved

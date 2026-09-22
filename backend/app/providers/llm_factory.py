"""Composition factory for the single real D026 LLM provider."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.interfaces import LLMProvider
from app.providers.llm_settings import LLMSettingsError, OpenAISettings
from app.providers.openai_llm import OpenAILLMProvider, OpenAITransport
from app.providers.registry import ProviderRegistry, ProviderRegistryError


class LLMFactoryError(ValueError):
    pass


def build_llm_provider(provider_config: ProviderConfig, *, registry: ProviderRegistry | None = None,
                       transport: OpenAITransport | None = None,
                       environment: Mapping[str, str] | None = None) -> LLMProvider:
    if not isinstance(provider_config, ProviderConfig):
        raise LLMFactoryError("LLM provider configuration must be a ProviderConfig instance.")
    if provider_config.provider_type is not ProviderType.LLM:
        raise LLMFactoryError("ProviderConfig must have provider_type 'LLMProvider'.")
    if not provider_config.enabled:
        raise LLMFactoryError("LLM provider is disabled.")
    if registry is not None and registry.has(ProviderType.LLM, provider_config.provider_name):
        return registry.resolve_from_config(provider_config)
    if provider_config.provider_name == "mock":
        from app.providers.mock_llm import MockLLMProvider

        provider: Any = MockLLMProvider()
    elif provider_config.provider_name == "openai":
        try:
            settings = OpenAISettings.from_mapping(provider_config.settings)
        except LLMSettingsError as exc:
            raise LLMFactoryError(str(exc)) from exc
        provider = OpenAILLMProvider(settings, transport=transport, environment=environment)
    else:
        raise LLMFactoryError(f"Unsupported LLM provider: {provider_config.provider_name}.")
    if registry is None:
        return provider
    try:
        registry.register(provider)
        resolved = registry.resolve_from_config(provider_config)
    except ProviderRegistryError as exc:
        raise LLMFactoryError(str(exc)) from exc
    if not isinstance(resolved, LLMProvider):
        raise LLMFactoryError("Configured provider does not implement LLMProvider.")
    return resolved


__all__ = ["LLMFactoryError", "build_llm_provider"]

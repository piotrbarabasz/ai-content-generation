from __future__ import annotations

import pytest

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.mock_llm import MockLLMProvider
from app.providers.mock_storage import MockStorageProvider
from app.providers.mock_tts import MockTTSProvider
from app.providers.registry import ProviderRegistry, ProviderRegistryError


def test_provider_registry_registers_and_resolves_providers_by_type_and_name() -> None:
    registry = ProviderRegistry()
    llm = registry.register(MockLLMProvider("primary"))
    tts = registry.register(MockTTSProvider("primary"))
    storage = registry.register(MockStorageProvider("archive"))
    provider_config = ProviderConfig.create(
        workflow_config_id="workflow_config_1",
        provider_type=ProviderType.LLM,
        provider_name="primary",
    )

    assert registry.has(ProviderType.LLM, "primary") is True
    assert registry.has("TTSProvider", "primary") is True
    assert registry.resolve(ProviderType.LLM, "primary") is llm
    assert registry.resolve(ProviderType.TTS, provider_config.provider_name) is tts
    assert registry.resolve_from_config(provider_config) is llm
    assert registry.get(ProviderType.STORAGE, "archive") is storage
    assert registry.list_provider_names(ProviderType.LLM) == ("primary",)
    assert registry.list_provider_names(ProviderType.TTS) == ("primary",)
    assert registry.list_provider_names(ProviderType.STORAGE) == ("archive",)


def test_provider_registry_rejects_duplicate_and_unknown_providers() -> None:
    registry = ProviderRegistry([MockLLMProvider("mock")])

    with pytest.raises(ProviderRegistryError, match="Duplicate provider registration"):
        registry.register(MockLLMProvider("mock"))

    with pytest.raises(ProviderRegistryError, match="Unknown provider"):
        registry.resolve(ProviderType.LLM, "missing")

    with pytest.raises(ProviderRegistryError, match="Unknown provider type"):
        registry.resolve("NotAProvider", "mock")

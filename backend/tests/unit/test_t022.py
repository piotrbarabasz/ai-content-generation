from __future__ import annotations

import pytest

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.interfaces import (
    AssetProvider,
    CaptionProvider,
    LLMProvider,
    PublishingProvider,
    StorageProvider,
    TTSProvider,
    TranscriptionProvider,
    VideoRendererProvider,
)
from app.providers.mock_assets import MockAssetProvider
from app.providers.mock_captions import MockCaptionProvider
from app.providers.mock_llm import MockLLMProvider
from app.providers.mock_storage import MockStorageProvider
from app.providers.mock_tts import MockTTSProvider
from app.providers.mock_transcription import MockTranscriptionProvider
from app.providers.mock_video_renderer import MockVideoRendererProvider
from app.providers.mocks import (
    MockPublishingProvider,
    build_mock_provider_registry,
    register_mock_providers,
)
from app.providers.registry import ProviderRegistry, ProviderRegistryError


def test_provider_registry_registers_and_resolves_providers_by_type_and_name() -> None:
    registry = ProviderRegistry()
    llm = registry.register(MockLLMProvider("primary"))
    tts = registry.register(MockTTSProvider("primary"))
    config = ProviderConfig.create(
        workflow_config_id="workflow_config_1",
        provider_type=ProviderType.LLM,
        provider_name="primary",
    )

    assert registry.has(ProviderType.LLM, "primary")
    assert registry.has("TTSProvider", "primary")
    assert registry.resolve(ProviderType.LLM, "primary") is llm
    assert registry.resolve(ProviderType.TTS, config.provider_name) is tts
    assert registry.resolve_from_config(config) is llm
    assert registry.list_provider_types() == (ProviderType.LLM, ProviderType.TTS)
    assert registry.list_provider_names(ProviderType.LLM) == ("primary",)
    assert registry.snapshot()[0].provider is llm
    assert registry.as_execution_context()[ProviderType.LLM]["primary"] is llm


def test_mock_provider_registration_builds_the_default_registry() -> None:
    registry = build_mock_provider_registry()

    assert registry.resolve(ProviderType.LLM, "mock")
    assert registry.resolve(ProviderType.TTS, "mock")
    assert registry.resolve(ProviderType.TRANSCRIPTION, "mock")
    assert registry.resolve(ProviderType.CAPTION, "mock")
    assert registry.resolve(ProviderType.ASSET, "mock")
    assert registry.resolve(ProviderType.VIDEO_RENDERER, "mock")
    assert registry.resolve(ProviderType.STORAGE, "mock")
    assert registry.resolve(ProviderType.PUBLISHING, "mock")


def test_provider_registry_rejects_duplicate_and_unknown_providers() -> None:
    registry = ProviderRegistry([MockLLMProvider("mock")])

    with pytest.raises(ProviderRegistryError, match="Duplicate provider registration"):
        registry.register(MockLLMProvider("mock"))

    with pytest.raises(ProviderRegistryError, match="Unknown provider"):
        registry.resolve(ProviderType.LLM, "missing")

    with pytest.raises(ProviderRegistryError, match="Unknown provider type"):
        registry.resolve("NotAProvider", "mock")


def test_mock_provider_registration_returns_deterministic_mock_contracts() -> None:
    registry = ProviderRegistry()
    register_mock_providers(registry, provider_name="default")

    llm = registry.resolve(ProviderType.LLM, "default")
    tts = registry.resolve(ProviderType.TTS, "default")
    transcription = registry.resolve(ProviderType.TRANSCRIPTION, "default")
    captions = registry.resolve(ProviderType.CAPTION, "default")
    assets = registry.resolve(ProviderType.ASSET, "default")
    renderer = registry.resolve(ProviderType.VIDEO_RENDERER, "default")
    storage = registry.resolve(ProviderType.STORAGE, "default")
    publishing = registry.resolve(ProviderType.PUBLISHING, "default")

    assert isinstance(llm, LLMProvider)
    assert isinstance(tts, TTSProvider)
    assert isinstance(transcription, TranscriptionProvider)
    assert isinstance(captions, CaptionProvider)
    assert isinstance(assets, AssetProvider)
    assert isinstance(renderer, VideoRendererProvider)
    assert isinstance(storage, StorageProvider)
    assert isinstance(publishing, PublishingProvider)

    assert isinstance(llm, MockLLMProvider)
    assert isinstance(tts, MockTTSProvider)
    assert isinstance(transcription, MockTranscriptionProvider)
    assert isinstance(captions, MockCaptionProvider)
    assert isinstance(assets, MockAssetProvider)
    assert isinstance(renderer, MockVideoRendererProvider)
    assert isinstance(storage, MockStorageProvider)
    assert isinstance(publishing, MockPublishingProvider)

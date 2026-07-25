"""Mock provider registration helpers for deterministic MVP testing."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from app.domain.enums import ProviderType
from app.domain.types import JsonDict

from .interfaces import (
    AssetProvider,
    CaptionProvider,
    LLMProvider,
    PublishingProvider,
    StorageProvider,
    TTSProvider,
    TranscriptionProvider,
    VideoRendererProvider,
    _coerce_json_dict,
    _slugify,
    _stable_signature,
)
from .mock_assets import MockAssetProvider
from .mock_captions import MockCaptionProvider
from .mock_llm import MockLLMProvider
from .mock_storage import MockStorageProvider
from .mock_tts import MockTTSProvider
from .mock_transcription import MockTranscriptionProvider
from .mock_video_renderer import MockVideoRendererProvider
from .registry import ProviderRegistry


class MockPublishingProvider(PublishingProvider):
    """Deterministic publishing provider placeholder for MVP registry wiring."""

    provider_type = ProviderType.PUBLISHING

    def __init__(self, provider_name: str = "mock") -> None:
        self.provider_name = provider_name

    def publish(self, export_bundle: JsonDict, target: str) -> JsonDict:
        normalized_bundle = _coerce_json_dict(export_bundle)
        signature = _stable_signature(
            {
                "provider": self.provider_name,
                "export_bundle": normalized_bundle,
                "target": target,
            }
        )
        publish_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
        return {
            "provider": self.provider_name,
            "target": target,
            "status": "published",
            "publish_ref": f"mock://publish/{_slugify(target)}/{publish_id}",
            "bundle_signature": signature[:12],
        }


def build_mock_provider_registry(provider_name: str = "mock") -> ProviderRegistry:
    """Create a registry preloaded with deterministic mock providers."""

    registry = ProviderRegistry()
    register_mock_providers(registry, provider_name=provider_name)
    return registry


def register_mock_providers(
    registry: ProviderRegistry,
    *,
    provider_name: str = "mock",
) -> ProviderRegistry:
    """Register the deterministic mock provider set used by the MVP."""

    providers: Iterable[
        LLMProvider
        | TTSProvider
        | TranscriptionProvider
        | CaptionProvider
        | AssetProvider
        | VideoRendererProvider
        | StorageProvider
        | PublishingProvider
    ] = (
        MockLLMProvider(provider_name),
        MockTTSProvider(provider_name),
        MockTranscriptionProvider(provider_name),
        MockCaptionProvider(provider_name),
        MockAssetProvider(provider_name),
        MockVideoRendererProvider(provider_name),
        MockStorageProvider(provider_name),
        MockPublishingProvider(provider_name),
    )
    registry.register_all(providers)
    return registry

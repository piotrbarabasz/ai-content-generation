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
    PublishingRequest,
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

    def publish(
        self,
        export_bundle: PublishingRequest | JsonDict,
        target: str | None = None,
    ) -> JsonDict:
        if isinstance(export_bundle, PublishingRequest):
            normalized_bundle = dict(export_bundle.handoff)
            target = export_bundle.target
            idempotency_key = export_bundle.idempotency_key
        else:
            normalized_bundle = _coerce_json_dict(export_bundle)
            target = str(target or normalized_bundle.get("platform") or "").strip()
            idempotency_key = str(normalized_bundle.get("idempotencyKey") or "")
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
            "idempotency_key": idempotency_key or publish_id,
            "platform": target,
            "privacy_status": str(
                _coerce_json_dict(normalized_bundle.get("metadata") if isinstance(normalized_bundle.get("metadata"), dict) else {}).get("privacyStatus")
                or "unlisted"
            ),
            "caption_upload": {"status": "mocked"},
            "video_id": publish_id,
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

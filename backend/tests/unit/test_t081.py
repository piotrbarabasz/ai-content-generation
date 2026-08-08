from __future__ import annotations

import hashlib
import sys

import pytest

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.publishing_factory import build_publishing_provider
from app.providers.mocks import MockPublishingProvider
from app.providers.registry import ProviderRegistry
from app.providers.youtube_publishing import (
    YouTubePublishingError,
    YouTubePublishingProvider,
)


PAYLOAD_CHECKSUM = hashlib.sha256(b"payload").hexdigest()


class FakeTransport:
    def __init__(self) -> None:
        self.video_calls = []
        self.caption_calls = []

    def upload_video(self, **kwargs):
        self.video_calls.append(kwargs)
        return {"id": "video_123"}

    def upload_caption(self, **kwargs):
        self.caption_calls.append(kwargs)
        return {"id": "caption_456"}


def _handoff(*, approved: bool = True):
    return {
        "schemaVersion": 1,
        "platform": "youtube",
        "exportId": "export_1",
        "sourceLanguage": "en",
        "approved": approved,
        "idempotencyKey": "stable-upload-key",
        "metadata": {
            "title": "Title",
            "description": "Description",
            "tags": ["one", "two"],
            "categoryId": "22",
            "privacyStatus": "unlisted",
            "madeForKids": False,
            "containsSyntheticMedia": True,
        },
        "artifacts": {
            "video": {
                "available": True,
                "name": "render.mp4",
                "storageKey": "run/render/video.mp4",
                "checksum": PAYLOAD_CHECKSUM,
            },
            "captions": {
                "language": "en",
                "srt": {
                    "available": True,
                    "name": "captions.en.srt",
                    "storageKey": "run/captions/captions.en.srt",
                    "checksum": PAYLOAD_CHECKSUM,
                },
            },
        },
        "localization": {
            "localizationStrategy": "platform_auto_dub",
            "localizationTargets": ["pl"],
            "manualAcceptanceRequired": True,
            "customAudioFallbackEnabled": True,
        },
    }


def test_factory_composes_youtube_through_provider_config_and_registry() -> None:
    transport = FakeTransport()
    registry = ProviderRegistry()
    config = ProviderConfig.create(
        workflow_config_id="config_1",
        provider_type=ProviderType.PUBLISHING,
        provider_name="youtube",
        settings={"credentialsEnv": "YOUTUBE_CREDENTIALS_FILE"},
    )
    provider = build_publishing_provider(
        config,
        registry=registry,
        transport=transport,
        artifact_reader=lambda key: key.encode("utf-8"),
    )
    assert registry.resolve_from_config(config) is provider
    assert provider.provider_name == "youtube"


def test_factory_keeps_deterministic_mock_as_offline_default() -> None:
    config = ProviderConfig.create(
        workflow_config_id="config_mock",
        provider_type=ProviderType.PUBLISHING,
        provider_name="mock",
    )
    provider = build_publishing_provider(config)
    assert isinstance(provider, MockPublishingProvider)
    assert provider.publish(_handoff(), "youtube")["status"] == "published"


def test_youtube_mapping_uploads_video_and_timed_english_caption_offline() -> None:
    transport = FakeTransport()
    reads = []
    provider = YouTubePublishingProvider(
        transport=transport,
        artifact_reader=lambda key: reads.append(key) or b"payload",
    )
    result = provider.publish(_handoff()).to_payload()
    assert result["video_id"] == "video_123"
    assert result["privacy_status"] == "unlisted"
    assert result["idempotency_key"] == "stable-upload-key"
    assert result["caption_upload"] == {
        "status": "uploaded",
        "caption_id": "caption_456",
        "language": "en",
    }
    body = transport.video_calls[0]["body"]
    assert body["snippet"]["defaultLanguage"] == "en"
    assert "defaultAudioLanguage" not in body["snippet"]
    assert body["status"]["containsSyntheticMedia"] is True
    assert transport.caption_calls[0]["body"]["snippet"]["language"] == "en"
    assert reads == ["run/render/video.mp4", "run/captions/captions.en.srt"]


def test_unapproved_export_is_rejected_before_transport_and_google_imports_are_lazy() -> None:
    transport = FakeTransport()
    provider = YouTubePublishingProvider(
        transport=transport, artifact_reader=lambda _key: b"payload"
    )
    with pytest.raises(YouTubePublishingError, match="approval"):
        provider.publish(_handoff(approved=False))
    assert transport.video_calls == []
    assert "googleapiclient.discovery" not in sys.modules


def test_default_transport_reports_missing_credential_reference_without_oauth(
    monkeypatch,
) -> None:
    monkeypatch.delenv("YOUTUBE_CREDENTIALS_FILE", raising=False)
    provider = YouTubePublishingProvider(artifact_reader=lambda _key: b"payload")
    with pytest.raises(YouTubePublishingError, match="YOUTUBE_CREDENTIALS_FILE"):
        provider.publish(_handoff())


def test_youtube_provider_rejects_artifact_checksum_mismatch_before_transport() -> None:
    transport = FakeTransport()
    provider = YouTubePublishingProvider(
        transport=transport,
        artifact_reader=lambda _key: b"different payload",
    )
    with pytest.raises(YouTubePublishingError, match="checksum mismatch"):
        provider.publish(_handoff())
    assert transport.video_calls == []


def test_external_errors_are_translated_without_secret_leakage() -> None:
    class FailingTransport(FakeTransport):
        def upload_video(self, **kwargs):
            raise RuntimeError("Authorization: Bearer super-secret-token")

    provider = YouTubePublishingProvider(
        transport=FailingTransport(), artifact_reader=lambda _key: b"payload"
    )
    with pytest.raises(YouTubePublishingError) as captured:
        provider.publish(_handoff())
    assert "super-secret-token" not in str(captured.value)
    assert "RuntimeError" in str(captured.value)

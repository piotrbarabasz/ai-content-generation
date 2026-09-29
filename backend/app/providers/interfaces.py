"""Provider interfaces for the AI Content Studio provider layer."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.domain.enums import ProviderType
from app.domain.types import JsonDict
from app.providers.tts_capabilities import TTSCapabilities
from app.providers.tts_result import TTSSynthesisResult
from app.storage.manifest import ArtifactManifest


def _coerce_json_dict(value: Mapping[str, Any] | None) -> JsonDict:
    return dict(value or {})


def _stable_signature(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _slugify(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower())
    slug = slug.strip("-")
    return slug or "item"


@runtime_checkable
class LLMProvider(Protocol):
    provider_type: ProviderType
    provider_name: str

    def generate_text(
        self,
        prompt: str,
        context: JsonDict | None = None,
    ) -> str:
        """Generate deterministic free-form text."""

    def generate_structured(
        self,
        prompt: str,
        schema: JsonDict,
    ) -> JsonDict:
        """Generate deterministic structured output."""


@runtime_checkable
class TTSProvider(Protocol):
    provider_type: ProviderType
    provider_name: str

    def capabilities(self) -> TTSCapabilities:
        """Describe deterministic capability metadata without loading optional runtimes."""

    def synthesize(
        self,
        text: str,
        voice_config: JsonDict | None = None,
    ) -> TTSSynthesisResult:
        """Synthesize deterministic audio for the supplied text."""

    def effective_synthesis_identity(
        self,
        voice_config: JsonDict | None = None,
    ) -> JsonDict:
        """Describe the effective, JSON-compatible synthesis configuration."""


@runtime_checkable
class TranscriptionProvider(Protocol):
    provider_type: ProviderType
    provider_name: str

    def transcribe(self, audio_ref: str) -> JsonDict:
        """Transcribe deterministic audio metadata."""


@runtime_checkable
class CaptionProvider(Protocol):
    provider_type: ProviderType
    provider_name: str

    def generate_captions(
        self,
        audio_ref: str,
        transcript_ref: str,
    ) -> JsonDict:
        """Generate deterministic caption payloads."""


@runtime_checkable
class AssetProvider(Protocol):
    provider_type: ProviderType
    provider_name: str

    def find_assets(self, query: str) -> tuple[JsonDict, ...]:
        """Return deterministic candidate assets for a query."""

    def prepare_asset(self, asset_ref: str) -> JsonDict:
        """Prepare a selected asset for downstream use."""


@runtime_checkable
class VideoRendererProvider(Protocol):
    provider_type: ProviderType
    provider_name: str

    def render(
        self,
        scene_plan: JsonDict,
        audio_ref: str | None = None,
        captions_ref: str | None = None,
    ) -> JsonDict:
        """Render deterministic video metadata."""


@runtime_checkable
class StorageProvider(Protocol):
    provider_type: ProviderType
    provider_name: str

    def save_artifact(
        self,
        name: str,
        content: bytes | str,
        metadata: JsonDict | None = None,
    ) -> ArtifactManifest:
        """Persist artifact content and return its manifest."""

    def read_artifact(self, key: str) -> bytes:
        """Read an artifact by storage key."""

    def list_artifacts(self, prefix: str = "") -> tuple[ArtifactManifest, ...]:
        """List stored artifacts."""


@runtime_checkable
class PublishingProvider(Protocol):
    provider_type: ProviderType
    provider_name: str

    def publish(
        self,
        export_bundle: "PublishingRequest | JsonDict",
        target: str | None = None,
    ) -> "PublicationResult | JsonDict":
        """Publish an export bundle to a target platform."""


@dataclass(frozen=True, slots=True)
class PublishingRequest:
    """Typed request containing an approved platform export handoff."""

    handoff: JsonDict
    target: str
    idempotency_key: str

    @classmethod
    def create(
        cls,
        handoff: Mapping[str, Any],
        *,
        target: str | None = None,
    ) -> "PublishingRequest":
        normalized = dict(handoff)
        effective_target = str(target or normalized.get("platform") or "").strip().lower()
        if not effective_target:
            raise ValueError("PublishingRequest target platform is required.")
        identity = str(normalized.get("idempotencyKey") or "").strip()
        if not identity:
            identity = hashlib.sha256(
                _stable_signature(normalized).encode("utf-8")
            ).hexdigest()
        return cls(handoff=normalized, target=effective_target, idempotency_key=identity)

    @property
    def approved(self) -> bool:
        return self.handoff.get("approved") is True


@dataclass(frozen=True, slots=True)
class PublicationResult:
    """Provider-neutral publication result returned by concrete adapters."""

    provider: str
    platform: str
    video_id: str
    publish_ref: str
    privacy_status: str
    caption_upload: JsonDict
    idempotency_key: str
    status: str = "published"

    def to_payload(self) -> JsonDict:
        return {
            "provider": self.provider,
            "platform": self.platform,
            "video_id": self.video_id,
            "publish_ref": self.publish_ref,
            "privacy_status": self.privacy_status,
            "caption_upload": dict(self.caption_upload),
            "idempotency_key": self.idempotency_key,
            "status": self.status,
        }

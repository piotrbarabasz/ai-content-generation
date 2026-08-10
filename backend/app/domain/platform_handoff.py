"""Serializable platform handoffs built from generic export bundles."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any, Protocol

from app.domain.base import DomainValidationError
from app.domain.export_config import ExportConfig, normalize_language
from app.domain.types import JsonDict


_SECRET_KEY = re.compile(r"(?:credential|secret|token|authorization|api[_-]?key)", re.I)
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def _text(value: object, *, field_name: str) -> str:
    normalized = " ".join(str(value).strip().split())
    if not normalized:
        raise DomainValidationError(f"Platform handoff {field_name} is required.")
    return normalized


def _safe_reference(value: object, *, field_name: str) -> str:
    reference = str(value).strip().replace("\\", "/")
    if not reference:
        raise DomainValidationError(f"Platform handoff {field_name} is required.")
    if reference.startswith("/") or _WINDOWS_ABSOLUTE.match(str(value).strip()):
        raise DomainValidationError("Platform handoff artifact references must be relative.")
    if ".." in PurePosixPath(reference).parts:
        raise DomainValidationError("Platform handoff artifact references cannot traverse directories.")
    return reference


def _reject_sensitive_keys(value: object, *, path: str = "handoff") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _SECRET_KEY.search(str(key)):
                raise DomainValidationError(
                    f"Platform handoff cannot persist sensitive field {path}.{key}."
                )
            _reject_sensitive_keys(nested, path=f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, nested in enumerate(value):
            _reject_sensitive_keys(nested, path=f"{path}[{index}]")


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    available: bool
    name: str
    storage_key: str | None = None
    checksum: str | None = None
    size_bytes: int | None = None
    reason: str | None = None

    @classmethod
    def present(cls, payload: Mapping[str, object], *, fallback_name: str) -> "ArtifactReference":
        raw_name = str(payload.get("name") or payload.get("artifact_name") or fallback_name).strip()
        if raw_name.startswith("/") or _WINDOWS_ABSOLUTE.match(raw_name):
            raise DomainValidationError("Platform handoff artifact names must not be absolute paths.")
        name = PurePosixPath(raw_name.replace("\\", "/")).name
        if not name:
            raise DomainValidationError("Platform handoff artifact name is required.")
        storage_key = _safe_reference(
            payload.get("storage_key") or payload.get("storageKey") or "",
            field_name=f"{name} storage_key",
        )
        checksum = str(payload.get("checksum") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise DomainValidationError(
                f"Platform handoff artifact {name} requires a SHA-256 checksum."
            )
        size_value = payload.get("size_bytes", payload.get("sizeBytes"))
        try:
            size_bytes = int(size_value) if size_value is not None else None
        except (TypeError, ValueError) as exc:
            raise DomainValidationError(
                "Platform handoff artifact size must be an integer."
            ) from exc
        if size_bytes is not None and size_bytes < 0:
            raise DomainValidationError("Platform handoff artifact size cannot be negative.")
        return cls(True, name, storage_key, checksum, size_bytes, None)

    @classmethod
    def missing(cls, name: str, reason: str = "not_produced") -> "ArtifactReference":
        return cls(False, name, reason=reason)

    def to_payload(self) -> JsonDict:
        payload: JsonDict = {"available": self.available, "name": self.name}
        if self.available:
            payload.update(
                {
                    "storageKey": self.storage_key,
                    "checksum": self.checksum,
                    "sizeBytes": self.size_bytes,
                }
            )
        else:
            payload["reason"] = self.reason or "not_produced"
        return payload


@dataclass(frozen=True, slots=True)
class YouTubeMetadata:
    title: str
    description: str
    tags: tuple[str, ...] = ()
    category_id: str = "22"
    privacy_status: str = "unlisted"
    made_for_kids: bool = False
    contains_synthetic_media: bool = False

    @classmethod
    def create(
        cls,
        *,
        title: str,
        description: str,
        tags: Sequence[str] | None = None,
        category_id: str = "22",
        privacy_status: str = "unlisted",
        made_for_kids: bool = False,
        contains_synthetic_media: bool = False,
    ) -> "YouTubeMetadata":
        normalized_title = _text(title, field_name="metadata title")
        normalized_description = str(description).strip()
        if len(normalized_title) > 100 or any(char in normalized_title for char in "<>"):
            raise DomainValidationError("YouTube title must be at most 100 characters and omit < and >.")
        if len(normalized_description.encode("utf-8")) > 5000 or any(
            char in normalized_description for char in "<>"
        ):
            raise DomainValidationError(
                "YouTube description must be at most 5000 UTF-8 bytes and omit < and >."
            )
        normalized_tags = tuple(_text(tag, field_name="metadata tag") for tag in (tags or ()))
        if len(set(normalized_tags)) != len(normalized_tags):
            raise DomainValidationError("YouTube metadata tags must be unique.")
        serialized_tag_length = sum(
            len(tag) + (2 if " " in tag else 0) for tag in normalized_tags
        ) + max(len(normalized_tags) - 1, 0)
        if serialized_tag_length > 500:
            raise DomainValidationError("YouTube metadata tags exceed the 500-character limit.")
        if not str(category_id).isdigit():
            raise DomainValidationError("YouTube category_id must be numeric.")
        if privacy_status not in {"private", "unlisted", "public"}:
            raise DomainValidationError("YouTube privacy status must be private, unlisted or public.")
        if not isinstance(made_for_kids, bool) or not isinstance(contains_synthetic_media, bool):
            raise DomainValidationError("YouTube audience settings must be booleans.")
        return cls(
            title=normalized_title,
            description=normalized_description,
            tags=normalized_tags,
            category_id=_text(category_id, field_name="category_id"),
            privacy_status=privacy_status,
            made_for_kids=made_for_kids,
            contains_synthetic_media=contains_synthetic_media,
        )

    def to_payload(self) -> JsonDict:
        return {
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "categoryId": self.category_id,
            "privacyStatus": self.privacy_status,
            "madeForKids": self.made_for_kids,
            "containsSyntheticMedia": self.contains_synthetic_media,
        }


@dataclass(frozen=True, slots=True)
class PlatformHandoff:
    platform: str
    source_language: str
    export_id: str
    metadata: JsonDict
    artifacts: JsonDict
    localization: JsonDict
    approved: bool
    idempotency_key: str
    schema_version: int = 1

    def to_payload(self) -> JsonDict:
        payload: JsonDict = {
            "schemaVersion": self.schema_version,
            "platform": self.platform,
            "exportId": self.export_id,
            "sourceLanguage": self.source_language,
            "metadata": dict(self.metadata),
            "artifacts": dict(self.artifacts),
            "localization": dict(self.localization),
            "approved": self.approved,
            "idempotencyKey": self.idempotency_key,
        }
        _reject_sensitive_keys(payload)
        return payload


class PlatformHandoffBuilder(Protocol):
    platform: str

    def build(
        self,
        export_manifest: Mapping[str, object],
        *,
        source_language: str,
        metadata: Mapping[str, object],
        export_config: ExportConfig,
    ) -> PlatformHandoff:
        """Build a platform-specific handoff from a generic export manifest."""


def _artifact(
    references: Mapping[str, object],
    *names: str,
    required: bool = False,
) -> ArtifactReference:
    for name in names:
        payload = references.get(name)
        if isinstance(payload, Mapping):
            return ArtifactReference.present(payload, fallback_name=name)
    if required:
        raise DomainValidationError(
            f"YouTube handoff requires an actual {names[0]} artifact."
        )
    return ArtifactReference.missing(names[0])


class YouTubeHandoffBuilder:
    """Adapter that maps a generic export bundle to an upload-ready YouTube handoff."""

    platform = "youtube"

    def build(
        self,
        export_manifest: Mapping[str, object],
        *,
        source_language: str,
        metadata: Mapping[str, object],
        export_config: ExportConfig,
    ) -> PlatformHandoff:
        _reject_sensitive_keys(metadata)
        source_language = normalize_language(
            source_language,
            field_name="source_language",
        )
        references = export_manifest.get("artifactReferences", {})
        if not isinstance(references, Mapping):
            raise DomainValidationError("Export manifest artifactReferences must be an object.")
        youtube_metadata = YouTubeMetadata.create(
            title=str(metadata.get("title", "")),
            description=str(metadata.get("description", "")),
            tags=(
                metadata.get("tags")
                if isinstance(metadata.get("tags"), Sequence)
                and not isinstance(metadata.get("tags"), (str, bytes))
                else ()
            ),
            category_id=str(metadata.get("categoryId", metadata.get("category_id", "22"))),
            privacy_status=str(metadata.get("privacyStatus", metadata.get("privacy_status", "unlisted"))),
            made_for_kids=metadata.get(
                "madeForKids", metadata.get("made_for_kids", False)
            ),
            contains_synthetic_media=metadata.get(
                "containsSyntheticMedia",
                metadata.get("contains_synthetic_media", False),
            ),
        )
        artifacts: JsonDict = {
            "video": _artifact(references, "render.mp4", "video.mp4", required=True).to_payload(),
            "voiceover": _artifact(references, "voiceover.wav").to_payload(),
            "captions": {
                "language": source_language,
                "json": _artifact(references, "captions.json").to_payload(),
                "srt": _artifact(references, f"captions.{source_language}.srt", "captions.srt").to_payload(),
            },
            "thumbnail": _artifact(
                references, "thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg"
            ).to_payload(),
        }
        approval_summary = export_manifest.get("approvalSummary", {})
        approved = False
        if isinstance(approval_summary, Mapping):
            approval_value = approval_summary.get(
                "finalExport", approval_summary.get("export", approval_summary.get("approved"))
            )
            approved = approval_value is True or str(approval_value).lower() == "approved"
        export_id = _text(export_manifest.get("exportId", ""), field_name="export_id")
        localization = export_config.to_payload()
        signature_payload = {
            "platform": self.platform,
            "exportId": export_id,
            "sourceLanguage": source_language,
            "metadata": youtube_metadata.to_payload(),
            "artifacts": artifacts,
            "localization": localization,
        }
        _reject_sensitive_keys(signature_payload)
        identity = sha256(
            json.dumps(
                signature_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return PlatformHandoff(
            platform=self.platform,
            source_language=source_language,
            export_id=export_id,
            metadata=youtube_metadata.to_payload(),
            artifacts=artifacts,
            localization=localization,
            approved=approved,
            idempotency_key=identity,
        )


__all__ = [
    "ArtifactReference",
    "PlatformHandoff",
    "PlatformHandoffBuilder",
    "YouTubeHandoffBuilder",
    "YouTubeMetadata",
]

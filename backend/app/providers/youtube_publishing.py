"""YouTube Data API publishing adapter with lazy optional dependencies."""

from __future__ import annotations

import io
import os
from collections.abc import Callable, Mapping
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any, Protocol

from app.domain.enums import ProviderType
from app.domain.export_config import normalize_language
from app.domain.types import JsonDict
from app.providers.interfaces import PublicationResult, PublishingRequest


class YouTubePublishingError(RuntimeError):
    """Safe provider-boundary failure without credential or payload leakage."""


class YouTubeTransport(Protocol):
    def upload_video(
        self,
        *,
        content: bytes,
        filename: str,
        body: JsonDict,
    ) -> Mapping[str, Any]: ...

    def upload_caption(
        self,
        *,
        video_id: str,
        content: bytes,
        filename: str,
        body: JsonDict,
    ) -> Mapping[str, Any]: ...


def _artifact_payload(handoff: Mapping[str, Any], *path: str) -> JsonDict:
    current: object = handoff
    for name in path:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(name)
    return dict(current) if isinstance(current, Mapping) else {}


def _required_storage_key(payload: Mapping[str, Any], *, kind: str) -> str:
    if payload.get("available") is not True:
        raise YouTubePublishingError(f"Approved YouTube handoff has no {kind} artifact.")
    storage_key = str(payload.get("storageKey") or "").strip().replace("\\", "/")
    if (
        not storage_key
        or storage_key.startswith("/")
        or ":" in storage_key.split("/", 1)[0]
        or ".." in PurePosixPath(storage_key).parts
    ):
        raise YouTubePublishingError(f"Approved YouTube handoff has an invalid {kind} reference.")
    return storage_key


def _read_verified_artifact(
    payload: Mapping[str, Any],
    *,
    kind: str,
    artifact_reader: Callable[[str], bytes],
) -> bytes:
    storage_key = _required_storage_key(payload, kind=kind)
    expected_checksum = str(payload.get("checksum") or "").strip().lower()
    if len(expected_checksum) != 64 or any(
        char not in "0123456789abcdef" for char in expected_checksum
    ):
        raise YouTubePublishingError(
            f"Approved YouTube handoff has no valid {kind} checksum."
        )
    content = artifact_reader(storage_key)
    if not isinstance(content, bytes):
        raise YouTubePublishingError(f"Artifact reader returned invalid {kind} content.")
    if sha256(content).hexdigest() != expected_checksum:
        raise YouTubePublishingError(f"Approved YouTube handoff {kind} checksum mismatch.")
    return content


def _metadata_bool(metadata: Mapping[str, Any], name: str, *, default: bool) -> bool:
    value = metadata.get(name, default)
    if not isinstance(value, bool):
        raise YouTubePublishingError(f"YouTube handoff metadata {name} must be a boolean.")
    return value


class GoogleYouTubeTransport:
    """Official Google client transport; imports and credential loading happen on first upload."""

    def __init__(
        self,
        *,
        credentials_env: str = "YOUTUBE_CREDENTIALS_FILE",
    ) -> None:
        self._credentials_env = credentials_env
        self._service: Any | None = None

    def _get_service(self) -> Any:
        if self._service is not None:
            return self._service
        credentials_path = os.environ.get(self._credentials_env, "").strip()
        if not credentials_path:
            raise YouTubePublishingError(
                f"YouTube credentials reference is missing; set {self._credentials_env}."
            )
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise YouTubePublishingError(
                "YouTube optional dependencies are not installed; install the youtube extra."
            ) from exc
        credentials = Credentials.from_authorized_user_file(
            credentials_path,
            # captions.insert requires youtube.force-ssl; the same scope also
            # authorizes videos.insert, so one explicit runtime grant covers both writes.
            scopes=["https://www.googleapis.com/auth/youtube.force-ssl"],
        )
        self._service = build(
            "youtube",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )
        return self._service

    @staticmethod
    def _media(content: bytes, *, mime_type: str) -> Any:
        try:
            from googleapiclient.http import MediaIoBaseUpload
        except ImportError as exc:
            raise YouTubePublishingError(
                "YouTube optional dependencies are not installed; install the youtube extra."
            ) from exc
        return MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=True)

    def upload_video(
        self,
        *,
        content: bytes,
        filename: str,
        body: JsonDict,
    ) -> Mapping[str, Any]:
        del filename
        return (
            self._get_service()
            .videos()
            .insert(
                part="snippet,status",
                body=body,
                media_body=self._media(content, mime_type="video/*"),
                notifySubscribers=False,
            )
            .execute()
        )

    def upload_caption(
        self,
        *,
        video_id: str,
        content: bytes,
        filename: str,
        body: JsonDict,
    ) -> Mapping[str, Any]:
        del video_id, filename
        return (
            self._get_service()
            .captions()
            .insert(
                part="snippet",
                body=body,
                media_body=self._media(content, mime_type="application/x-subrip"),
            )
            .execute()
        )


class YouTubePublishingProvider:
    """Publish approved handoffs using only documented YouTube Data API writes."""

    provider_type = ProviderType.PUBLISHING

    def __init__(
        self,
        provider_name: str = "youtube",
        *,
        transport: YouTubeTransport | None = None,
        artifact_reader: Callable[[str], bytes] | None = None,
        credentials_env: str = "YOUTUBE_CREDENTIALS_FILE",
    ) -> None:
        self.provider_name = provider_name
        self._transport = transport or GoogleYouTubeTransport(credentials_env=credentials_env)
        self._artifact_reader = artifact_reader

    def publish(
        self,
        export_bundle: PublishingRequest | JsonDict,
        target: str | None = None,
    ) -> PublicationResult:
        request = (
            export_bundle
            if isinstance(export_bundle, PublishingRequest)
            else PublishingRequest.create(export_bundle, target=target)
        )
        if request.target != "youtube":
            raise YouTubePublishingError("YouTube provider requires a YouTube handoff.")
        if not request.approved:
            raise YouTubePublishingError("Final export approval is required before publishing.")
        if self._artifact_reader is None:
            raise YouTubePublishingError("YouTube publishing requires an artifact reader.")

        handoff = request.handoff
        metadata = handoff.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise YouTubePublishingError("YouTube handoff metadata is invalid.")
        try:
            source_language = normalize_language(
                handoff.get("sourceLanguage") or "",
                field_name="source_language",
            )
        except ValueError as exc:
            raise YouTubePublishingError("YouTube handoff source language is invalid.") from exc
        video_artifact = _artifact_payload(handoff, "artifacts", "video")
        privacy_status = str(metadata.get("privacyStatus") or "unlisted")
        if privacy_status not in {"private", "unlisted", "public"}:
            raise YouTubePublishingError("YouTube handoff privacy status is invalid.")
        snippet: JsonDict = {
            "title": str(metadata.get("title") or ""),
            "description": str(metadata.get("description") or ""),
            "categoryId": str(metadata.get("categoryId") or "22"),
            # This is the title/description language. The Data API does not expose a
            # writable defaultAudioLanguage property for videos.insert/update.
            "defaultLanguage": source_language,
        }
        tags = metadata.get("tags")
        if isinstance(tags, list) and tags:
            snippet["tags"] = [str(tag) for tag in tags]
        body: JsonDict = {
            "snippet": snippet,
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": _metadata_bool(
                    metadata, "madeForKids", default=False
                ),
                "containsSyntheticMedia": _metadata_bool(
                    metadata, "containsSyntheticMedia", default=False
                ),
            },
        }

        try:
            video_response = self._transport.upload_video(
                content=_read_verified_artifact(
                    video_artifact,
                    kind="video",
                    artifact_reader=self._artifact_reader,
                ),
                filename=str(video_artifact.get("name") or "video.mp4"),
                body=body,
            )
            video_id = str(video_response.get("id") or "").strip()
            if not video_id:
                raise YouTubePublishingError("YouTube videos.insert returned no video id.")
            response_status = video_response.get("status")
            if isinstance(response_status, Mapping):
                privacy_status = str(
                    response_status.get("privacyStatus") or privacy_status
                )

            caption_upload: JsonDict = {"status": "not_requested"}
            caption_artifact = _artifact_payload(handoff, "artifacts", "captions", "srt")
            if caption_artifact.get("available") is True:
                caption_response = self._transport.upload_caption(
                    video_id=video_id,
                    content=_read_verified_artifact(
                        caption_artifact,
                        kind="caption",
                        artifact_reader=self._artifact_reader,
                    ),
                    filename=str(caption_artifact.get("name") or "captions.srt"),
                    body={
                        "snippet": {
                            "videoId": video_id,
                            "language": source_language,
                            "name": f"{source_language.upper()} source captions",
                            "isDraft": False,
                        }
                    },
                )
                caption_upload = {
                    "status": "uploaded",
                    "caption_id": str(caption_response.get("id") or ""),
                    "language": source_language,
                }
        except YouTubePublishingError:
            raise
        except Exception as exc:  # noqa: BLE001 - sanitize the external provider boundary.
            raise YouTubePublishingError(
                f"YouTube publication failed ({type(exc).__name__})."
            ) from None

        return PublicationResult(
            provider=self.provider_name,
            platform="youtube",
            video_id=video_id,
            publish_ref=f"https://www.youtube.com/watch?v={video_id}",
            privacy_status=privacy_status,
            caption_upload=caption_upload,
            idempotency_key=request.idempotency_key,
        )


__all__ = [
    "GoogleYouTubeTransport",
    "YouTubePublishingError",
    "YouTubePublishingProvider",
    "YouTubeTransport",
]

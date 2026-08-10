"""Application-level API dependencies and settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Request

from app.providers.tts_catalog import build_tts_catalog
from app.tts.preview import TTSPreviewService


@dataclass(slots=True, frozen=True)
class ApiSettings:
    title: str = "AI Content Studio API"
    version: str = "0.1.0"
    description: str = "FastAPI application for the AI Content Studio MVP."
    api_prefix: str = "/api/v1"
    tts_preview_root: Path = Path(".specify/runtime/tts-previews")


@dataclass(slots=True, frozen=True)
class ApiDependencies:
    settings: ApiSettings
    tts_preview_service: TTSPreviewService | None = field(default=None, compare=False)


def get_api_settings() -> ApiSettings:
    return ApiSettings()


def build_api_dependencies(
    settings: ApiSettings | None = None,
    *,
    tts_preview_service: TTSPreviewService | None = None,
) -> ApiDependencies:
    resolved_settings = settings or get_api_settings()
    preview_service = tts_preview_service
    if preview_service is None:
        preview_service = TTSPreviewService(
            catalog=build_tts_catalog(),
            preview_root=resolved_settings.tts_preview_root,
        )
    return ApiDependencies(
        settings=resolved_settings,
        tts_preview_service=preview_service,
    )


def get_tts_preview_service(request: Request) -> TTSPreviewService:
    """Resolve the app-scoped preview service through FastAPI's dependency seam."""

    service = request.app.state.api_dependencies.tts_preview_service
    if service is None:  # Defensive guard for manually assembled test applications.
        raise RuntimeError("TTS preview service is unavailable.")
    return service


__all__ = [
    "ApiDependencies",
    "ApiSettings",
    "build_api_dependencies",
    "get_api_settings",
    "get_tts_preview_service",
]

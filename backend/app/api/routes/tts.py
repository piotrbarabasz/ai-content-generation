"""Provider-neutral TTS catalog and preview endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import get_tts_preview_service
from app.api.schemas import TTSCatalog, VoicePreview, VoicePreviewCreateRequest
from app.providers.tts_catalog import build_tts_catalog
from app.tts.catalog import TTSCatalog as TTSCatalogDomain
from app.tts.catalog import TTSCatalogError
from app.tts.preview import (
    TTSPreviewError,
    TTSPreviewNotFoundError,
    TTSPreviewResult,
    TTSPreviewService,
)

from .projects import register_api_router

router = APIRouter(tags=["tts"])


def _catalog_schema(catalog: TTSCatalogDomain) -> TTSCatalog:
    return TTSCatalog.model_validate(catalog.to_payload())


def _preview_schema(result: TTSPreviewResult) -> VoicePreview:
    return VoicePreview.model_validate(
        {
            **result.to_payload(),
            "audio_url": f"/api/v1/tts/previews/{result.preview_id}/audio",
        }
    )


def _preview_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, TTSPreviewNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TTS preview was not found.",
        )
    if isinstance(exc, TTSPreviewError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="TTS preview request failed.",
    )


@router.get(
    "/tts/catalog",
    response_model=TTSCatalog,
    response_model_exclude_none=True,
)
def get_tts_catalog(
    language: Annotated[
        str | None,
        Query(description="Optional language filter for provider, model and voice compatibility."),
    ] = None,
    usage_policy: Annotated[
        str | None,
        Query(alias="usagePolicy", description="Optional usage-policy filter for catalog entries."),
    ] = None,
) -> TTSCatalog:
    try:
        catalog = build_tts_catalog().filter(language=language, usage_policy=usage_policy)
    except TTSCatalogError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _catalog_schema(catalog)


@router.post(
    "/tts/previews",
    response_model=VoicePreview,
)
def create_tts_preview(
    request: VoicePreviewCreateRequest,
    service: Annotated[TTSPreviewService, Depends(get_tts_preview_service)],
) -> VoicePreview:
    try:
        result = service.synthesize_preview(
            provider=request.provider,
            model=request.model,
            voice=request.voice,
            language=request.language,
            tempo=request.tempo,
            text=request.text,
            reference_audio_artifact_id=request.reference_audio_artifact_id,
            synthesis_settings=request.synthesis_settings,
        )
    except Exception as exc:
        raise _preview_http_error(exc) from exc
    return _preview_schema(result)


@router.get(
    "/tts/previews/{preview_id}/audio",
    response_class=Response,
    responses={
        status.HTTP_200_OK: {
            "content": {"audio/wav": {}},
            "description": "Validated preview WAV audio.",
        },
        status.HTTP_404_NOT_FOUND: {"description": "Preview not found."},
    },
)
def get_tts_preview_audio(
    preview_id: str,
    service: Annotated[TTSPreviewService, Depends(get_tts_preview_service)],
) -> Response:
    try:
        audio_bytes = service.read_audio(preview_id)
    except Exception as exc:
        raise _preview_http_error(exc) from exc
    return Response(content=audio_bytes, media_type="audio/wav")


register_api_router(router)


__all__ = [
    "create_tts_preview",
    "get_tts_catalog",
    "get_tts_preview_audio",
    "router",
]

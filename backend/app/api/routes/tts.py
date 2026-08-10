"""TTS catalog endpoints for the AI Content Studio API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas import TTSCatalog
from app.providers.tts_catalog import build_tts_catalog
from app.tts.catalog import TTSCatalog as TTSCatalogDomain
from app.tts.catalog import TTSCatalogError

from .projects import register_api_router

router = APIRouter(tags=["tts"])


def _catalog_schema(catalog: TTSCatalogDomain) -> TTSCatalog:
    return TTSCatalog.model_validate(catalog.to_payload())


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


register_api_router(router)


__all__ = [
    "get_tts_catalog",
    "router",
]

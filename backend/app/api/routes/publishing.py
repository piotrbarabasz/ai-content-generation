"""Manual publishing-localization handoff endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from app.api.schemas import ApiSchema, LocalizationHandoffSchema
from app.domain.base import DomainValidationError
from app.domain.localization_handoff import LocalizationHandoff

from .projects import register_api_router


router = APIRouter(tags=["publishing"])
LOCALIZATION_HANDOFFS: dict[str, LocalizationHandoff] = {}


class PlatformStatusRequest(ApiSchema):
    status: str = Field(min_length=1)


class LocalizationDecisionRequest(ApiSchema):
    decision: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    comment: str = ""


class CustomAudioRequest(ApiSchema):
    artifact_reference: str = Field(min_length=1)
    checksum: str = Field(min_length=64, max_length=64)
    approved_label: str = Field(min_length=1)
    provenance: str = Field(min_length=1)


def reset_publishing_state() -> None:
    LOCALIZATION_HANDOFFS.clear()


def register_localization_handoff(handoff: LocalizationHandoff) -> LocalizationHandoff:
    LOCALIZATION_HANDOFFS[handoff.id] = handoff
    return handoff


def _get_handoff(handoff_id: str) -> LocalizationHandoff:
    try:
        return LOCALIZATION_HANDOFFS[handoff_id]
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Localization handoff not found",
        ) from exc


def _schema(handoff: LocalizationHandoff) -> LocalizationHandoffSchema:
    return LocalizationHandoffSchema.model_validate(handoff.to_payload())


def _apply(handoff_id: str, operation) -> LocalizationHandoffSchema:
    handoff = _get_handoff(handoff_id)
    try:
        operation(handoff)
    except DomainValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _schema(handoff)


@router.get(
    "/publishing/localization-handoffs/{handoff_id}",
    response_model=LocalizationHandoffSchema,
)
def get_localization_handoff(handoff_id: str) -> LocalizationHandoffSchema:
    return _schema(_get_handoff(handoff_id))


@router.post(
    "/publishing/localization-handoffs/{handoff_id}/targets/{language}/platform-status",
    response_model=LocalizationHandoffSchema,
)
def update_localization_platform_status(
    handoff_id: str,
    language: str,
    request: PlatformStatusRequest,
) -> LocalizationHandoffSchema:
    return _apply(
        handoff_id,
        lambda handoff: handoff.update_platform_status(
            language=language,
            status=request.status,
        ),
    )


@router.post(
    "/publishing/localization-handoffs/{handoff_id}/targets/{language}/decisions",
    response_model=LocalizationHandoffSchema,
)
def decide_localization(
    handoff_id: str,
    language: str,
    request: LocalizationDecisionRequest,
) -> LocalizationHandoffSchema:
    return _apply(
        handoff_id,
        lambda handoff: handoff.decide(
            language=language,
            decision=request.decision,
            reviewer_id=request.reviewer_id,
            comment=request.comment,
        ),
    )


@router.post(
    "/publishing/localization-handoffs/{handoff_id}/targets/{language}/custom-audio",
    response_model=LocalizationHandoffSchema,
)
def supply_localization_custom_audio(
    handoff_id: str,
    language: str,
    request: CustomAudioRequest,
) -> LocalizationHandoffSchema:
    return _apply(
        handoff_id,
        lambda handoff: handoff.supply_custom_audio(
            language=language,
            artifact_reference=request.artifact_reference,
            checksum=request.checksum,
            approved_label=request.approved_label,
            provenance=request.provenance,
        ),
    )


register_api_router(router)


__all__ = [
    "LOCALIZATION_HANDOFFS",
    "decide_localization",
    "get_localization_handoff",
    "register_localization_handoff",
    "reset_publishing_state",
    "supply_localization_custom_audio",
    "update_localization_platform_status",
]

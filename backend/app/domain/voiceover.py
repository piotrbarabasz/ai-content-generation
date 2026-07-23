"""Voiceover domain model."""

from dataclasses import dataclass
from datetime import datetime

from app.domain.base import DomainEntity, DomainValidationError, new_id


@dataclass(slots=True)
class Voiceover(DomainEntity):
    workflow_run_id: str = ""
    text_reference_id: str = ""
    provider: str = ""
    audio_storage_key: str = ""
    duration_seconds: float = 0.0
    approved_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        workflow_run_id: str,
        text_reference_id: str,
        provider: str,
        audio_storage_key: str,
        duration_seconds: float,
        approved_at: datetime | None = None,
    ) -> "Voiceover":
        if not workflow_run_id.strip():
            raise DomainValidationError("Voiceover workflow_run_id is required.")
        if not text_reference_id.strip():
            raise DomainValidationError("Voiceover text_reference_id is required.")
        if not provider.strip():
            raise DomainValidationError("Voiceover provider is required.")
        if not audio_storage_key.strip():
            raise DomainValidationError("Voiceover audio_storage_key is required.")
        if duration_seconds < 0:
            raise DomainValidationError("Voiceover duration_seconds cannot be negative.")

        return cls(
            id=new_id("voiceover"),
            workflow_run_id=workflow_run_id,
            text_reference_id=text_reference_id,
            provider=provider,
            audio_storage_key=audio_storage_key,
            duration_seconds=duration_seconds,
            approved_at=approved_at,
        )

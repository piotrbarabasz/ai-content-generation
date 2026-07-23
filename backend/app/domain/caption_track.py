"""Caption track domain model."""

from dataclasses import dataclass
from datetime import datetime

from app.domain.base import DomainEntity, DomainValidationError, new_id


@dataclass(slots=True)
class CaptionTrack(DomainEntity):
    workflow_run_id: str = ""
    provider: str = ""
    caption_storage_key: str = ""
    approved_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        workflow_run_id: str,
        provider: str,
        caption_storage_key: str,
        approved_at: datetime | None = None,
    ) -> "CaptionTrack":
        if not workflow_run_id.strip():
            raise DomainValidationError("CaptionTrack workflow_run_id is required.")
        if not provider.strip():
            raise DomainValidationError("CaptionTrack provider is required.")
        if not caption_storage_key.strip():
            raise DomainValidationError("CaptionTrack caption_storage_key is required.")

        return cls(
            id=new_id("caption_track"),
            workflow_run_id=workflow_run_id,
            provider=provider,
            caption_storage_key=caption_storage_key,
            approved_at=approved_at,
        )

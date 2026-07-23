"""Video render domain model."""

from dataclasses import dataclass
from datetime import datetime

from app.domain.base import DomainEntity, DomainValidationError, new_id


@dataclass(slots=True)
class VideoRender(DomainEntity):
    workflow_run_id: str = ""
    render_storage_key: str = ""
    duration_seconds: float = 0.0
    format: str = ""
    approved_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        workflow_run_id: str,
        render_storage_key: str,
        duration_seconds: float,
        format: str,
        approved_at: datetime | None = None,
    ) -> "VideoRender":
        if not workflow_run_id.strip():
            raise DomainValidationError("VideoRender workflow_run_id is required.")
        if not render_storage_key.strip():
            raise DomainValidationError("VideoRender render_storage_key is required.")
        if duration_seconds < 0:
            raise DomainValidationError("VideoRender duration_seconds cannot be negative.")
        if not format.strip():
            raise DomainValidationError("VideoRender format is required.")

        return cls(
            id=new_id("video_render"),
            workflow_run_id=workflow_run_id,
            render_storage_key=render_storage_key,
            duration_seconds=duration_seconds,
            format=format,
            approved_at=approved_at,
        )

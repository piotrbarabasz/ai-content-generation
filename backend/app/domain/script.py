"""Script domain model."""

from dataclasses import dataclass
from datetime import datetime

from app.domain.base import DomainEntity, DomainValidationError, new_id


@dataclass(slots=True)
class Script(DomainEntity):
    workflow_run_id: str = ""
    text: str = ""
    version: int = 1
    language: str = "en"
    word_count: int = 0
    approved_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        workflow_run_id: str,
        text: str,
        version: int = 1,
        language: str = "en",
        word_count: int = 0,
        approved_at: datetime | None = None,
    ) -> "Script":
        if not workflow_run_id.strip():
            raise DomainValidationError("Script workflow_run_id is required.")
        if not text.strip():
            raise DomainValidationError("Script text is required.")
        if version < 1:
            raise DomainValidationError("Script version must be greater than zero.")
        if not language.strip():
            raise DomainValidationError("Script language is required.")
        if word_count < 0:
            raise DomainValidationError("Script word_count cannot be negative.")

        return cls(
            id=new_id("script"),
            workflow_run_id=workflow_run_id,
            text=text,
            version=version,
            language=language,
            word_count=word_count,
            approved_at=approved_at,
        )

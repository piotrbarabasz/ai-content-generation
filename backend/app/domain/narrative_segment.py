"""Narrative segment domain model."""

from dataclasses import dataclass

from app.domain.base import DomainEntity, DomainValidationError, new_id


@dataclass(slots=True)
class NarrativeSegment(DomainEntity):
    workflow_run_id: str = ""
    order: int = 1
    title: str = ""
    text: str = ""
    role: str = ""
    duration_estimate: float = 0.0

    @classmethod
    def create(
        cls,
        *,
        workflow_run_id: str,
        order: int,
        title: str,
        text: str,
        role: str,
        duration_estimate: float = 0.0,
    ) -> "NarrativeSegment":
        if not workflow_run_id.strip():
            raise DomainValidationError("NarrativeSegment workflow_run_id is required.")
        if order < 1:
            raise DomainValidationError("NarrativeSegment order must be greater than zero.")
        if not title.strip():
            raise DomainValidationError("NarrativeSegment title is required.")
        if not text.strip():
            raise DomainValidationError("NarrativeSegment text is required.")
        if not role.strip():
            raise DomainValidationError("NarrativeSegment role is required.")
        if duration_estimate < 0:
            raise DomainValidationError("NarrativeSegment duration_estimate cannot be negative.")

        return cls(
            id=new_id("narrative_segment"),
            workflow_run_id=workflow_run_id,
            order=order,
            title=title,
            text=text,
            role=role,
            duration_estimate=duration_estimate,
        )

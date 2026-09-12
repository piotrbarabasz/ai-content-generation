"""Narrative segment domain model."""

from dataclasses import dataclass, replace

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


@dataclass(frozen=True, slots=True)
class SectionRevision:
    """Immutable content of a NarrativeSegment's stable editorial identity.

    Unlike the legacy run payload above, this value needs no workflow run.
    Previous values remain available to their owning script snapshots; retaining
    those snapshots across restarts is a repository responsibility, not this model's.
    """

    id: str
    section_id: str
    project_id: str
    title: str
    text: str
    role: str
    parent_revision_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("id", "section_id", "project_id", "title", "text", "role"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(f"SectionRevision {name} must be non-empty text.")
        if self.parent_revision_id is not None:
            if not isinstance(self.parent_revision_id, str) or not self.parent_revision_id.strip():
                raise DomainValidationError("SectionRevision parent_revision_id must be non-empty text.")
            if self.parent_revision_id == self.id:
                raise DomainValidationError("SectionRevision cannot be its own parent.")

    @classmethod
    def create(cls, *, project_id: str, title: str, text: str, role: str) -> "SectionRevision":
        """Start a new section; subsequent edits must keep its section_id."""
        return cls(
            id=new_id("section_revision"),
            section_id=new_id("narrative_segment"),
            project_id=project_id,
            title=title,
            text=text,
            role=role,
        )

    @classmethod
    def from_segment(cls, segment: NarrativeSegment, *, project_id: str) -> "SectionRevision":
        """Copy a legacy segment, retaining its ID but not its mutable payload.

        Order belongs to ScriptRevision. Run ID and duration estimates remain
        legacy execution data, not editorial identity or measured scene timing.
        """
        if not isinstance(segment, NarrativeSegment):
            raise DomainValidationError("Expected a NarrativeSegment for revision import.")
        return cls(
            id=new_id("section_revision"),
            section_id=segment.id,
            project_id=project_id,
            title=segment.title,
            text=segment.text,
            role=segment.role,
        )

    def revise(
        self, *, text: str, title: str | None = None, role: str | None = None
    ) -> "SectionRevision":
        """Return edited content with the same section identity and explicit lineage."""
        return replace(
            self,
            id=new_id("section_revision"),
            parent_revision_id=self.id,
            text=text,
            title=self.title if title is None else title,
            role=self.role if role is None else role,
        )

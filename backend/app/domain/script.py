"""Script domain model."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from app.domain.base import DomainEntity, DomainValidationError, new_id
from app.domain.narrative_segment import NarrativeSegment, SectionRevision


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


@dataclass(frozen=True, slots=True)
class ScriptRevision:
    """An immutable ordered selection of exact section revision values.

    `sections` is the active selection in this snapshot, not a mutable history
    table. Editing/selection/reorder returns a new snapshot and leaves the old
    one (including its B1 value) intact. Empty selections represent draft scripts.
    """

    id: str
    script_id: str
    project_id: str
    sections: tuple[SectionRevision, ...]
    language: str = "en"
    parent_revision_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("id", "script_id", "project_id", "language"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(f"ScriptRevision {name} must be non-empty text.")
        if self.parent_revision_id is not None:
            if not isinstance(self.parent_revision_id, str) or not self.parent_revision_id.strip():
                raise DomainValidationError("ScriptRevision parent_revision_id must be non-empty text.")
            if self.parent_revision_id == self.id:
                raise DomainValidationError("ScriptRevision cannot be its own parent.")
        if not isinstance(self.sections, (list, tuple)):
            raise DomainValidationError("ScriptRevision sections must be a list or tuple of revisions.")
        # Snapshot caller-owned lists as well as validating direct construction.
        object.__setattr__(self, "sections", tuple(self.sections))
        section_ids: set[str] = set()
        revision_ids: set[str] = set()
        for section in self.sections:
            if not isinstance(section, SectionRevision):
                raise DomainValidationError("ScriptRevision sections must contain SectionRevision values.")
            if section.project_id != self.project_id:
                raise DomainValidationError("Section revision belongs to a different project.")
            if section.section_id in section_ids:
                raise DomainValidationError("ScriptRevision cannot select a section more than once.")
            if section.id in revision_ids:
                raise DomainValidationError("ScriptRevision cannot contain duplicate revision IDs.")
            section_ids.add(section.section_id)
            revision_ids.add(section.id)

    @classmethod
    def create(
        cls, *, project_id: str, sections: Sequence[SectionRevision] = (), language: str = "en"
    ) -> "ScriptRevision":
        if isinstance(sections, (str, bytes)) or not isinstance(sections, Sequence):
            raise DomainValidationError("Script creation requires a sequence of section revisions.")
        return cls(
            id=new_id("script_revision"),
            script_id=new_id("script"),
            project_id=project_id,
            sections=tuple(sections),
            language=language,
        )

    @classmethod
    def from_legacy(
        cls, script: Script, *, project_id: str, segments: Sequence[NarrativeSegment]
    ) -> "ScriptRevision":
        """Import structured legacy content without changing the legacy models.

        The supplied segments are authoritative content; the legacy flat string
        is not parsed or guessed into sections. Preserve script/segment IDs and
        language, order by legacy ordinal, and require a single matching run.
        """
        if (
            not isinstance(script, Script)
            or not isinstance(script.workflow_run_id, str)
            or not script.workflow_run_id.strip()
        ):
            raise DomainValidationError("Legacy import requires a Script with a workflow run.")
        if isinstance(segments, (str, bytes)) or not isinstance(segments, Sequence):
            raise DomainValidationError("Legacy import requires a sequence of narrative segments.")
        segments = tuple(segments)
        if not segments:
            raise DomainValidationError("Legacy import requires narrative segments.")
        orders: set[int] = set()
        for segment in segments:
            if not isinstance(segment, NarrativeSegment):
                raise DomainValidationError("Legacy import requires NarrativeSegment values.")
            if segment.workflow_run_id != script.workflow_run_id:
                raise DomainValidationError("Legacy segment belongs to a different workflow run.")
            if (
                isinstance(segment.order, bool)
                or not isinstance(segment.order, int)
                or segment.order < 1
            ):
                raise DomainValidationError("Legacy segment order must be a positive integer.")
            if segment.order in orders:
                raise DomainValidationError("Legacy segment order must be unique.")
            orders.add(segment.order)
        return cls(
            id=new_id("script_revision"),
            script_id=script.id,
            project_id=project_id,
            language=script.language,
            sections=tuple(
                SectionRevision.from_segment(segment, project_id=project_id)
                for segment in sorted(segments, key=lambda segment: segment.order)
            ),
        )

    def section(self, section_id: str) -> SectionRevision:
        """Resolve the selected revision, failing rather than guessing unknown IDs."""
        for section in self.sections:
            if section.section_id == section_id:
                return section
        raise DomainValidationError(f"Unknown section ID: {section_id!r}.")

    def select_section_revision(self, revision: SectionRevision) -> "ScriptRevision":
        """Select a new or retained revision of a section already in this script."""
        if not isinstance(revision, SectionRevision):
            raise DomainValidationError("Selection requires a SectionRevision.")
        current = self.section(revision.section_id)
        if revision.project_id != self.project_id:
            raise DomainValidationError("Section revision belongs to a different project.")
        if revision.id == current.id and revision != current:
            raise DomainValidationError("A revision ID cannot identify different content.")
        return self._with_sections(
            tuple(
                revision if section.section_id == revision.section_id else section
                for section in self.sections
            )
        )

    def edit_section(
        self, section_id: str, *, text: str, title: str | None = None, role: str | None = None
    ) -> "ScriptRevision":
        revision = self.section(section_id).revise(text=text, title=title, role=role)
        return self.select_section_revision(revision)

    def reorder(self, section_ids: Sequence[str]) -> "ScriptRevision":
        """Return a pure permutation: no insertion, removal or identity changes."""
        if isinstance(section_ids, str) or not isinstance(section_ids, Sequence):
            raise DomainValidationError("Reorder requires a sequence of section IDs.")
        requested = tuple(section_ids)
        if any(not isinstance(value, str) for value in requested):
            raise DomainValidationError("Reorder section IDs must be strings.")
        existing = {section.section_id: section for section in self.sections}
        if len(requested) != len(existing) or set(requested) != set(existing):
            raise DomainValidationError("Reorder must contain every section ID exactly once.")
        return self._with_sections(tuple(existing[section_id] for section_id in requested))

    def _with_sections(self, sections: tuple[SectionRevision, ...]) -> "ScriptRevision":
        if sections == self.sections:
            return self
        return replace(self, id=new_id("script_revision"), parent_revision_id=self.id, sections=sections)

"""Pure section-order snapshots and their dependency impact."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass

from app.domain.base import DomainValidationError
from app.domain.dependencies import content_fingerprint
from app.domain.script import ScriptRevision


PROJECT_OUTPUTS = ("timeline", "video_render")


def _identities(value, label):
    if (type(value) is not tuple or any(type(item) is not str or not item.strip() for item in value)
            or len(set(value)) != len(value)):
        raise DomainValidationError(f"{label} must be a unique immutable section ID tuple.")


@dataclass(frozen=True, slots=True)
class SectionOrderImpact:
    """D005 inputs and downstream work implied by one requested permutation."""

    parent_script_revision_id: str
    script_revision_id: str
    previous_order: tuple[str, ...]
    section_order: tuple[str, ...]
    order_fingerprint: str
    reusable_section_ids: tuple[str, ...]
    project_outputs: tuple[str, ...]

    def __post_init__(self):
        for value, label in ((self.parent_script_revision_id, "Parent script revision ID"),
                             (self.script_revision_id, "Script revision ID")):
            if type(value) is not str or not value.strip():
                raise DomainValidationError(f"{label} is required.")
        _identities(self.previous_order, "Previous order")
        _identities(self.section_order, "Section order")
        _identities(self.reusable_section_ids, "Reusable sections")
        if (set(self.previous_order) != set(self.section_order)
                or self.reusable_section_ids != self.section_order
                or self.order_fingerprint != content_fingerprint(list(self.section_order))):
            raise DomainValidationError("Section-order impact must retain identities and its exact D005 fingerprint.")
        changed = self.previous_order != self.section_order
        if (changed and (self.script_revision_id == self.parent_script_revision_id
                         or self.project_outputs != PROJECT_OUTPUTS)) or (
                not changed and (self.script_revision_id != self.parent_script_revision_id
                                 or self.project_outputs != ())):
            raise DomainValidationError("Section-order downstream work does not match the requested permutation.")

    @property
    def changed(self) -> bool:
        return self.previous_order != self.section_order

    def to_payload(self):
        return asdict(self) | {
            "previous_order": list(self.previous_order),
            "section_order": list(self.section_order),
            "reusable_section_ids": list(self.reusable_section_ids),
            "project_outputs": list(self.project_outputs),
        }


@dataclass(frozen=True, slots=True)
class SectionOrderResult:
    script: ScriptRevision
    impact: SectionOrderImpact

    def __post_init__(self):
        if not isinstance(self.script, ScriptRevision) or not isinstance(self.impact, SectionOrderImpact):
            raise DomainValidationError("Section reorder result requires an immutable script and impact.")
        if (self.script.id != self.impact.script_revision_id
                or tuple(section.section_id for section in self.script.sections) != self.impact.section_order
                or (self.impact.changed and self.script.parent_revision_id != self.impact.parent_script_revision_id)):
            raise DomainValidationError("Section reorder result and impact disagree.")

    def to_metadata(self):
        return {"version": 1, "script_revision_id": self.script.id,
                "parent_script_revision_id": self.impact.parent_script_revision_id,
                "invalidation": self.impact.to_payload()}


def reorder_script(parent: ScriptRevision, section_ids: Sequence[str]) -> SectionOrderResult:
    """Return a pure complete permutation and its explicit D005 impact."""
    if not isinstance(parent, ScriptRevision):
        raise DomainValidationError("Reorder requires an immutable script revision.")
    script = parent.reorder(section_ids)
    previous_order = tuple(section.section_id for section in parent.sections)
    section_order = tuple(section.section_id for section in script.sections)
    impact = SectionOrderImpact(
        parent.id,
        script.id,
        previous_order,
        section_order,
        content_fingerprint(list(section_order)),
        section_order,
        PROJECT_OUTPUTS if section_order != previous_order else (),
    )
    return SectionOrderResult(script, impact)

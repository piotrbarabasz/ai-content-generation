"""Pure split/merge topology, retained lineage and downstream impact values."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace

from app.domain.base import DomainValidationError, new_id
from app.domain.dependencies import content_fingerprint
from app.domain.narrative_segment import SectionRevision
from app.domain.script import ScriptRevision


SECTION_OUTPUTS = ("raw_audio", "processed_audio", "scene_plan", "scene_timing",
                   "visual_prompt", "scene_image")
PROJECT_OUTPUTS = ("timeline", "video_render")


def _text(value, label):
    if type(value) is not str or not value.strip():
        raise DomainValidationError(f"{label} must be non-empty text.")


def _unique(values, label):
    if type(values) is not tuple or not values or any(type(value) is not str or not value.strip() for value in values):
        raise DomainValidationError(f"{label} must be a non-empty immutable identity tuple.")
    if len(set(values)) != len(values):
        raise DomainValidationError(f"{label} identities must be unique.")


@dataclass(frozen=True, slots=True)
class SectionSourceSlice:
    section_id: str
    revision_id: str
    start: int
    end: int

    def __post_init__(self):
        _text(self.section_id, "Source section ID")
        _text(self.revision_id, "Source revision ID")
        if (type(self.start) is not int or type(self.end) is not int
                or not 0 <= self.start < self.end):
            raise DomainValidationError("Source lineage requires a positive half-open text range.")


@dataclass(frozen=True, slots=True)
class SectionLineage:
    section_id: str
    revision_id: str
    sources: tuple[SectionSourceSlice, ...]

    def __post_init__(self):
        _text(self.section_id, "Created section ID")
        _text(self.revision_id, "Created revision ID")
        if type(self.sources) is not tuple or not self.sources or any(
                not isinstance(source, SectionSourceSlice) for source in self.sources):
            raise DomainValidationError("Created section lineage needs immutable source slices.")


@dataclass(frozen=True, slots=True)
class SectionDownstreamWork:
    section_id: str
    revision_id: str
    text_fingerprint: str
    outputs: tuple[str, ...] = SECTION_OUTPUTS

    def __post_init__(self):
        _text(self.section_id, "Changed section ID")
        _text(self.revision_id, "Changed revision ID")
        if (type(self.text_fingerprint) is not str or len(self.text_fingerprint) != 64
                or any(c not in "0123456789abcdef" for c in self.text_fingerprint)
                or self.outputs != SECTION_OUTPUTS):
            raise DomainValidationError("Changed section needs its exact D005 source and downstream outputs.")


@dataclass(frozen=True, slots=True)
class SectionEditImpact:
    operation: str
    retired_section_ids: tuple[str, ...]
    created: tuple[SectionDownstreamWork, ...]
    reusable_section_ids: tuple[str, ...]
    project_outputs: tuple[str, ...] = PROJECT_OUTPUTS

    def __post_init__(self):
        if self.operation not in ("split", "merge"):
            raise DomainValidationError("Section edit operation must be split or merge.")
        _unique(self.retired_section_ids, "Retired sections")
        if type(self.created) is not tuple or not self.created or any(
                not isinstance(value, SectionDownstreamWork) for value in self.created):
            raise DomainValidationError("Section edit needs immutable created-section work.")
        if type(self.reusable_section_ids) is not tuple or len(set(self.reusable_section_ids)) != len(self.reusable_section_ids):
            raise DomainValidationError("Reusable section IDs must be an immutable unique tuple.")
        created_ids = tuple(value.section_id for value in self.created)
        if (len(set(created_ids)) != len(created_ids)
                or set(self.retired_section_ids).intersection(created_ids)
                or set(self.retired_section_ids).intersection(self.reusable_section_ids)
                or set(created_ids).intersection(self.reusable_section_ids)
                or self.project_outputs != PROJECT_OUTPUTS):
            raise DomainValidationError("Section impact identities or project outputs are inconsistent.")

    def to_payload(self):
        return {"operation": self.operation, "retired_section_ids": list(self.retired_section_ids),
                "created": [asdict(value) | {"outputs": list(value.outputs)} for value in self.created],
                "reusable_section_ids": list(self.reusable_section_ids),
                "project_outputs": list(self.project_outputs)}


@dataclass(frozen=True, slots=True)
class SectionEditResult:
    script: ScriptRevision
    lineages: tuple[SectionLineage, ...]
    impact: SectionEditImpact

    def __post_init__(self):
        if (not isinstance(self.script, ScriptRevision) or self.script.parent_revision_id is None
                or type(self.lineages) is not tuple
                or not self.lineages or any(not isinstance(value, SectionLineage) for value in self.lineages)
                or not isinstance(self.impact, SectionEditImpact)):
            raise DomainValidationError("Section edit result must contain an immutable script, lineage and impact.")
        lineages = {(value.section_id, value.revision_id) for value in self.lineages}
        created = {(value.section_id, value.revision_id) for value in self.impact.created}
        selected = {(value.section_id, value.id): value for value in self.script.sections}
        if (lineages != created or len(lineages) != len(self.lineages)
                or not lineages.issubset(selected)
                or any(value.text_fingerprint != content_fingerprint(selected[(value.section_id, value.revision_id)].text)
                       for value in self.impact.created)):
            raise DomainValidationError("Lineage must describe every created section exactly once.")

    def to_metadata(self):
        return {"version": 1, "script_revision_id": self.script.id,
                "parent_script_revision_id": self.script.parent_revision_id,
                "lineages": [{"section_id": value.section_id, "revision_id": value.revision_id,
                              "sources": [asdict(source) for source in value.sources]} for value in self.lineages],
                "invalidation": self.impact.to_payload()}


def _new_script(parent, sections):
    return replace(parent, id=new_id("script_revision"), parent_revision_id=parent.id, sections=tuple(sections))


def _impact(operation, parent, retired, created):
    retired_ids = tuple(section.section_id for section in retired)
    created_work = tuple(SectionDownstreamWork(section.section_id, section.id,
                                               content_fingerprint(section.text)) for section in created)
    reusable = tuple(section.section_id for section in parent.sections if section.section_id not in retired_ids)
    return SectionEditImpact(operation, retired_ids, created_work, reusable)


def split_script(parent, section_id, boundary, *, left_title=None, right_title=None):
    if not isinstance(parent, ScriptRevision):
        raise DomainValidationError("Split requires an immutable script revision.")
    _text(section_id, "Split section ID")
    if type(boundary) is not int:
        raise DomainValidationError("Split boundary must be an integer text offset.")
    source = parent.section(section_id)
    if not 0 < boundary < len(source.text):
        raise DomainValidationError("Split boundary must lie inside the source text.")
    left_text, right_text = source.text[:boundary], source.text[boundary:]
    if not left_text.strip() or not right_text.strip():
        raise DomainValidationError("Split must leave non-whitespace text on both sides.")
    left = SectionRevision.create(project_id=parent.project_id, title=source.title if left_title is None else left_title,
                                  text=left_text, role=source.role)
    right = SectionRevision.create(project_id=parent.project_id, title=source.title if right_title is None else right_title,
                                   text=right_text, role=source.role)
    index = parent.sections.index(source)
    script = _new_script(parent, (*parent.sections[:index], left, right, *parent.sections[index + 1:]))
    lineages = (SectionLineage(left.section_id, left.id, (SectionSourceSlice(source.section_id, source.id, 0, boundary),)),
                SectionLineage(right.section_id, right.id,
                               (SectionSourceSlice(source.section_id, source.id, boundary, len(source.text)),)))
    return SectionEditResult(script, lineages, _impact("split", parent, (source,), (left, right)))


def merge_script(parent, section_ids: Sequence[str], *, separator="\n\n", title=None, role=None):
    if not isinstance(parent, ScriptRevision):
        raise DomainValidationError("Merge requires an immutable script revision.")
    if isinstance(section_ids, str) or not isinstance(section_ids, Sequence):
        raise DomainValidationError("Merge requires an ordered section ID sequence.")
    section_ids = tuple(section_ids)
    if len(section_ids) < 2 or any(type(value) is not str or not value.strip() for value in section_ids):
        raise DomainValidationError("Merge requires at least two section identities.")
    if len(set(section_ids)) != len(section_ids):
        raise DomainValidationError("Merge section identities must be unique.")
    if type(separator) is not str:
        raise DomainValidationError("Merge separator must be explicit text.")
    positions = tuple(next((i for i, section in enumerate(parent.sections) if section.section_id == value), -1)
                      for value in section_ids)
    if -1 in positions or positions != tuple(range(positions[0], positions[0] + len(positions))):
        raise DomainValidationError("Merge requires selected adjacent sections in current order.")
    sources = tuple(parent.sections[position] for position in positions)
    roles = {source.role for source in sources}
    if role is None and len(roles) != 1:
        raise DomainValidationError("Merging different roles requires an explicit result role.")
    merged = SectionRevision.create(project_id=parent.project_id, title=sources[0].title if title is None else title,
                                    text=separator.join(source.text for source in sources),
                                    role=sources[0].role if role is None else role)
    first, last = positions[0], positions[-1]
    script = _new_script(parent, (*parent.sections[:first], merged, *parent.sections[last + 1:]))
    lineage = SectionLineage(merged.section_id, merged.id, tuple(
        SectionSourceSlice(source.section_id, source.id, 0, len(source.text)) for source in sources))
    return SectionEditResult(script, (lineage,), _impact("merge", parent, sources, (merged,)))


def describe_section_edit(parent, child):
    """Rebuild durable lineage from exact parent/child snapshots after reopen."""
    if (not isinstance(parent, ScriptRevision) or not isinstance(child, ScriptRevision)
            or child.parent_revision_id != parent.id or child.script_id != parent.script_id
            or child.project_id != parent.project_id or child.language != parent.language):
        raise DomainValidationError("Section edit snapshots do not form one script lineage step.")
    old_ids = {section.section_id for section in parent.sections}
    new_ids = {section.section_id for section in child.sections}
    retired = tuple(section for section in parent.sections if section.section_id not in new_ids)
    created = tuple(section for section in child.sections if section.section_id not in old_ids)
    common_old = tuple(section for section in parent.sections if section.section_id in new_ids)
    common_new = tuple(section for section in child.sections if section.section_id in old_ids)
    if common_old != common_new or any(section.parent_revision_id is not None for section in created):
        raise DomainValidationError("Section edit changed an unaffected revision or reused a created identity.")
    if len(retired) == 1 and len(created) == 2 and "".join(section.text for section in created) == retired[0].text:
        source = retired[0]
        boundary = len(created[0].text)
        if tuple(section.role for section in created) != (source.role, source.role):
            raise DomainValidationError("Split descendants must retain the source role.")
        lineages = (SectionLineage(created[0].section_id, created[0].id,
                                   (SectionSourceSlice(source.section_id, source.id, 0, boundary),)),
                    SectionLineage(created[1].section_id, created[1].id,
                                   (SectionSourceSlice(source.section_id, source.id, boundary, len(source.text)),)))
        result = SectionEditResult(child, lineages, _impact("split", parent, retired, created))
    elif len(retired) >= 2 and len(created) == 1:
        merged = created[0]
        separator_bytes = len(merged.text) - sum(len(source.text) for source in retired)
        gaps = len(retired) - 1
        if separator_bytes < 0 or separator_bytes % gaps:
            raise DomainValidationError("Merged text has content outside explicit separators.")
        separator_length = separator_bytes // gaps
        separator_start = len(retired[0].text)
        separator = merged.text[separator_start:separator_start + separator_length]
        if separator.join(source.text for source in retired) != merged.text:
            raise DomainValidationError("Merged text does not retain ordered complete sources and one explicit separator.")
        lineage = SectionLineage(merged.section_id, merged.id, tuple(
            SectionSourceSlice(source.section_id, source.id, 0, len(source.text)) for source in retired))
        result = SectionEditResult(child, (lineage,), _impact("merge", parent, retired, created))
    else:
        raise DomainValidationError("Snapshot change is not one supported split or merge.")
    # Exact prefix/suffix placement distinguishes replacement from unrelated moves.
    retired_positions = [parent.sections.index(section) for section in retired]
    created_positions = [child.sections.index(section) for section in created]
    if (retired_positions != list(range(retired_positions[0], retired_positions[-1] + 1))
            or created_positions != list(range(created_positions[0], created_positions[-1] + 1))
            or retired_positions[0] != created_positions[0]):
        raise DomainValidationError("Section edit replacement is not contiguous at the source position.")
    return result

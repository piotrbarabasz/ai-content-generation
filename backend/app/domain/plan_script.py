"""Immutable plan/script binding and validated generated group cache value."""

from dataclasses import dataclass
from app.domain.narrative_segment import planned_section_identity


def _text(value, label):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be nonempty text.")


@dataclass(frozen=True, slots=True)
class PlanScriptBinding:
    id: str
    project_id: str
    video_plan_revision_id: str
    script_id: str

    def __post_init__(self):
        for key in ("id", "project_id", "video_plan_revision_id", "script_id"):
            _text(getattr(self, key), key)

    def to_payload(self):
        return {"version": 1, "id": self.id, "project_id": self.project_id,
                "video_plan_revision_id": self.video_plan_revision_id, "script_id": self.script_id}

    @classmethod
    def from_payload(cls, payload):
        if type(payload) is not dict or set(payload) != {"version", "id", "project_id", "video_plan_revision_id", "script_id"} or type(payload["version"]) is not int or payload["version"] != 1:
            raise ValueError("Malformed plan-script binding payload.")
        return cls(**{k: v for k, v in payload.items() if k != "version"})


@dataclass(frozen=True, slots=True)
class PlanScriptBindingSelection:
    id: str
    project_id: str
    binding_id: str
    parent_selection_id: str | None = None

    def __post_init__(self):
        for key in ("id", "project_id", "binding_id"):
            _text(getattr(self, key), key)
        if self.parent_selection_id is not None:
            _text(self.parent_selection_id, "parent_selection_id")

    def to_payload(self):
        return {"version": 1, "id": self.id, "project_id": self.project_id,
                "binding_id": self.binding_id, "parent_selection_id": self.parent_selection_id}

    @classmethod
    def from_payload(cls, payload):
        if type(payload) is not dict or set(payload) != {"version", "id", "project_id", "binding_id", "parent_selection_id"} or type(payload["version"]) is not int or payload["version"] != 1:
            raise ValueError("Malformed plan-script selection payload.")
        return cls(**{k: v for k, v in payload.items() if k != "version"})


@dataclass(frozen=True, slots=True)
class GeneratedGroupSection:
    planned_section_id: str
    text: str

    def __post_init__(self):
        _text(self.planned_section_id, "planned_section_id")
        _text(self.text, "generated section text")


@dataclass(frozen=True, slots=True)
class GeneratedScriptGroup:
    id: str
    project_id: str
    video_plan_revision_id: str
    group_id: str
    cache_key: str
    sections: tuple[GeneratedGroupSection, ...]

    def __post_init__(self):
        for key in ("id", "project_id", "video_plan_revision_id", "group_id", "cache_key"):
            _text(getattr(self, key), key)
        if type(self.sections) not in (tuple, list) or not self.sections:
            raise ValueError("Generated script group requires sections.")
        object.__setattr__(self, "sections", tuple(self.sections))
        if any(type(section) is not GeneratedGroupSection for section in self.sections):
            raise ValueError("Invalid generated group section value.")
        ids = [section.planned_section_id for section in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("Generated group contains duplicate planned section IDs.")

    def to_payload(self):
        return {"version": 1, "id": self.id, "project_id": self.project_id,
                "video_plan_revision_id": self.video_plan_revision_id, "group_id": self.group_id,
                "cache_key": self.cache_key,
                "sections": [{"planned_section_id": s.planned_section_id, "text": s.text} for s in self.sections]}

    @classmethod
    def from_payload(cls, payload):
        expected = {"version", "id", "project_id", "video_plan_revision_id", "group_id", "cache_key", "sections"}
        if type(payload) is not dict or set(payload) != expected or type(payload["version"]) is not int or payload["version"] != 1 or type(payload["sections"]) is not list:
            raise ValueError("Malformed generated group payload.")
        sections = []
        for section in payload["sections"]:
            if type(section) is not dict or set(section) != {"planned_section_id", "text"}:
                raise ValueError("Malformed generated group section.")
            sections.append(GeneratedGroupSection(**section))
        return cls(**{k: v for k, v in payload.items() if k not in {"version", "sections"}}, sections=tuple(sections))

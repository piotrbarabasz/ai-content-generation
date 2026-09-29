"""Immutable Social / Standard video planning revisions."""

from dataclasses import asdict, dataclass
from enum import StrEnum

from app.domain.base import DomainValidationError

WORDS_PER_SECOND = 2.2  # Editorial estimate (~132 words/minute), not measured TTS duration.


class VideoFormat(StrEnum):
    SOCIAL = "social"
    STANDARD = "standard"


@dataclass(frozen=True, slots=True)
class VideoFormatSpec:
    format: VideoFormat
    min_duration_seconds: int
    max_duration_seconds: int
    default_duration_seconds: int
    preferred_scene_seconds: int
    min_scene_seconds: int
    max_scene_seconds: int


VIDEO_FORMATS = {
    VideoFormat.SOCIAL: VideoFormatSpec(VideoFormat.SOCIAL, 30, 60, 45, 6, 4, 8),
    VideoFormat.STANDARD: VideoFormatSpec(VideoFormat.STANDARD, 540, 660, 600, 10, 8, 12),
}


def _text(value, name):
    if type(value) is not str or not value.strip():
        raise DomainValidationError(f"{name} must be non-empty text.")


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise DomainValidationError(f"{name} must be a positive integer.")


@dataclass(frozen=True, slots=True)
class PlannedSection:
    id: str
    title: str
    role: str
    purpose: str
    target_duration_seconds: int
    target_word_count: int
    target_scene_count: int

    def __post_init__(self):
        for field in ("id", "title", "role", "purpose"):
            _text(getattr(self, field), f"section {field}")
        for field in ("target_duration_seconds", "target_word_count", "target_scene_count"):
            _positive(getattr(self, field), f"section {field}")


@dataclass(frozen=True, slots=True)
class VideoPlanGroup:
    id: str
    kind: str
    title: str
    purpose: str
    target_duration_seconds: int
    sections: tuple[PlannedSection, ...]

    def __post_init__(self):
        for field in ("id", "kind", "title", "purpose"):
            _text(getattr(self, field), f"group {field}")
        _positive(self.target_duration_seconds, "group target_duration_seconds")
        if type(self.sections) not in (tuple, list) or not self.sections:
            raise DomainValidationError("Video plan group requires planned sections.")
        object.__setattr__(self, "sections", tuple(self.sections))
        if any(type(s) is not PlannedSection for s in self.sections):
            raise DomainValidationError("Video plan group sections must be PlannedSection values.")
        if sum(s.target_duration_seconds for s in self.sections) != self.target_duration_seconds:
            raise DomainValidationError("Section duration budgets must sum to the group duration.")


@dataclass(frozen=True, slots=True)
class VideoPlanRevision:
    id: str
    project_id: str
    language: str
    format: VideoFormat
    target_duration_seconds: int
    topic: str
    working_title: str
    film_brief: str
    visual_style: str
    groups: tuple[VideoPlanGroup, ...]
    parent_revision_id: str | None = None

    def __post_init__(self):
        for field in ("id", "project_id", "language", "topic", "working_title", "film_brief", "visual_style"):
            _text(getattr(self, field), field)
        try:
            fmt = VideoFormat(self.format)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("Invalid video format.") from exc
        object.__setattr__(self, "format", fmt)
        spec = VIDEO_FORMATS[fmt]
        if type(self.target_duration_seconds) is not int or not spec.min_duration_seconds <= self.target_duration_seconds <= spec.max_duration_seconds:
            raise DomainValidationError("Target duration is outside the selected format profile.")
        if type(self.groups) not in (tuple, list) or not self.groups:
            raise DomainValidationError("Video plan requires groups.")
        object.__setattr__(self, "groups", tuple(self.groups))
        if any(type(g) is not VideoPlanGroup for g in self.groups):
            raise DomainValidationError("Video plan groups must be VideoPlanGroup values.")
        if fmt is VideoFormat.SOCIAL:
            kinds = [g.kind.lower() for g in self.groups]
            if not 4 <= len(self.groups) <= 8 or any(len(g.sections) != 1 for g in self.groups):
                raise DomainValidationError("Social plans require four to eight flat groups.")
            if kinds[0] not in {"hook", "opening", "cold_open"} or not any(k in {"body", "development", "explanation", "payoff", "fact"} for k in kinds[1:]):
                raise DomainValidationError("Social plan requires an opening and a development beat.")
        else:
            kinds = [g.kind.lower() for g in self.groups]
            chapters = kinds.count("chapter")
            if not 7 <= len(self.groups) <= 10 or not 5 <= chapters <= 7 or kinds[0] not in {"cold_open", "opening"} or "introduction" not in kinds or "conclusion" not in kinds:
                raise DomainValidationError("Standard plans require an opening, introduction, five to seven chapters and conclusion.")
        gids, sids = [g.id for g in self.groups], [s.id for g in self.groups for s in g.sections]
        if len(set(gids + sids)) != len(gids + sids):
            raise DomainValidationError("Video plan identities must be unique.")
        if sum(g.target_duration_seconds for g in self.groups) != self.target_duration_seconds:
            raise DomainValidationError("Group duration budgets must sum to the plan duration.")
        if self.parent_revision_id is not None:
            _text(self.parent_revision_id, "parent_revision_id")
            if self.parent_revision_id == self.id:
                raise DomainValidationError("Video plan cannot be its own parent.")

    @property
    def target_word_count(self):
        return sum(s.target_word_count for g in self.groups for s in g.sections)

    @property
    def target_scene_count(self):
        return sum(s.target_scene_count for g in self.groups for s in g.sections)

    def to_payload(self):
        return {"version": 1, "id": self.id, "project_id": self.project_id,
                "language": self.language, "format": self.format.value,
                "target_duration_seconds": self.target_duration_seconds, "topic": self.topic,
                "working_title": self.working_title, "film_brief": self.film_brief,
                "visual_style": self.visual_style,
                "groups": [{"id": g.id, "kind": g.kind, "title": g.title, "purpose": g.purpose,
                            "target_duration_seconds": g.target_duration_seconds,
                            "sections": [asdict(s) for s in g.sections]} for g in self.groups],
                "parent_revision_id": self.parent_revision_id}

    @classmethod
    def from_payload(cls, payload):
        if type(payload) is not dict or set(payload) != {"version", "id", "project_id", "language", "format", "target_duration_seconds", "topic", "working_title", "film_brief", "visual_style", "groups", "parent_revision_id"}:
            raise DomainValidationError("Malformed video plan payload.")
        if type(payload["version"]) is not int or payload["version"] != 1:
            raise DomainValidationError("Unsupported video plan payload version.")
        groups = payload["groups"]
        if type(groups) is not list:
            raise DomainValidationError("Malformed video plan groups.")
        parsed = []
        for group in groups:
            if type(group) is not dict or set(group) != {"id", "kind", "title", "purpose", "target_duration_seconds", "sections"} or type(group["sections"]) is not list:
                raise DomainValidationError("Malformed video plan group.")
            sections = []
            for section in group["sections"]:
                if type(section) is not dict or set(section) != {"id", "title", "role", "purpose", "target_duration_seconds", "target_word_count", "target_scene_count"}:
                    raise DomainValidationError("Malformed planned section.")
                sections.append(PlannedSection(**section))
            parsed.append(VideoPlanGroup(**{**group, "sections": tuple(sections)}))
        return cls(**{k: v for k, v in payload.items() if k not in ("version", "groups")}, groups=tuple(parsed))


@dataclass(frozen=True, slots=True)
class VideoPlanSelection:
    id: str
    project_id: str
    revision_id: str
    parent_selection_id: str | None = None

    def __post_init__(self):
        _text(self.id, "selection id")
        _text(self.project_id, "selection project_id")
        _text(self.revision_id, "selection revision_id")
        if self.parent_selection_id is not None:
            _text(self.parent_selection_id, "parent_selection_id")
            if self.parent_selection_id == self.id:
                raise DomainValidationError("Selection cannot be its own parent.")

    def to_payload(self):
        return {"version": 1, "id": self.id, "project_id": self.project_id,
                "revision_id": self.revision_id, "parent_selection_id": self.parent_selection_id}

    @classmethod
    def from_payload(cls, payload):
        if type(payload) is not dict or set(payload) != {"version", "id", "project_id", "revision_id", "parent_selection_id"} or type(payload["version"]) is not int or payload["version"] != 1:
            raise DomainValidationError("Malformed video plan selection payload.")
        return cls(**{k: v for k, v in payload.items() if k != "version"})

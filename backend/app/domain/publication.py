"""Enqueue-time editorial inputs and immutable publication decisions."""

from dataclasses import asdict, dataclass, replace
import json

from .base import DomainValidationError
from .dependencies import InputEdge, content_fingerprint
from .generation_job import require_text
from .narrative_segment import SectionRevision


PUBLICATION_KEY = "desktop_publication"


class PublicationConflictError(ValueError):
    pass


@dataclass(frozen=True)
class PublicationSnapshot:
    project_id: str
    generation_id: str
    sections: tuple[SectionRevision, ...]
    script_revision_id: str | None = None

    def __post_init__(self):
        require_text(self.project_id, "publication project")
        require_text(self.generation_id, "generation")
        if (not isinstance(self.sections, (list, tuple)) or not self.sections
                or any(not isinstance(s, SectionRevision) or s.project_id != self.project_id for s in self.sections)
                or len({s.section_id for s in self.sections}) != len(self.sections)):
            raise DomainValidationError("Publication needs unique project-owned section inputs.")
        object.__setattr__(self, "sections", tuple(sorted(self.sections, key=lambda s: s.section_id)))
        if self.script_revision_id is not None:
            require_text(self.script_revision_id, "expected script revision")

    def to_payload(self):
        return {"version": 1, "project_id": self.project_id, "generation_id": self.generation_id,
                "sections": [asdict(s) for s in self.sections], "script_revision_id": self.script_revision_id}

    @classmethod
    def from_job(cls, job):
        data = json.loads(job.input_snapshot_json)[PUBLICATION_KEY]
        if (set(data) != {"version", "project_id", "generation_id", "sections", "script_revision_id"}
                or type(data["version"]) is not int or data["version"] != 1):
            raise DomainValidationError("Unsupported publication snapshot.")
        return cls(data["project_id"], data["generation_id"], tuple(SectionRevision(**s) for s in data["sections"]),
                   data["script_revision_id"])

    def bind(self, request):
        edges = tuple(InputEdge("revision:" + s.section_id, "section:" + s.section_id + ":revision",
                                content_fingerprint(asdict(s))) for s in self.sections)
        if self.script_revision_id is not None:
            edges += (InputEdge("script_revision", "project:script_revision", content_fingerprint(self.script_revision_id)),)
        return replace(request, inputs=(*request.inputs, *edges))


@dataclass(frozen=True)
class PublicationResult:
    job_id: str
    attempt_id: str
    artifact_id: str
    selected_at_publication: bool
    reason: str

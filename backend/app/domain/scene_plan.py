"""Immutable desktop plans, explicit acceptance, and separate audio-bound timing."""

from dataclasses import asdict, dataclass
from hashlib import sha256

from .render_scene import ProjectRenderScene


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Nonempty scene identity/text is required.")


@dataclass(frozen=True)
class ScenePlan:
    id: str
    project_id: str
    section_id: str
    revision_id: str
    text_checksum: str
    text_length: int
    scenes: tuple[ProjectRenderScene, ...]
    method: str = "paragraph_editorial_v1"
    planning_audio_id: str | None = None

    def __post_init__(self):
        for value in (self.id, self.project_id, self.section_id, self.revision_id, self.text_checksum):
            _text(value)
        if (len(self.text_checksum) != 64 or any(c not in "0123456789abcdef" for c in self.text_checksum)
                or type(self.text_length) is not int or self.text_length <= 0
                or not isinstance(self.scenes, tuple) or not self.scenes
                or self.method != "paragraph_editorial_v1"):
            raise ValueError("Invalid scene plan source or method.")
        if self.planning_audio_id is not None:
            _text(self.planning_audio_id)
        cursor, scene_ids, sentence_ids = 0, set(), set()
        for scene in self.scenes:
            if (not isinstance(scene, ProjectRenderScene)
                    or (scene.project_id, scene.section_id, scene.revision_id) != (self.project_id, self.section_id, self.revision_id)
                    or scene.source_start != cursor or scene.id in scene_ids
                    or sentence_ids.intersection(scene.sentence_ids)):
                raise ValueError("Scene plan ownership or complete unique source coverage is invalid.")
            cursor = scene.source_end
            scene_ids.add(scene.id)
            sentence_ids.update(scene.sentence_ids)
        if cursor != self.text_length:
            raise ValueError("Scene plan must cover the full section text.")

    def validate_section(self, section):
        if ((section.project_id, section.section_id, section.id) != (self.project_id, self.section_id, self.revision_id)
                or len(section.text) != self.text_length or sha256(section.text.encode()).hexdigest() != self.text_checksum):
            raise ValueError("Scene plan differs from the expected section revision.")

    def to_payload(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_payload(cls, value):
        data = dict(value)
        if data.pop("version") != 1:
            raise ValueError("Unsupported scene plan version.")
        data["scenes"] = tuple(ProjectRenderScene(**(scene | {"sentence_ids": tuple(scene["sentence_ids"])}))
                               for scene in data["scenes"])
        return cls(**data)


@dataclass(frozen=True)
class AcceptedScenePlan:
    id: str
    plan: ScenePlan
    reviewer_id: str

    def __post_init__(self):
        _text(self.id)
        _text(self.reviewer_id)
        if not isinstance(self.plan, ScenePlan):
            raise ValueError("Acceptance requires the exact immutable scene plan.")

    def to_payload(self):
        return {"version": 1, "id": self.id, "plan": self.plan.to_payload(), "reviewer_id": self.reviewer_id}

    @classmethod
    def from_payload(cls, value):
        if value["version"] != 1:
            raise ValueError("Unsupported scene acceptance version.")
        return cls(value["id"], ScenePlan.from_payload(value["plan"]), value["reviewer_id"])


@dataclass(frozen=True)
class SceneTiming:
    scene_id: str
    start_frame: int
    end_frame: int

    def __post_init__(self):
        _text(self.scene_id)
        if (type(self.start_frame) is not int or type(self.end_frame) is not int
                or not 0 <= self.start_frame < self.end_frame):
            raise ValueError("SceneTiming requires a positive measured sample interval.")


@dataclass(frozen=True)
class SceneTimingSet:
    id: str
    acceptance_id: str
    plan_id: str
    audio_artifact_id: str
    audio_checksum: str
    sample_rate: int
    frame_count: int
    method: str
    quality: str
    scenes: tuple[SceneTiming, ...]

    def __post_init__(self):
        for value in (self.id, self.acceptance_id, self.plan_id, self.audio_artifact_id, self.audio_checksum):
            _text(value)
        if (len(self.audio_checksum) != 64 or any(c not in "0123456789abcdef" for c in self.audio_checksum)
                or type(self.sample_rate) is not int or self.sample_rate <= 0
                or type(self.frame_count) is not int or self.frame_count <= 0
                or not isinstance(self.scenes, tuple) or not self.scenes
                or (self.method, self.quality) not in (("measured_chunk_assembly", "measured_sentence_blocks"),
                                                       ("measured_duration_ratio", "approximate_internal_positions"))):
            raise ValueError("Scene timing requires measured audio and known boundary quality.")
        cursor, seen = 0, set()
        for scene in self.scenes:
            if not isinstance(scene, SceneTiming) or scene.start_frame != cursor or scene.scene_id in seen:
                raise ValueError("Scene timing must be ordered, contiguous and unique.")
            cursor = scene.end_frame
            seen.add(scene.scene_id)
        if cursor != self.frame_count:
            raise ValueError("Scene timings must cover the full selected WAV.")

    def to_payload(self):
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_payload(cls, value):
        data = dict(value)
        if data.pop("version") != 1:
            raise ValueError("Unsupported scene timing version.")
        data["scenes"] = tuple(SceneTiming(**scene) for scene in data["scenes"])
        return cls(**data)

"""Render scene domain model."""

from dataclasses import dataclass

from app.domain.base import DomainEntity, DomainValidationError, new_id


@dataclass(slots=True)
class RenderScene(DomainEntity):
    workflow_run_id: str = ""
    order: int = 1
    scene_plan_id: str = ""
    timing_hint: str = ""
    visual_intensity: str = ""

    @classmethod
    def create(
        cls,
        *,
        workflow_run_id: str,
        order: int,
        scene_plan_id: str,
        timing_hint: str,
        visual_intensity: str,
    ) -> "RenderScene":
        if not workflow_run_id.strip():
            raise DomainValidationError("RenderScene workflow_run_id is required.")
        if order < 1:
            raise DomainValidationError("RenderScene order must be greater than zero.")
        if not scene_plan_id.strip():
            raise DomainValidationError("RenderScene scene_plan_id is required.")
        if not timing_hint.strip():
            raise DomainValidationError("RenderScene timing_hint is required.")
        if not visual_intensity.strip():
            raise DomainValidationError("RenderScene visual_intensity is required.")

        return cls(
            id=new_id("render_scene"),
            workflow_run_id=workflow_run_id,
            order=order,
            scene_plan_id=scene_plan_id,
            timing_hint=timing_hint,
            visual_intensity=visual_intensity,
        )


@dataclass(frozen=True, slots=True)
class ProjectRenderScene:
    """Desktop scene semantics; the legacy run-based RenderScene stays unchanged."""

    id: str
    project_id: str
    section_id: str
    revision_id: str
    source_start: int
    source_end: int
    sentence_ids: tuple[str, ...]
    visual_description: str

    def __post_init__(self):
        for name in ("id", "project_id", "section_id", "revision_id", "visual_description"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(f"ProjectRenderScene {name} is required.")
        if (type(self.source_start) is not int or type(self.source_end) is not int
                or not 0 <= self.source_start < self.source_end
                or not isinstance(self.sentence_ids, tuple) or not self.sentence_ids
                or any(not isinstance(s, str) or not s.strip() for s in self.sentence_ids)
                or len(set(self.sentence_ids)) != len(self.sentence_ids)):
            raise DomainValidationError("Scene requires a valid whole-sentence source range.")

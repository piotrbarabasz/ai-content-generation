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

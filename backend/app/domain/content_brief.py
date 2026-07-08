"""Content brief domain model."""

from dataclasses import dataclass, field

from app.domain.base import DomainEntity, DomainValidationError, new_id
from app.domain.enums import DurationProfile


@dataclass(slots=True)
class ContentBrief(DomainEntity):
    project_id: str = ""
    topic: str = ""
    objective: str = ""
    audience: str = ""
    constraints: list[str] = field(default_factory=list)
    duration_profile: DurationProfile = DurationProfile.SIXTY_SECONDS
    success_criteria: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        topic: str,
        duration_profile: DurationProfile | str,
        objective: str = "",
        audience: str = "",
        constraints: list[str] | None = None,
        success_criteria: list[str] | None = None,
    ) -> "ContentBrief":
        if not topic.strip():
            raise DomainValidationError("ContentBrief topic is required.")
        return cls(
            id=new_id("brief"),
            project_id=project_id,
            topic=topic,
            objective=objective,
            audience=audience,
            constraints=constraints or [],
            duration_profile=DurationProfile(duration_profile),
            success_criteria=success_criteria or [],
        )

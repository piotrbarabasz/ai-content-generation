"""Project domain model."""

from dataclasses import dataclass, field

from app.domain.base import DomainEntity, DomainValidationError, new_id
from app.domain.enums import ContentGenre, ContentType, TargetPlatform


@dataclass(slots=True)
class Project(DomainEntity):
    workspace_id: str = ""
    name: str = ""
    content_type: ContentType = ContentType.SHORT_VIDEO
    genre: ContentGenre = ContentGenre.NEWS
    target_platform: TargetPlatform = TargetPlatform.GENERIC_EXPORT
    language: str = "en"
    tone: str = "neutral"
    status: str = "draft"
    workflow_config_ids: list[str] = field(default_factory=list)
    workflow_run_ids: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        name: str,
        content_type: ContentType | str,
        genre: ContentGenre | str,
        target_platform: TargetPlatform | str,
        language: str,
        tone: str,
    ) -> "Project":
        if not name.strip():
            raise DomainValidationError("Project name is required.")
        return cls(
            id=new_id("project"),
            workspace_id=workspace_id,
            name=name,
            content_type=ContentType(content_type),
            genre=ContentGenre(genre),
            target_platform=TargetPlatform(target_platform),
            language=language,
            tone=tone,
        )

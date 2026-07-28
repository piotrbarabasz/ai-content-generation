"""Shared API schemas for the AI Content Studio MVP."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.artifact import Artifact
from app.domain.enums import ContentGenre, ContentType, DurationProfile, TargetPlatform, WorkflowPreset
from app.domain.export_bundle import ExportBundle
from app.domain.project import Project
from app.domain.workflow_config import WorkflowConfig
from app.domain.workflow_run import WorkflowRun


def _to_camel(value: str) -> str:
    if "_" not in value:
        return value
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class ApiSchema(BaseModel):
    """Base model with camelCase payload support."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        from_attributes=True,
        alias_generator=_to_camel,
    )


class ProjectCreateRequest(ApiSchema):
    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    content_type: ContentType
    genre: ContentGenre = Field(alias="contentGenre")
    target_platform: TargetPlatform
    language: str = Field(min_length=1)
    tone: str = Field(min_length=1)

    def to_domain(self) -> Project:
        return Project.create(
            workspace_id=self.workspace_id,
            name=self.name,
            content_type=self.content_type,
            genre=self.genre,
            target_platform=self.target_platform,
            language=self.language,
            tone=self.tone,
        )


class ProjectSchema(ProjectCreateRequest):
    id: str
    status: str
    workflow_config_ids: list[str] = Field(default_factory=list)
    workflow_run_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class WorkflowConfigCreateRequest(ApiSchema):
    project_id: str = Field(min_length=1)
    workflow_preset: WorkflowPreset
    content_type: ContentType
    content_genre: ContentGenre
    duration_profile: DurationProfile
    target_platform: TargetPlatform
    language: str = Field(min_length=1)
    tone: str = Field(min_length=1)
    enabled_modules: list[str] = Field(default_factory=list)
    disabled_modules: list[str] = Field(default_factory=list)
    provider_config: dict[str, Any] = Field(default_factory=dict)
    render_config: dict[str, Any] = Field(default_factory=dict)
    caption_config: dict[str, Any] = Field(default_factory=dict)
    voice_config: dict[str, Any] = Field(default_factory=dict)
    asset_config: dict[str, Any] = Field(default_factory=dict)
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    export_config: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> WorkflowConfig:
        return WorkflowConfig.create(
            project_id=self.project_id,
            workflow_preset=self.workflow_preset,
            content_type=self.content_type,
            content_genre=self.content_genre,
            duration_profile=self.duration_profile,
            target_platform=self.target_platform,
            language=self.language,
            tone=self.tone,
            enabled_modules=self.enabled_modules,
            disabled_modules=self.disabled_modules,
            provider_config=self.provider_config,
            render_config=self.render_config,
            caption_config=self.caption_config,
            voice_config=self.voice_config,
            asset_config=self.asset_config,
            approval_policy=self.approval_policy,
            export_config=self.export_config,
        )


class WorkflowConfigSchema(WorkflowConfigCreateRequest):
    id: str
    created_at: datetime


_WORKFLOW_CONFIG_CANONICAL_FIELDS = (
    "project_id",
    "workflow_preset",
    "content_type",
    "content_genre",
    "duration_profile",
    "target_platform",
    "language",
    "tone",
    "enabled_modules",
    "disabled_modules",
    "provider_config",
    "render_config",
    "caption_config",
    "voice_config",
    "asset_config",
    "approval_policy",
    "export_config",
)

_WORKFLOW_CONFIG_ENUM_FIELDS = {
    "workflow_preset": WorkflowPreset,
    "content_type": ContentType,
    "content_genre": ContentGenre,
    "duration_profile": DurationProfile,
    "target_platform": TargetPlatform,
}


def _validate_workflow_config_schema_sync() -> None:
    """Fail fast if the API request schema drifts from the canonical domain model."""

    actual_fields = tuple(WorkflowConfigCreateRequest.model_fields)
    if actual_fields != _WORKFLOW_CONFIG_CANONICAL_FIELDS:
        raise RuntimeError(
            "WorkflowConfigCreateRequest field order is out of sync with WorkflowConfig."
        )

    actual_schema_fields = tuple(WorkflowConfigSchema.model_fields)
    expected_schema_fields = _WORKFLOW_CONFIG_CANONICAL_FIELDS + ("id", "created_at")
    if actual_schema_fields != expected_schema_fields:
        raise RuntimeError(
            "WorkflowConfigSchema field order is out of sync with WorkflowConfig."
        )

    for field_name, expected_type in _WORKFLOW_CONFIG_ENUM_FIELDS.items():
        actual_type = WorkflowConfigCreateRequest.model_fields[field_name].annotation
        if actual_type is not expected_type:
            raise RuntimeError(
                f"WorkflowConfigCreateRequest field {field_name} no longer matches {expected_type.__name__}."
            )


_validate_workflow_config_schema_sync()


class WorkflowRunCreateRequest(ApiSchema):
    workflow_config_id: str = Field(min_length=1)

    def to_domain(self) -> WorkflowRun:
        return WorkflowRun.create(workflow_config_id=self.workflow_config_id)


class WorkflowRunSchema(WorkflowRunCreateRequest):
    id: str
    status: str
    current_stage: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str
    artifact_ids: list[str] = Field(default_factory=list)
    approval_checkpoint_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class ArtifactSchema(ApiSchema):
    id: str
    workflow_run_id: str
    module_name: str
    artifact_type: str
    storage_key: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ExportBundleSchema(ApiSchema):
    id: str
    workflow_run_id: str
    manifest_path: str
    required_files: list[str] = Field(default_factory=list)
    included_artifacts: list[str] = Field(default_factory=list)
    missing_optional_artifacts: list[str] = Field(default_factory=list)
    approval_summary: dict[str, Any] = Field(default_factory=dict)
    provider_summary: dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: datetime


__all__ = [
    "ApiSchema",
    "ArtifactSchema",
    "ExportBundleSchema",
    "ProjectCreateRequest",
    "ProjectSchema",
    "WorkflowConfigCreateRequest",
    "WorkflowConfigSchema",
    "WorkflowRunCreateRequest",
    "WorkflowRunSchema",
]

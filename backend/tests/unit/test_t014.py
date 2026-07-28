from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.dependencies import ApiDependencies, ApiSettings, build_api_dependencies, get_api_settings
from app.api.main import app, create_app
from app.api.schemas import (
    ArtifactSchema,
    ExportBundleSchema,
    ProjectCreateRequest,
    ProjectSchema,
    WorkflowConfigCreateRequest,
    WorkflowConfigSchema,
    WorkflowRunCreateRequest,
    WorkflowRunSchema,
)
from app.domain.artifact import Artifact
from app.domain.enums import ContentGenre, ContentType, DurationProfile, TargetPlatform, WorkflowPreset
from app.domain.export_bundle import ExportBundle
from app.domain.project import Project
from app.domain.workflow_config import WorkflowConfig
from app.domain.workflow_run import WorkflowRun


def test_create_app_exposes_a_fastapi_application_and_shared_dependencies() -> None:
    api_app = create_app()

    assert isinstance(app, FastAPI)
    assert isinstance(api_app, FastAPI)
    assert api_app.title == "AI Content Studio API"
    assert api_app.version == "0.1.0"
    assert api_app.state.api_settings == get_api_settings()
    assert api_app.state.api_dependencies == build_api_dependencies(api_app.state.api_settings)

    assert api_app.openapi()["info"]["title"] == "AI Content Studio API"


def test_api_dependency_factories_return_consistent_settings() -> None:
    settings = get_api_settings()
    dependencies = build_api_dependencies(settings)

    assert settings == ApiSettings()
    assert dependencies == ApiDependencies(settings=settings)
    assert settings.api_prefix == "/api/v1"


def test_project_and_workflow_config_requests_accept_canonical_camel_case_payloads() -> None:
    project_request = ProjectCreateRequest.model_validate(
        {
            "workspaceId": "workspace_1",
            "name": "Short Video Launch",
            "contentType": "short_video",
            "contentGenre": "story",
            "targetPlatform": "youtube_shorts",
            "language": "en",
            "tone": "friendly",
        }
    )
    workflow_config_request = WorkflowConfigCreateRequest.model_validate(
        {
            "projectId": "project_1",
            "workflowPreset": "short_video",
            "contentType": "short_video",
            "contentGenre": "news",
            "durationProfile": "60s",
            "targetPlatform": "youtube_shorts",
            "language": "en",
            "tone": "neutral",
            "enabledModules": ["brief", "export"],
            "disabledModules": ["voiceover"],
            "providerConfig": {
                "LLMProvider": {"providerName": "mock", "enabled": True},
                "StorageProvider": {"providerName": "mock", "enabled": True},
            },
        }
    )

    assert project_request.genre is ContentGenre.STORY
    assert project_request.content_type is ContentType.SHORT_VIDEO
    assert project_request.model_dump(mode="json", by_alias=True)["contentGenre"] == "story"

    assert workflow_config_request.workflow_preset is WorkflowPreset.SHORT_VIDEO
    assert workflow_config_request.content_genre is ContentGenre.NEWS
    assert workflow_config_request.duration_profile is DurationProfile.SIXTY_SECONDS
    assert workflow_config_request.target_platform is TargetPlatform.YOUTUBE_SHORTS
    assert workflow_config_request.model_dump(mode="json", by_alias=True)["workflowPreset"] == "short_video"


def test_shared_schemas_round_trip_domain_models_with_camel_case_output() -> None:
    created_at = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

    project = Project(
        id="project_1",
        created_at=created_at,
        workspace_id="workspace_1",
        name="Launch Package",
        content_type=ContentType.SHORT_VIDEO,
        genre=ContentGenre.COMMENTARY,
        target_platform=TargetPlatform.TIKTOK,
        language="en",
        tone="direct",
        status="active",
        workflow_config_ids=["workflow_config_1"],
        workflow_run_ids=["workflow_run_1"],
    )
    workflow_config = WorkflowConfig.create(
        project_id="project_1",
        workflow_preset=WorkflowPreset.SHORT_VIDEO,
        content_type=ContentType.SHORT_VIDEO,
        content_genre=ContentGenre.NEWS,
        duration_profile=DurationProfile.SIXTY_SECONDS,
        target_platform=TargetPlatform.YOUTUBE_SHORTS,
        language="en",
        tone="neutral",
        enabled_modules=["brief", "scenePlanning", "export"],
        disabled_modules=["voiceover"],
        provider_config={
            "LLMProvider": {"providerName": "mock", "enabled": True},
            "StorageProvider": {"providerName": "mock", "enabled": True},
        },
    )
    workflow_config.created_at = created_at
    workflow_run = WorkflowRun.create(
        workflow_config_id="workflow_config_1",
        status="running",
        current_stage="brief",
        artifact_ids=["artifact_1"],
        approval_checkpoint_ids=["approval_1"],
    )
    workflow_run.created_at = created_at
    workflow_run.started_at = created_at
    artifact = Artifact.create(
        workflow_run_id="workflow_run_1",
        module_name="export",
        artifact_type="manifest",
        storage_key="artifacts/workflow_run_1/manifest.json",
        metadata={"kind": "bundle"},
    )
    artifact.created_at = created_at
    export_bundle = ExportBundle.create(
        workflow_run_id="workflow_run_1",
        manifest_path="artifacts/workflow_run_1/manifest.json",
        required_files=["manifest.json", "workflow_config.json", "workflow_run.json"],
        included_artifacts=["script.txt"],
        missing_optional_artifacts=["voiceover.wav"],
        approval_summary={"export": "pending"},
        provider_summary={"llm": "mock"},
        status="created",
    )
    export_bundle.created_at = created_at

    project_schema = ProjectSchema.model_validate(project)
    workflow_config_schema = WorkflowConfigSchema.model_validate(workflow_config)
    workflow_run_schema = WorkflowRunSchema.model_validate(workflow_run)
    artifact_schema = ArtifactSchema.model_validate(artifact)
    export_bundle_schema = ExportBundleSchema.model_validate(export_bundle)

    assert project_schema.model_dump(mode="json", by_alias=True)["contentGenre"] == "commentary"
    assert project_schema.workflow_config_ids == ["workflow_config_1"]
    assert workflow_config_schema.model_dump(mode="json", by_alias=True)["workflowPreset"] == "short_video"
    assert workflow_config_schema.model_dump(mode="json", by_alias=True)["contentGenre"] == "news"
    assert workflow_run_schema.model_dump(mode="json", by_alias=True)["workflowConfigId"] == "workflow_config_1"
    assert workflow_run_schema.model_dump(mode="json", by_alias=True)["artifactIds"] == ["artifact_1"]
    assert artifact_schema.model_dump(mode="json", by_alias=True)["storageKey"] == "artifacts/workflow_run_1/manifest.json"
    assert export_bundle_schema.model_dump(mode="json", by_alias=True)["missingOptionalArtifacts"] == ["voiceover.wav"]


def test_schemas_reject_invalid_enum_values_and_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        ProjectCreateRequest.model_validate(
            {
                "workspaceId": "workspace_1",
                "name": "Bad project",
                "contentType": "not_a_real_type",
                "contentGenre": "story",
                "targetPlatform": "youtube",
                "language": "en",
                "tone": "neutral",
            }
        )

    with pytest.raises(ValidationError):
        WorkflowRunCreateRequest.model_validate({})

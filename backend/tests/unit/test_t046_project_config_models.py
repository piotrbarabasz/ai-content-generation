from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from app.domain.base import DomainValidationError
from app.domain.enums import ContentGenre, ContentType, DurationProfile, TargetPlatform, WorkflowPreset
from app.domain.project import Project
from app.domain.provider_config import ProviderConfig
from app.domain.workflow_config import WorkflowConfig


def test_project_create_builds_valid_domain_model() -> None:
    project = Project.create(
        workspace_id="workspace_1",
        name="Launch Campaign",
        content_type=ContentType.SHORT_VIDEO,
        genre=ContentGenre.MARKETING,
        target_platform=TargetPlatform.YOUTUBE_SHORTS,
        language="en",
        tone="energetic",
    )

    assert project.workspace_id == "workspace_1"
    assert project.name == "Launch Campaign"
    assert project.content_type is ContentType.SHORT_VIDEO
    assert project.genre is ContentGenre.MARKETING
    assert project.target_platform is TargetPlatform.YOUTUBE_SHORTS
    assert project.language == "en"
    assert project.tone == "energetic"


def test_project_rejects_missing_name_and_invalid_enum_values() -> None:
    with pytest.raises(DomainValidationError, match="Project name is required"):
        Project.create(
            workspace_id="workspace_1",
            name="   ",
            content_type=ContentType.SHORT_VIDEO,
            genre=ContentGenre.MARKETING,
            target_platform=TargetPlatform.YOUTUBE_SHORTS,
            language="en",
            tone="energetic",
        )

    with pytest.raises(ValueError):
        Project.create(
            workspace_id="workspace_1",
            name="Launch Campaign",
            content_type="not_a_content_type",
            genre=ContentGenre.MARKETING,
            target_platform=TargetPlatform.YOUTUBE_SHORTS,
            language="en",
            tone="energetic",
        )


def test_project_serialization_keeps_plain_status_and_created_at() -> None:
    project = Project.create(
        workspace_id="workspace_2",
        name="Newsletter",
        content_type="script_only",
        genre="commentary",
        target_platform="generic_export",
        language="pl",
        tone="informative",
    )

    payload = asdict(project)
    encoded = json.dumps(payload, default=str)

    assert payload["status"] == "draft"
    assert isinstance(payload["workflow_config_ids"], list)
    assert isinstance(payload["workflow_run_ids"], list)
    assert "\"status\": \"draft\"" in encoded
    assert "\"created_at\":" in encoded
    assert "status" not in Project.__annotations__ or Project.__annotations__["status"] is str


def test_provider_config_create_validates_provider_type_and_serializes_settings() -> None:
    provider_config = ProviderConfig.create(
        workflow_config_id="workflow_config_1",
        provider_type="LLMProvider",
        provider_name="mock",
        enabled=True,
        settings={"temperature": 0.2, "nested": {"retries": 1}},
    )

    assert provider_config.workflow_config_id == "workflow_config_1"
    assert provider_config.provider_type is not None
    assert provider_config.provider_type.value == "LLMProvider"
    assert provider_config.provider_name == "mock"
    assert provider_config.enabled is True
    assert provider_config.settings == {"temperature": 0.2, "nested": {"retries": 1}}

    payload = asdict(provider_config)
    assert json.loads(json.dumps(payload, default=str))["provider_type"] == "LLMProvider"


def test_provider_config_rejects_unknown_provider_type() -> None:
    with pytest.raises(ValueError):
        ProviderConfig.create(
            workflow_config_id="workflow_config_1",
            provider_type="unknown_provider",
        )


def test_workflow_config_create_validates_required_fields_and_payload_aliases() -> None:
    config = WorkflowConfig.create(
        project_id="project_1",
        workflow_preset=WorkflowPreset.SHORT_VIDEO,
        content_type=ContentType.SHORT_VIDEO,
        content_genre=ContentGenre.NEWS,
        duration_profile=DurationProfile.SIXTY_SECONDS,
        target_platform=TargetPlatform.YOUTUBE_SHORTS,
        language="pl",
        tone="dynamic",
        enabled_modules=["brief", "export"],
        disabled_modules=["captions"],
        provider_config={"LLMProvider": {"providerName": "mock"}},
        render_config={"quality": "draft"},
        caption_config={},
        voice_config={},
        asset_config={},
        approval_policy={"script": "approved"},
        export_config={"includeManifest": True},
    )

    assert config.project_id == "project_1"
    assert config.workflow_preset is WorkflowPreset.SHORT_VIDEO
    assert config.enabled_modules == ["brief", "export"]
    assert config.disabled_modules == ["captions"]
    assert config.provider_config == {"LLMProvider": {"providerName": "mock"}}

    payload = asdict(config)
    encoded = json.dumps(payload, default=str)
    assert payload["language"] == "pl"
    assert payload["tone"] == "dynamic"
    assert "\"language\": \"pl\"" in encoded
    assert "\"tone\": \"dynamic\"" in encoded

    payload = {
        "projectId": "project_2",
        "workflowPreset": "long_form_script_voiceover",
        "contentType": "long_form_video",
        "contentGenre": "documentary",
        "durationProfile": "8_15min",
        "targetPlatform": "youtube",
        "language": "en",
        "tone": "informative",
        "enabledModules": ["brief", "outline", "export"],
        "disabledModules": ["videoRendering"],
        "providerConfig": {},
        "renderConfig": {},
        "captionConfig": {},
        "voiceConfig": {},
        "assetConfig": {},
        "approvalPolicy": {},
        "exportConfig": {},
    }
    payload_config = WorkflowConfig.from_payload(payload)

    assert payload_config.project_id == "project_2"
    assert payload_config.workflow_preset is WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER
    assert payload_config.content_type is ContentType.LONG_FORM_VIDEO
    assert payload_config.content_genre is ContentGenre.DOCUMENTARY


def test_workflow_config_rejects_missing_fields_invalid_values_and_module_conflicts() -> None:
    with pytest.raises(DomainValidationError, match="WorkflowConfig project_id is required"):
        WorkflowConfig.create(
            project_id="",
            workflow_preset="short_video",
            content_type="short_video",
            content_genre="news",
            duration_profile="60s",
            target_platform="youtube_shorts",
            language="pl",
            tone="dynamic",
        )

    with pytest.raises(DomainValidationError, match="WorkflowConfig language is required"):
        WorkflowConfig.create(
            project_id="project_3",
            workflow_preset="short_video",
            content_type="short_video",
            content_genre="news",
            duration_profile="60s",
            target_platform="youtube_shorts",
            language="",
            tone="dynamic",
        )

    with pytest.raises(DomainValidationError, match="WorkflowConfig tone is required"):
        WorkflowConfig.create(
            project_id="project_3",
            workflow_preset="short_video",
            content_type="short_video",
            content_genre="news",
            duration_profile="60s",
            target_platform="youtube_shorts",
            language="pl",
            tone="",
        )

    with pytest.raises(DomainValidationError, match="Modules cannot be both enabled and disabled"):
        WorkflowConfig.create(
            project_id="project_3",
            workflow_preset="short_video",
            content_type="short_video",
            content_genre="news",
            duration_profile="60s",
            target_platform="youtube_shorts",
            language="pl",
            tone="dynamic",
            enabled_modules=["brief", "captions"],
            disabled_modules=["captions"],
        )

    with pytest.raises(DomainValidationError):
        WorkflowConfig.create(
            project_id="project_4",
            workflow_preset="short_video",
            content_type="short_video",
            content_genre="not_a_genre",
            duration_profile="60s",
            target_platform="youtube_shorts",
            language="pl",
            tone="dynamic",
        )

    with pytest.raises(DomainValidationError, match="long_form_script_voiceover workflowPreset cannot use short_video contentType"):
        WorkflowConfig.create(
            project_id="project_5",
            workflow_preset="long_form_script_voiceover",
            content_type="short_video",
            content_genre="documentary",
            duration_profile="8_15min",
            target_platform="youtube",
            language="en",
            tone="informative",
        )

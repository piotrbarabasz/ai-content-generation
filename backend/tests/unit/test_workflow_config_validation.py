from __future__ import annotations

import pytest

from app.domain.base import DomainValidationError
from app.domain.enums import ContentGenre, ContentType, DurationProfile, TargetPlatform, WorkflowPreset
from app.domain.workflow_config import WorkflowConfig
from app.workflow.presets import LONG_FORM_SCRIPT_VOICEOVER_PRESET, SHORT_VIDEO_PRESET


@pytest.mark.parametrize(
    ("preset_definition", "project_id"),
    [
        (SHORT_VIDEO_PRESET, "project_1"),
        (LONG_FORM_SCRIPT_VOICEOVER_PRESET, "project_2"),
    ],
)
def test_workflow_config_accepts_canonical_preset_payloads(
    preset_definition,
    project_id: str,
) -> None:
    config = WorkflowConfig.from_payload(
        preset_definition.build_workflow_config_payload(project_id=project_id)
    )

    assert config.project_id == project_id
    assert config.workflow_preset is preset_definition.workflow_preset
    assert config.content_type is preset_definition.content_type
    assert config.content_genre is preset_definition.content_genre
    assert config.duration_profile is preset_definition.duration_profile
    assert config.target_platform is preset_definition.target_platform
    assert config.enabled_modules == list(preset_definition.required_modules)
    assert config.disabled_modules == list(preset_definition.optional_modules)


def test_workflow_config_rejects_invalid_enum_values() -> None:
    with pytest.raises(DomainValidationError):
        WorkflowConfig.create(
            project_id="project_3",
            workflow_preset="short_video",
            content_type="short_video",
            content_genre="not_a_genre",
            duration_profile="60s",
            target_platform="youtube_shorts",
            language="pl",
            tone="dynamic",
        )


def test_workflow_config_rejects_enabled_and_disabled_module_conflicts() -> None:
    with pytest.raises(DomainValidationError):
        WorkflowConfig.create(
            project_id="project_4",
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


def test_provider_validation_runs_after_config_validation() -> None:
    calls: list[WorkflowPreset] = []

    def provider_validator(config: WorkflowConfig) -> None:
        calls.append(config.workflow_preset)

    WorkflowConfig.create(
        project_id="project_5",
        workflow_preset="short_video",
        content_type="short_video",
        content_genre="news",
        duration_profile="60s",
        target_platform="youtube_shorts",
        language="pl",
        tone="dynamic",
        provider_validator=provider_validator,
    )

    assert calls == [WorkflowPreset.SHORT_VIDEO]

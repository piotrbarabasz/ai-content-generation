from __future__ import annotations

import pytest

from app.api.schemas import ExportConfigSchema, WorkflowConfigCreateRequest
from app.domain.base import DomainValidationError
from app.domain.export_config import ExportConfig, LocalizationStrategy
from app.domain.workflow_config import WorkflowConfig
from app.workflow.presets import LONG_FORM_SCRIPT_VOICEOVER_PRESET, SHORT_VIDEO_PRESET


def test_export_config_validates_and_preserves_order() -> None:
    config = ExportConfig.from_mapping(
        {
            "localizationStrategy": "platform_auto_dub",
            "localizationTargets": ["pl", "de"],
            "manualAcceptanceRequired": True,
            "customAudioFallbackEnabled": True,
        },
        source_language="en",
    )
    assert config.localization_strategy is LocalizationStrategy.PLATFORM_AUTO_DUB
    assert config.localization_targets == ("pl", "de")
    assert ExportConfigSchema.model_validate(config.to_payload()).model_dump(by_alias=True) == config.to_payload()


@pytest.mark.parametrize(
    "payload,match",
    [
        ({"unknown": True}, "Unknown"),
        ({"localizationStrategy": "future"}, "Unsupported"),
        ({"localizationStrategy": "none", "localizationTargets": ["pl"]}, "requires no"),
        ({"localizationStrategy": "platform_auto_dub"}, "at least one"),
        ({"localizationStrategy": "custom_audio_tracks"}, "at least one"),
        ({"localizationStrategy": "platform_auto_dub", "localizationTargets": ["pl", "PL"]}, "unique"),
        ({"localizationStrategy": "platform_auto_dub", "localizationTargets": [""]}, "empty"),
        ({"localizationStrategy": "platform_auto_dub", "localizationTargets": ["en"]}, "source language"),
    ],
)
def test_export_config_rejects_invalid_payloads(payload, match: str) -> None:
    with pytest.raises(DomainValidationError, match=match):
        ExportConfig.from_mapping(payload, source_language="en")


def test_workflow_config_keeps_source_language_separate_and_empty_config_compatible() -> None:
    long_payload = LONG_FORM_SCRIPT_VOICEOVER_PRESET.build_workflow_config_payload(
        project_id="project_en"
    )
    request = WorkflowConfigCreateRequest.model_validate(long_payload)
    config = request.to_domain()
    assert config.language == "en"
    assert "sourceLanguage" not in config.export_config
    assert config.export_config == {
        "localizationStrategy": "platform_auto_dub",
        "localizationTargets": ["pl"],
        "manualAcceptanceRequired": True,
        "customAudioFallbackEnabled": True,
    }

    short_payload = SHORT_VIDEO_PRESET.build_workflow_config_payload(project_id="project_short")
    short = WorkflowConfig.from_payload(short_payload)
    assert short.export_config == {}
    assert short.effective_export_config.localization_strategy is LocalizationStrategy.NONE

from __future__ import annotations

import pytest

from app.domain.enums import (
    ContentGenre,
    ContentType,
    DurationProfile,
    TargetPlatform,
    WorkflowPreset,
)
from app.workflow.presets import (
    LONG_FORM_SCRIPT_VOICEOVER_PRESET,
    MVP_WORKFLOW_PRESETS,
    SHORT_VIDEO_PRESET,
    WorkflowPresetDefinition,
)
from app.workflow.registry import (
    MVP_WORKFLOW_PRESET_REGISTRY,
    WorkflowPresetRegistry,
    WorkflowPresetRegistryError,
    build_mvp_workflow_preset_registry,
)


def test_mvp_workflow_presets_define_the_two_canonical_workflows() -> None:
    short_video = SHORT_VIDEO_PRESET
    long_form = LONG_FORM_SCRIPT_VOICEOVER_PRESET

    assert MVP_WORKFLOW_PRESETS == (short_video, long_form)

    assert short_video.workflow_preset is WorkflowPreset.SHORT_VIDEO
    assert short_video.content_type is ContentType.SHORT_VIDEO
    assert short_video.content_genre is ContentGenre.NEWS
    assert short_video.duration_profile is DurationProfile.SIXTY_SECONDS
    assert short_video.target_platform is TargetPlatform.YOUTUBE_SHORTS
    assert short_video.module_sequence == (
        "brief",
        "scenePlanning",
        "voiceover",
        "captions",
        "videoRendering",
        "export",
    )
    assert short_video.required_modules == ("brief", "scenePlanning", "videoRendering", "export")
    assert short_video.optional_modules == ("voiceover", "captions")
    assert short_video.default_enabled_modules == short_video.required_modules
    assert short_video.default_disabled_modules == short_video.optional_modules
    assert short_video.expected_artifacts == (
        "brief.json",
        "scene_plan.json",
        "voiceover.wav",
        "speech_timeline.json",
        "captions.ass",
        "captions.json",
        "render.mp4",
        "manifest.json",
    )
    assert short_video.default_provider_config == {
        "LLMProvider": {"providerName": "mock", "enabled": True},
        "TTSProvider": {"providerName": "mock", "enabled": True},
        "CaptionProvider": {"providerName": "mock", "enabled": True},
        "VideoRendererProvider": {"providerName": "mock", "enabled": True},
        "StorageProvider": {"providerName": "mock", "enabled": True},
    }

    assert long_form.workflow_preset is WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER
    assert long_form.content_type is ContentType.LONG_FORM_VIDEO
    assert long_form.content_genre is ContentGenre.DOCUMENTARY
    assert long_form.duration_profile is DurationProfile.EIGHT_FIFTEEN_MINUTES
    assert long_form.target_platform is TargetPlatform.YOUTUBE
    assert long_form.module_sequence == (
        "brief",
        "research",
        "dossier",
        "outline",
        "scriptGeneration",
        "postProcessing",
        "qa",
        "voiceover",
        "export",
    )
    assert long_form.required_modules == (
        "brief",
        "outline",
        "scriptGeneration",
        "postProcessing",
        "qa",
        "export",
    )
    assert long_form.optional_modules == ("research", "dossier", "voiceover")
    assert long_form.expected_artifacts == (
        "brief.json",
        "research.json",
        "dossier.json",
        "outline.json",
        "script.txt",
        "post_processed_script.txt",
        "qa_report.json",
        "voiceover.wav",
        "speech_timeline.json",
        "manifest.json",
    )
    assert long_form.default_provider_config == {
        "LLMProvider": {"providerName": "mock", "enabled": True},
        "TTSProvider": {"providerName": "mock", "enabled": True},
        "StorageProvider": {"providerName": "mock", "enabled": True},
    }


def test_workflow_preset_registry_lists_and_builds_payload_defaults() -> None:
    registry = build_mvp_workflow_preset_registry()

    assert registry.list_presets() == MVP_WORKFLOW_PRESETS
    assert registry.has("short_video") is True
    assert registry.get("long_form_script_voiceover") is LONG_FORM_SCRIPT_VOICEOVER_PRESET
    assert MVP_WORKFLOW_PRESET_REGISTRY.get(WorkflowPreset.SHORT_VIDEO) is SHORT_VIDEO_PRESET

    short_payload = registry.build_workflow_config_payload("short_video", project_id="project_1")
    long_payload = registry.build_workflow_config_payload(WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER)

    assert short_payload == {
        "workflowPreset": "short_video",
        "contentType": "short_video",
        "contentGenre": "news",
        "durationProfile": "60s",
        "targetPlatform": "youtube_shorts",
        "language": "en",
        "tone": "neutral",
        "enabledModules": ["brief", "scenePlanning", "videoRendering", "export"],
        "disabledModules": ["voiceover", "captions"],
        "providerConfig": {
            "LLMProvider": {"providerName": "mock", "enabled": True},
            "TTSProvider": {"providerName": "mock", "enabled": True},
            "CaptionProvider": {"providerName": "mock", "enabled": True},
            "VideoRendererProvider": {"providerName": "mock", "enabled": True},
            "StorageProvider": {"providerName": "mock", "enabled": True},
        },
        "renderConfig": {},
        "captionConfig": {},
        "voiceConfig": {},
        "assetConfig": {},
        "approvalPolicy": {},
        "exportConfig": {},
        "projectId": "project_1",
    }
    assert long_payload == {
        "workflowPreset": "long_form_script_voiceover",
        "contentType": "long_form_video",
        "contentGenre": "documentary",
        "durationProfile": "8_15min",
        "targetPlatform": "youtube",
        "language": "en",
        "tone": "neutral",
        "enabledModules": ["brief", "outline", "scriptGeneration", "postProcessing", "qa", "export"],
        "disabledModules": ["research", "dossier", "voiceover"],
        "providerConfig": {
            "LLMProvider": {"providerName": "mock", "enabled": True},
            "TTSProvider": {"providerName": "mock", "enabled": True},
            "StorageProvider": {"providerName": "mock", "enabled": True},
        },
        "renderConfig": {},
        "captionConfig": {},
        "voiceConfig": {},
        "assetConfig": {},
        "approvalPolicy": {},
        "exportConfig": {
            "localizationStrategy": "platform_auto_dub",
            "localizationTargets": ["pl"],
            "manualAcceptanceRequired": True,
            "customAudioFallbackEnabled": True,
        },
    }


def test_workflow_preset_definition_rejects_invalid_module_lists() -> None:
    with pytest.raises(ValueError, match="required_modules and optional_modules must not overlap"):
        WorkflowPresetDefinition(
            workflow_preset="short_video",
            content_type="short_video",
            content_genre="news",
            duration_profile="60s",
            target_platform="youtube_shorts",
            module_sequence=("brief", "export"),
            required_modules=("brief", "export"),
            optional_modules=("export",),
            expected_artifacts=("brief.json",),
            default_provider_config={},
        )


def test_workflow_preset_registry_rejects_unknown_and_duplicate_presets() -> None:
    registry = WorkflowPresetRegistry([SHORT_VIDEO_PRESET])

    with pytest.raises(WorkflowPresetRegistryError, match="Unknown workflow preset: not_a_preset"):
        registry.get("not_a_preset")

    with pytest.raises(WorkflowPresetRegistryError, match="Duplicate workflow preset registration: short_video"):
        registry.register(SHORT_VIDEO_PRESET)

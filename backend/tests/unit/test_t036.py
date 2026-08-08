from __future__ import annotations

from app.domain.enums import ContentGenre, ContentType, DurationProfile, TargetPlatform, WorkflowPreset
from app.workflow.presets import LONG_FORM_SCRIPT_VOICEOVER_PRESET, MVP_WORKFLOW_PRESETS
from app.workflow.registry import build_mvp_workflow_preset_registry


def test_long_form_workflow_preset_is_the_canonical_mvp_long_form_definition() -> None:
    preset = LONG_FORM_SCRIPT_VOICEOVER_PRESET

    assert preset.workflow_preset is WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER
    assert preset.content_type is ContentType.LONG_FORM_VIDEO
    assert preset.content_genre is ContentGenre.DOCUMENTARY
    assert preset.duration_profile is DurationProfile.EIGHT_FIFTEEN_MINUTES
    assert preset.target_platform is TargetPlatform.YOUTUBE
    assert MVP_WORKFLOW_PRESETS[1] is preset

    assert preset.module_sequence == (
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
    assert preset.required_modules == ("brief", "outline", "scriptGeneration", "postProcessing", "qa", "export")
    assert preset.optional_modules == ("research", "dossier", "voiceover")
    assert preset.default_enabled_modules == preset.required_modules
    assert preset.default_disabled_modules == preset.optional_modules
    assert preset.expected_artifacts == (
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


def test_long_form_workflow_preset_payload_matches_the_registry_default() -> None:
    registry = build_mvp_workflow_preset_registry()
    preset = registry.get("long_form_script_voiceover")

    assert preset is LONG_FORM_SCRIPT_VOICEOVER_PRESET
    assert registry.list_presets() == MVP_WORKFLOW_PRESETS
    assert registry.build_workflow_config_payload("long_form_script_voiceover") == {
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

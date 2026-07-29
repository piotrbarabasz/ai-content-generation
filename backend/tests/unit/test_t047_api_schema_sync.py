from __future__ import annotations

from app.api.schemas import WorkflowConfigCreateRequest, WorkflowConfigSchema
from app.domain.enums import ContentGenre, ContentType, DurationProfile, TargetPlatform, WorkflowPreset
from app.domain.workflow_config import WorkflowConfig


def test_workflow_config_api_schema_matches_the_canonical_domain_fields() -> None:
    expected_domain_fields = tuple(
        field_name
        for field_name in WorkflowConfig.__dataclass_fields__
        if field_name not in {"id", "created_at"}
    )

    assert tuple(WorkflowConfigCreateRequest.model_fields) == expected_domain_fields
    assert tuple(WorkflowConfigSchema.model_fields) == expected_domain_fields + ("id", "created_at")


def test_workflow_config_api_schema_uses_the_same_enum_types_as_the_domain_model() -> None:
    expected_types = {
        "workflow_preset": WorkflowPreset,
        "content_type": ContentType,
        "content_genre": ContentGenre,
        "duration_profile": DurationProfile,
        "target_platform": TargetPlatform,
    }

    for field_name, expected_type in expected_types.items():
        assert WorkflowConfigCreateRequest.model_fields[field_name].annotation is expected_type


def test_workflow_config_api_schema_exposes_the_canonical_enum_values_in_json_schema() -> None:
    json_schema = WorkflowConfigCreateRequest.model_json_schema()
    expected_enums = {
        "WorkflowPreset": [
            "short_video",
            "long_form_script_voiceover",
        ],
        "ContentType": [
            "short_video",
            "long_form_video",
            "audio_only",
            "script_only",
        ],
        "ContentGenre": [
            "news",
            "story",
            "documentary",
            "educational",
            "tutorial",
            "marketing",
            "commentary",
            "listicle",
        ],
        "DurationProfile": [
            "15_30s",
            "60s",
            "3_5min",
            "8_15min",
            "custom",
        ],
        "TargetPlatform": [
            "tiktok",
            "youtube_shorts",
            "youtube",
            "instagram",
            "linkedin",
            "generic_export",
        ],
    }

    for property_name, definition_name in {
        "workflowPreset": "WorkflowPreset",
        "contentType": "ContentType",
        "contentGenre": "ContentGenre",
        "durationProfile": "DurationProfile",
        "targetPlatform": "TargetPlatform",
    }.items():
        assert json_schema["properties"][property_name]["$ref"] == f"#/$defs/{definition_name}"
        assert json_schema["$defs"][definition_name]["enum"] == expected_enums[definition_name]

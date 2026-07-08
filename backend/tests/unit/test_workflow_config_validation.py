import unittest

from app.domain.base import DomainValidationError
from app.domain.enums import (
    ContentGenre,
    ContentType,
    DurationProfile,
    TargetPlatform,
    WorkflowPreset,
)
from app.domain.workflow_config import WorkflowConfig


class WorkflowConfigValidationTests(unittest.TestCase):
    def test_valid_short_video_config(self) -> None:
        config = WorkflowConfig.create(
            project_id="project_1",
            workflow_preset="short_video",
            content_type="short_video",
            content_genre="news",
            duration_profile="60s",
            target_platform="youtube_shorts",
            language="pl",
            tone="dynamic",
            enabled_modules=["brief", "scenePlanning", "videoRendering", "export"],
            disabled_modules=["voiceover", "thumbnail", "publishing"],
        )

        self.assertEqual(config.workflow_preset, WorkflowPreset.SHORT_VIDEO)
        self.assertEqual(config.content_type, ContentType.SHORT_VIDEO)
        self.assertEqual(config.content_genre, ContentGenre.NEWS)
        self.assertEqual(config.duration_profile, DurationProfile.SIXTY_SECONDS)
        self.assertEqual(config.target_platform, TargetPlatform.YOUTUBE_SHORTS)

    def test_valid_long_form_script_voiceover_config(self) -> None:
        config = WorkflowConfig.create(
            project_id="project_2",
            workflow_preset="long_form_script_voiceover",
            content_type="long_form_video",
            content_genre="documentary",
            duration_profile="8_15min",
            target_platform="youtube",
            language="en",
            tone="informative",
            enabled_modules=["brief", "outline", "scriptGeneration", "qa", "export"],
            disabled_modules=["videoRendering", "captions"],
        )

        self.assertEqual(
            config.workflow_preset, WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER
        )
        self.assertEqual(config.content_type, ContentType.LONG_FORM_VIDEO)

    def test_payload_accepts_canonical_camel_case_schema(self) -> None:
        config = WorkflowConfig.from_payload(
            {
                "projectId": "project_3",
                "workflowPreset": "short_video",
                "contentType": "short_video",
                "contentGenre": "story",
                "durationProfile": "15_30s",
                "targetPlatform": "tiktok",
                "language": "pl",
                "tone": "dramatic",
                "enabledModules": ["brief", "export"],
                "disabledModules": ["captions"],
                "providerConfig": {},
                "renderConfig": {},
                "captionConfig": {},
                "voiceConfig": {},
                "assetConfig": {},
                "approvalPolicy": {},
                "exportConfig": {},
            }
        )

        self.assertEqual(config.content_genre, ContentGenre.STORY)

    def test_invalid_enum_value_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
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

    def test_enabled_disabled_module_conflict_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            WorkflowConfig.create(
                project_id="project_5",
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

    def test_provider_validation_runs_after_config_validation(self) -> None:
        calls = []

        def provider_validator(config: WorkflowConfig) -> None:
            calls.append(config.workflow_preset)

        WorkflowConfig.create(
            project_id="project_6",
            workflow_preset="short_video",
            content_type="short_video",
            content_genre="news",
            duration_profile="60s",
            target_platform="youtube_shorts",
            language="pl",
            tone="dynamic",
            provider_validator=provider_validator,
        )

        self.assertEqual(calls, [WorkflowPreset.SHORT_VIDEO])


if __name__ == "__main__":
    unittest.main()

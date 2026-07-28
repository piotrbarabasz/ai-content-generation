"""Declarative MVP workflow presets."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import (
    ContentGenre,
    ContentType,
    DurationProfile,
    ProviderType,
    TargetPlatform,
    WorkflowPreset,
)
from app.domain.types import JsonDict


def _normalize_text(value: str, *, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"WorkflowPresetDefinition {field_name} is required.")
    return normalized


def _coerce_string_tuple(
    values: tuple[str, ...] | list[str] | None,
    *,
    field_name: str,
) -> tuple[str, ...]:
    if values is None:
        return ()
    return tuple(_normalize_text(value, field_name=field_name) for value in values)


def _coerce_provider_config(provider_config: dict[str, Any] | None) -> JsonDict:
    if provider_config is None:
        return {}
    normalized: JsonDict = {}
    for provider_type, config in provider_config.items():
        if not isinstance(config, dict):
            raise ValueError(
                f"WorkflowPresetDefinition provider config for {provider_type} must be an object."
            )
        normalized[str(provider_type)] = dict(config)
    return normalized


def _build_mock_provider_config(*provider_types: ProviderType) -> JsonDict:
    return {
        provider_type.value: {
            "providerName": "mock",
            "enabled": True,
        }
        for provider_type in provider_types
    }


@dataclass(slots=True, frozen=True)
class WorkflowPresetDefinition:
    """Static declaration for one MVP workflow preset."""

    workflow_preset: WorkflowPreset
    content_type: ContentType
    content_genre: ContentGenre
    duration_profile: DurationProfile
    target_platform: TargetPlatform
    module_sequence: tuple[str, ...] = field(default_factory=tuple)
    required_modules: tuple[str, ...] = field(default_factory=tuple)
    optional_modules: tuple[str, ...] = field(default_factory=tuple)
    expected_artifacts: tuple[str, ...] = field(default_factory=tuple)
    default_provider_config: JsonDict = field(default_factory=dict)
    default_language: str = "en"
    default_tone: str = "neutral"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workflow_preset",
            WorkflowPreset(self.workflow_preset),
        )
        object.__setattr__(self, "content_type", ContentType(self.content_type))
        object.__setattr__(self, "content_genre", ContentGenre(self.content_genre))
        object.__setattr__(self, "duration_profile", DurationProfile(self.duration_profile))
        object.__setattr__(self, "target_platform", TargetPlatform(self.target_platform))
        object.__setattr__(
            self,
            "module_sequence",
            _coerce_string_tuple(self.module_sequence, field_name="module_sequence"),
        )
        object.__setattr__(
            self,
            "required_modules",
            _coerce_string_tuple(self.required_modules, field_name="required_modules"),
        )
        object.__setattr__(
            self,
            "optional_modules",
            _coerce_string_tuple(self.optional_modules, field_name="optional_modules"),
        )
        object.__setattr__(
            self,
            "expected_artifacts",
            _coerce_string_tuple(self.expected_artifacts, field_name="expected_artifacts"),
        )
        object.__setattr__(
            self,
            "default_provider_config",
            _coerce_provider_config(dict(self.default_provider_config)),
        )
        object.__setattr__(self, "default_language", _normalize_text(self.default_language, field_name="default_language"))
        object.__setattr__(self, "default_tone", _normalize_text(self.default_tone, field_name="default_tone"))

        if not self.module_sequence:
            raise ValueError("WorkflowPresetDefinition module_sequence is required.")
        if not self.required_modules:
            raise ValueError("WorkflowPresetDefinition required_modules is required.")
        if not self.expected_artifacts:
            raise ValueError("WorkflowPresetDefinition expected_artifacts is required.")
        if len(set(self.module_sequence)) != len(self.module_sequence):
            raise ValueError("WorkflowPresetDefinition module_sequence must be unique.")
        if len(set(self.required_modules)) != len(self.required_modules):
            raise ValueError("WorkflowPresetDefinition required_modules must be unique.")
        if len(set(self.optional_modules)) != len(self.optional_modules):
            raise ValueError("WorkflowPresetDefinition optional_modules must be unique.")

        sequence_modules = set(self.module_sequence)
        required_modules = set(self.required_modules)
        optional_modules = set(self.optional_modules)

        if not required_modules.issubset(sequence_modules):
            missing = ", ".join(sorted(required_modules - sequence_modules))
            raise ValueError(
                f"WorkflowPresetDefinition required_modules must appear in module_sequence: {missing}."
            )
        if not optional_modules.issubset(sequence_modules):
            missing = ", ".join(sorted(optional_modules - sequence_modules))
            raise ValueError(
                f"WorkflowPresetDefinition optional_modules must appear in module_sequence: {missing}."
            )
        if required_modules.intersection(optional_modules):
            overlap = ", ".join(sorted(required_modules.intersection(optional_modules)))
            raise ValueError(
                f"WorkflowPresetDefinition required_modules and optional_modules must not overlap: {overlap}."
            )

    @property
    def default_enabled_modules(self) -> tuple[str, ...]:
        """Return the modules enabled by default for a new workflow config."""

        return self.required_modules

    @property
    def default_disabled_modules(self) -> tuple[str, ...]:
        """Return the modules disabled by default for a new workflow config."""

        return self.optional_modules

    def build_workflow_config_payload(self, *, project_id: str | None = None) -> JsonDict:
        """Return a canonical config payload for this preset."""

        payload: JsonDict = {
            "workflowPreset": self.workflow_preset.value,
            "contentType": self.content_type.value,
            "contentGenre": self.content_genre.value,
            "durationProfile": self.duration_profile.value,
            "targetPlatform": self.target_platform.value,
            "language": self.default_language,
            "tone": self.default_tone,
            "enabledModules": list(self.default_enabled_modules),
            "disabledModules": list(self.default_disabled_modules),
            "providerConfig": deepcopy(self.default_provider_config),
            "renderConfig": {},
            "captionConfig": {},
            "voiceConfig": {},
            "assetConfig": {},
            "approvalPolicy": {},
            "exportConfig": {},
        }
        if project_id is not None:
            payload["projectId"] = project_id
        return payload


SHORT_VIDEO_PRESET = WorkflowPresetDefinition(
    workflow_preset=WorkflowPreset.SHORT_VIDEO,
    content_type=ContentType.SHORT_VIDEO,
    content_genre=ContentGenre.NEWS,
    duration_profile=DurationProfile.SIXTY_SECONDS,
    target_platform=TargetPlatform.YOUTUBE_SHORTS,
    module_sequence=(
        "brief",
        "scenePlanning",
        "voiceover",
        "captions",
        "videoRendering",
        "export",
    ),
    required_modules=(
        "brief",
        "scenePlanning",
        "videoRendering",
        "export",
    ),
    optional_modules=(
        "voiceover",
        "captions",
    ),
    expected_artifacts=(
        "brief.json",
        "scene_plan.json",
        "voiceover.wav",
        "speech_timeline.json",
        "captions.ass",
        "captions.json",
        "render.mp4",
        "manifest.json",
    ),
    default_provider_config=_build_mock_provider_config(
        ProviderType.LLM,
        ProviderType.TTS,
        ProviderType.CAPTION,
        ProviderType.VIDEO_RENDERER,
        ProviderType.STORAGE,
    ),
)


LONG_FORM_SCRIPT_VOICEOVER_PRESET = WorkflowPresetDefinition(
    workflow_preset=WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER,
    content_type=ContentType.LONG_FORM_VIDEO,
    content_genre=ContentGenre.DOCUMENTARY,
    duration_profile=DurationProfile.EIGHT_FIFTEEN_MINUTES,
    target_platform=TargetPlatform.YOUTUBE,
    module_sequence=(
        "brief",
        "research",
        "dossier",
        "outline",
        "scriptGeneration",
        "postProcessing",
        "qa",
        "voiceover",
        "export",
    ),
    required_modules=(
        "brief",
        "outline",
        "scriptGeneration",
        "postProcessing",
        "qa",
        "export",
    ),
    optional_modules=(
        "research",
        "dossier",
        "voiceover",
    ),
    expected_artifacts=(
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
    ),
    default_provider_config=_build_mock_provider_config(
        ProviderType.LLM,
        ProviderType.TTS,
        ProviderType.STORAGE,
    ),
)


MVP_WORKFLOW_PRESETS: tuple[WorkflowPresetDefinition, ...] = (
    SHORT_VIDEO_PRESET,
    LONG_FORM_SCRIPT_VOICEOVER_PRESET,
)

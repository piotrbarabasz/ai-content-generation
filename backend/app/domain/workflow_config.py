"""Canonical WorkflowConfig schema and validation."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.domain.base import DomainEntity, DomainValidationError, new_id
from app.domain.enums import (
    ContentGenre,
    ContentType,
    DurationProfile,
    TargetPlatform,
    WorkflowPreset,
)
from app.domain.types import JsonDict


ProviderValidator = Callable[["WorkflowConfig"], None]


def _coerce_str_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if values is None:
        return []
    return [str(value) for value in values]


@dataclass(slots=True)
class WorkflowConfig(DomainEntity):
    project_id: str = ""
    workflow_preset: WorkflowPreset = WorkflowPreset.SHORT_VIDEO
    content_type: ContentType = ContentType.SHORT_VIDEO
    content_genre: ContentGenre = ContentGenre.NEWS
    duration_profile: DurationProfile = DurationProfile.SIXTY_SECONDS
    target_platform: TargetPlatform = TargetPlatform.GENERIC_EXPORT
    language: str = "en"
    tone: str = "neutral"
    enabled_modules: list[str] = field(default_factory=list)
    disabled_modules: list[str] = field(default_factory=list)
    provider_config: JsonDict = field(default_factory=dict)
    render_config: JsonDict = field(default_factory=dict)
    caption_config: JsonDict = field(default_factory=dict)
    voice_config: JsonDict = field(default_factory=dict)
    asset_config: JsonDict = field(default_factory=dict)
    approval_policy: JsonDict = field(default_factory=dict)
    export_config: JsonDict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        workflow_preset: WorkflowPreset | str,
        content_type: ContentType | str,
        content_genre: ContentGenre | str,
        duration_profile: DurationProfile | str,
        target_platform: TargetPlatform | str,
        language: str,
        tone: str,
        enabled_modules: list[str] | tuple[str, ...] | None = None,
        disabled_modules: list[str] | tuple[str, ...] | None = None,
        provider_config: JsonDict | None = None,
        render_config: JsonDict | None = None,
        caption_config: JsonDict | None = None,
        voice_config: JsonDict | None = None,
        asset_config: JsonDict | None = None,
        approval_policy: JsonDict | None = None,
        export_config: JsonDict | None = None,
        provider_validator: ProviderValidator | None = None,
    ) -> "WorkflowConfig":
        try:
            config = cls(
                id=new_id("workflow_config"),
                project_id=project_id,
                workflow_preset=WorkflowPreset(workflow_preset),
                content_type=ContentType(content_type),
                content_genre=ContentGenre(content_genre),
                duration_profile=DurationProfile(duration_profile),
                target_platform=TargetPlatform(target_platform),
                language=language,
                tone=tone,
                enabled_modules=_coerce_str_list(enabled_modules),
                disabled_modules=_coerce_str_list(disabled_modules),
                provider_config=provider_config or {},
                render_config=render_config or {},
                caption_config=caption_config or {},
                voice_config=voice_config or {},
                asset_config=asset_config or {},
                approval_policy=approval_policy or {},
                export_config=export_config or {},
            )
        except ValueError as exc:
            raise DomainValidationError(str(exc)) from exc

        config.validate(provider_validator=provider_validator)
        return config

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        provider_validator: ProviderValidator | None = None,
    ) -> "WorkflowConfig":
        aliases = {
            "projectId": "project_id",
            "workflowPreset": "workflow_preset",
            "contentType": "content_type",
            "contentGenre": "content_genre",
            "durationProfile": "duration_profile",
            "targetPlatform": "target_platform",
            "enabledModules": "enabled_modules",
            "disabledModules": "disabled_modules",
            "providerConfig": "provider_config",
            "renderConfig": "render_config",
            "captionConfig": "caption_config",
            "voiceConfig": "voice_config",
            "assetConfig": "asset_config",
            "approvalPolicy": "approval_policy",
            "exportConfig": "export_config",
        }
        normalized = {aliases.get(key, key): value for key, value in payload.items()}
        return cls.create(provider_validator=provider_validator, **normalized)

    def validate(self, *, provider_validator: ProviderValidator | None = None) -> None:
        if not self.project_id:
            raise DomainValidationError("WorkflowConfig project_id is required.")
        if not self.language:
            raise DomainValidationError("WorkflowConfig language is required.")
        if not self.tone:
            raise DomainValidationError("WorkflowConfig tone is required.")

        conflicts = set(self.enabled_modules).intersection(self.disabled_modules)
        if conflicts:
            names = ", ".join(sorted(conflicts))
            raise DomainValidationError(
                f"Modules cannot be both enabled and disabled: {names}."
            )

        if (
            self.workflow_preset is WorkflowPreset.SHORT_VIDEO
            and self.content_type is not ContentType.SHORT_VIDEO
        ):
            raise DomainValidationError(
                "short_video workflowPreset requires contentType short_video."
            )

        if (
            self.workflow_preset is WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER
            and self.content_type is ContentType.SHORT_VIDEO
        ):
            raise DomainValidationError(
                "long_form_script_voiceover workflowPreset cannot use short_video contentType."
            )

        if provider_validator is not None:
            provider_validator(self)

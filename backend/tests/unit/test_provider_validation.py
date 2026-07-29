from __future__ import annotations

import pytest

from app.domain.base import DomainValidationError
from app.domain.enums import ContentGenre, ContentType, DurationProfile, TargetPlatform, WorkflowPreset
from app.domain.workflow_config import WorkflowConfig
from app.providers.mocks import build_mock_provider_registry
from app.providers.validation import validate_provider_availability
from app.workflow.execution import ModuleExecutionPlan, ModuleExecutionStep


def _workflow_config(
    *,
    enabled_modules: tuple[str, ...],
    disabled_modules: tuple[str, ...] = (),
    provider_config: dict[str, dict[str, object]] | None = None,
) -> WorkflowConfig:
    return WorkflowConfig.create(
        project_id="project_1",
        workflow_preset=WorkflowPreset.SHORT_VIDEO,
        content_type=ContentType.SHORT_VIDEO,
        content_genre=ContentGenre.NEWS,
        duration_profile=DurationProfile.SIXTY_SECONDS,
        target_platform=TargetPlatform.YOUTUBE_SHORTS,
        language="en",
        tone="dynamic",
        enabled_modules=list(enabled_modules),
        disabled_modules=list(disabled_modules),
        provider_config=provider_config or {},
    )


def _plan(*, voiceover_enabled: bool, export_enabled: bool = False) -> ModuleExecutionPlan:
    steps = [
        ModuleExecutionStep(
            module_name="brief",
            enabled=True,
            status="pending",
            artifact_outputs=("brief.json",),
        ),
        ModuleExecutionStep(
            module_name="voiceover",
            enabled=voiceover_enabled,
            status="pending" if voiceover_enabled else "skipped",
            artifact_outputs=("voiceover.wav",),
        ),
    ]
    if export_enabled:
        steps.append(
            ModuleExecutionStep(
                module_name="export",
                enabled=True,
                status="pending",
                artifact_outputs=("manifest.json",),
            )
        )
    return ModuleExecutionPlan(steps=tuple(steps))


def test_missing_provider_for_enabled_module_fails_before_run_start() -> None:
    registry = build_mock_provider_registry()
    workflow_config = _workflow_config(enabled_modules=("brief",))

    with pytest.raises(
        DomainValidationError,
        match="Missing provider LLMProvider for enabled workflow modules.",
    ):
        validate_provider_availability(
            workflow_config=workflow_config,
            plan=_plan(voiceover_enabled=False),
            provider_registry=registry,
        )


@pytest.mark.parametrize(
    ("provider_config", "expected_message"),
    [
        ({"NotAProvider": {"providerName": "mock"}}, "Unknown provider type: NotAProvider."),
        ({"LLMProvider": {"providerName": "missing"}}, "Unknown provider: LLMProvider/missing."),
    ],
)
def test_provider_validation_rejects_invalid_provider_type_and_unknown_name(
    provider_config: dict[str, dict[str, object]],
    expected_message: str,
) -> None:
    registry = build_mock_provider_registry()
    workflow_config = _workflow_config(
        enabled_modules=("brief",),
        provider_config=provider_config,
    )

    with pytest.raises(DomainValidationError, match=expected_message):
        validate_provider_availability(
            workflow_config=workflow_config,
            plan=_plan(voiceover_enabled=False),
            provider_registry=registry,
        )


def test_disabled_optional_module_does_not_require_its_provider() -> None:
    registry = build_mock_provider_registry()
    workflow_config = _workflow_config(
        enabled_modules=("brief", "export"),
        disabled_modules=("voiceover",),
        provider_config={
            "LLMProvider": {"providerName": "mock", "enabled": True},
            "StorageProvider": {"providerName": "mock", "enabled": True},
        },
    )

    validate_provider_availability(
        workflow_config=workflow_config,
        plan=_plan(voiceover_enabled=False, export_enabled=True),
        provider_registry=registry,
    )


def test_valid_mock_provider_config_passes_before_run_start() -> None:
    registry = build_mock_provider_registry()
    workflow_config = _workflow_config(
        enabled_modules=("brief", "voiceover", "export"),
        provider_config={
            "LLMProvider": {"providerName": "mock", "enabled": True},
            "TTSProvider": {"providerName": "mock", "enabled": True},
            "StorageProvider": {"providerName": "mock", "enabled": True},
        },
    )

    validate_provider_availability(
        workflow_config=workflow_config,
        plan=_plan(voiceover_enabled=True, export_enabled=True),
        provider_registry=registry,
    )

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domain.base import DomainValidationError
from app.domain.enums import ContentGenre, ContentType, DurationProfile, TargetPlatform, WorkflowPreset
from app.domain.workflow_config import WorkflowConfig
from app.providers.mocks import build_mock_provider_registry
from app.workflow.engine import CoreWorkflowEngine
from app.workflow.execution import ModuleResult
from app.workflow.module import ModuleDefinition
from app.workflow.registry import ModuleRegistry


def _definition(name: str, *, disabled_behavior: str = "fail") -> ModuleDefinition:
    return ModuleDefinition(
        name=name,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        config_schema={"type": "object"},
        disabled_behavior=disabled_behavior,
        artifact_outputs=(f"{name}.json",),
    )


@dataclass(slots=True)
class RecordingModule:
    definition: ModuleDefinition
    order: list[str]

    def execute(self, context) -> ModuleResult:
        self.order.append(context.module_name)
        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=(f"{self.definition.name}.json",),
            output={"module": self.definition.name},
        )


def _build_engine_with_plan(module_names: tuple[str, ...], *, disabled_modules: tuple[str, ...] = ()) -> tuple[CoreWorkflowEngine, WorkflowConfig, list[str], object]:
    order: list[str] = []
    registry = ModuleRegistry(
        [
            _definition("brief"),
            _definition("voiceover", disabled_behavior="skip"),
            _definition("export"),
        ]
    )
    modules = {
        "brief": RecordingModule(definition=_definition("brief"), order=order),
        "voiceover": RecordingModule(definition=_definition("voiceover", disabled_behavior="skip"), order=order),
        "export": RecordingModule(definition=_definition("export"), order=order),
    }
    plan = registry.build_execution_plan(module_names, disabled_modules=disabled_modules)
    engine = CoreWorkflowEngine(registry, modules)
    config = WorkflowConfig.create(
        project_id="project_1",
        workflow_preset=WorkflowPreset.SHORT_VIDEO,
        content_type=ContentType.SHORT_VIDEO,
        content_genre=ContentGenre.NEWS,
        duration_profile=DurationProfile.SIXTY_SECONDS,
        target_platform=TargetPlatform.YOUTUBE_SHORTS,
        language="en",
        tone="dynamic",
    )
    return engine, config, order, plan


def test_missing_provider_for_enabled_module_fails_before_execution() -> None:
    engine, config, order, plan = _build_engine_with_plan(("brief",))
    provider_registry = build_mock_provider_registry()
    workflow_config = WorkflowConfig.create(
        project_id=config.project_id,
        workflow_preset=config.workflow_preset,
        content_type=config.content_type,
        content_genre=config.content_genre,
        duration_profile=config.duration_profile,
        target_platform=config.target_platform,
        language=config.language,
        tone=config.tone,
        provider_config={},
    )

    with pytest.raises(DomainValidationError, match="Missing provider LLMProvider"):
        engine.run(
            plan,
            workflow_run_id="workflow_run_1",
            workflow_config_id=workflow_config.id,
            workflow_config=workflow_config,
            provider_registry=provider_registry,
        )

    assert order == []


@pytest.mark.parametrize(
    "provider_config, expected_message",
    [
        ({"NotAProvider": {"providerName": "mock"}}, "Unknown provider type: NotAProvider."),
        ({"LLMProvider": {"providerName": "missing"}}, "Unknown provider: LLMProvider/missing."),
    ],
)
def test_provider_validation_rejects_invalid_provider_type_and_unknown_name(
    provider_config: dict[str, object],
    expected_message: str,
) -> None:
    engine, config, order, plan = _build_engine_with_plan(("brief",))
    provider_registry = build_mock_provider_registry()
    workflow_config = WorkflowConfig.create(
        project_id=config.project_id,
        workflow_preset=config.workflow_preset,
        content_type=config.content_type,
        content_genre=config.content_genre,
        duration_profile=config.duration_profile,
        target_platform=config.target_platform,
        language=config.language,
        tone=config.tone,
        provider_config=provider_config,
    )

    with pytest.raises(DomainValidationError, match=expected_message):
        engine.run(
            plan,
            workflow_run_id="workflow_run_2",
            workflow_config_id=workflow_config.id,
            workflow_config=workflow_config,
            provider_registry=provider_registry,
        )

    assert order == []


def test_disabled_optional_module_does_not_require_its_provider() -> None:
    engine, config, order, plan = _build_engine_with_plan(
        ("brief", "voiceover", "export"),
        disabled_modules=("voiceover",),
    )
    provider_registry = build_mock_provider_registry()
    workflow_config = WorkflowConfig.create(
        project_id=config.project_id,
        workflow_preset=config.workflow_preset,
        content_type=config.content_type,
        content_genre=config.content_genre,
        duration_profile=config.duration_profile,
        target_platform=config.target_platform,
        language=config.language,
        tone=config.tone,
        enabled_modules=["brief", "export"],
        disabled_modules=["voiceover"],
        provider_config={
            "LLMProvider": {"providerName": "mock", "enabled": True},
            "StorageProvider": {
                "providerName": "mock",
                "enabled": True,
            },
        },
    )

    result = engine.run(
        plan,
        workflow_run_id="workflow_run_3",
        workflow_config_id=workflow_config.id,
        workflow_config=workflow_config,
        provider_registry=provider_registry,
    )

    assert order == ["brief", "export"]
    assert result.status == "completed"
    assert result.module_results["voiceover"].status == "skipped"


def test_valid_mock_provider_config_passes_before_run_start() -> None:
    engine, config, order, plan = _build_engine_with_plan(
        ("brief", "voiceover", "export"),
        disabled_modules=("voiceover",),
    )
    provider_registry = build_mock_provider_registry()
    workflow_config = WorkflowConfig.create(
        project_id=config.project_id,
        workflow_preset=config.workflow_preset,
        content_type=config.content_type,
        content_genre=config.content_genre,
        duration_profile=config.duration_profile,
        target_platform=config.target_platform,
        language=config.language,
        tone=config.tone,
        enabled_modules=["brief", "export"],
        disabled_modules=["voiceover"],
        provider_config={
            "LLMProvider": {"providerName": "mock", "enabled": True},
            "StorageProvider": {"providerName": "mock", "enabled": True},
        },
    )

    result = engine.run(
        plan,
        workflow_run_id="workflow_run_4",
        workflow_config_id=workflow_config.id,
        workflow_config=workflow_config,
        provider_registry=provider_registry,
    )

    assert order == ["brief", "export"]
    assert result.status == "completed"
    assert result.artifact_ids == ("brief.json", "export.json")

from __future__ import annotations

from dataclasses import asdict

import pytest

from app.workflow.execution import (
    ModuleExecutionContext,
    ModuleExecutionPlan,
    ModuleExecutionStep,
    ModuleResult,
)
from app.workflow.module import ModuleDefinition
from app.workflow.registry import ModuleRegistry, ModuleRegistryError


def _module(
    name: str,
    *,
    dependencies: tuple[tuple[str, ...], ...] = (),
    disabled_behavior: str = "fail",
) -> ModuleDefinition:
    return ModuleDefinition(
        name=name,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        config_schema={"type": "object"},
        dependencies=dependencies,
        disabled_behavior=disabled_behavior,
        artifact_outputs=(f"{name}.json",),
    )


def test_module_definition_and_execution_types_capture_contract_state() -> None:
    definition = _module(
        "scriptGeneration",
        dependencies=(("outline",),),
    )
    result = ModuleResult(
        module_name="scriptGeneration",
        status="completed",
        output_artifact_ids=("artifact_1",),
        usage_metadata={"providerName": "mock", "inputTokens": 20},
        output={"script": "Draft script"},
    )
    context = ModuleExecutionContext(
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
        module_name="scriptGeneration",
        enabled_modules=("brief", "outline", "scriptGeneration"),
        disabled_modules=("thumbnail",),
        inputs={"topic": "AI workflows"},
        module_results={"scriptGeneration": result},
        artifact_ids=("artifact_1",),
        approval_checkpoint_ids=("approval_1",),
    )
    step = ModuleExecutionStep(
        module_name="scriptGeneration",
        dependency_groups=(("outline",),),
        enabled=True,
        status="pending",
        retry_limit=2,
        artifact_outputs=("script.txt",),
        resolved_dependencies=("outline",),
    )
    plan = ModuleExecutionPlan(steps=(step,))

    assert definition.name == "scriptGeneration"
    assert definition.dependencies == (("outline",),)
    assert definition.artifact_outputs == ("scriptGeneration.json",)
    assert definition.is_optional is False
    assert result.status == "completed"
    assert result.output_artifact_ids == ("artifact_1",)
    assert context.enabled_modules == ("brief", "outline", "scriptGeneration")
    assert context.disabled_modules == ("thumbnail",)
    assert context.module_results["scriptGeneration"] is result
    assert plan.module_names == ("scriptGeneration",)
    assert asdict(plan.steps[0])["resolved_dependencies"] == ("outline",)


def test_module_registry_validates_dependency_order_and_skips_optional_modules() -> None:
    registry = ModuleRegistry(
        [
            _module("brief"),
            _module("outline", dependencies=(("brief",),)),
            _module("scriptGeneration", dependencies=(("outline",),)),
            _module(
                "captions",
                dependencies=(("voiceover", "scriptGeneration"),),
                disabled_behavior="skip",
            ),
            _module("thumbnail", disabled_behavior="skip"),
        ]
    )

    plan = registry.build_execution_plan(
        ("brief", "outline", "scriptGeneration", "captions", "thumbnail"),
        disabled_modules=("thumbnail",),
    )

    assert plan.enabled_modules == ("brief", "outline", "scriptGeneration", "captions")
    assert plan.disabled_modules == ("thumbnail",)
    assert plan.step_for("outline").resolved_dependencies == ("brief",)
    assert plan.step_for("captions").resolved_dependencies == ("scriptGeneration",)
    assert plan.step_for("thumbnail").status == "skipped"
    assert plan.step_for("thumbnail").disabled_reason == "disabled"


def test_module_registry_rejects_unmet_dependencies_and_disabled_required_modules() -> None:
    registry = ModuleRegistry(
        [
            _module("brief"),
            _module("outline", dependencies=(("brief",),)),
        ]
    )

    with pytest.raises(ModuleRegistryError, match="Unsatisfied module dependency group"):
        registry.build_execution_plan(("outline", "brief"))

    with pytest.raises(ModuleRegistryError, match="Module brief cannot be disabled"):
        registry.build_execution_plan(("brief",), disabled_modules=("brief",))

from __future__ import annotations

from dataclasses import dataclass

from app.workflow.engine import CoreWorkflowEngine
from app.workflow.execution import ModuleExecutionPlan, ModuleExecutionStep, ModuleResult
from app.workflow.module import ModuleDefinition
from app.workflow.registry import ModuleRegistry


def _definition(
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


@dataclass(slots=True)
class RecordingModule:
    definition: ModuleDefinition
    order: list[str]
    expected_context_modules: tuple[str, ...]
    expected_disabled_modules: tuple[str, ...] = ()

    def execute(self, context) -> ModuleResult:
        self.order.append(context.module_name)
        assert tuple(context.module_results) == self.expected_context_modules
        assert context.disabled_modules == self.expected_disabled_modules
        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=(f"{self.definition.name}.json",),
            output={"module": self.definition.name},
        )


def test_workflow_engine_executes_modules_in_plan_order_and_skips_disabled_modules() -> None:
    order: list[str] = []
    registry = ModuleRegistry(
        [
            _definition("brief"),
            _definition("outline", dependencies=(("brief",),)),
            _definition("scriptGeneration", dependencies=(("outline",),)),
            _definition("thumbnail", disabled_behavior="skip"),
        ]
    )
    modules = {
        "brief": RecordingModule(
            definition=_definition("brief"),
            order=order,
            expected_context_modules=(),
            expected_disabled_modules=("thumbnail",),
        ),
        "outline": RecordingModule(
            definition=_definition("outline", dependencies=(("brief",),)),
            order=order,
            expected_context_modules=("brief",),
            expected_disabled_modules=("thumbnail",),
        ),
        "scriptGeneration": RecordingModule(
            definition=_definition("scriptGeneration", dependencies=(("outline",),)),
            order=order,
            expected_context_modules=("brief", "outline"),
            expected_disabled_modules=("thumbnail",),
        ),
    }
    plan = registry.build_execution_plan(
        ("brief", "outline", "scriptGeneration", "thumbnail"),
        disabled_modules=("thumbnail",),
    )
    engine = CoreWorkflowEngine(registry, modules)

    result = engine.run_plan(
        plan,
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
    )

    assert order == ["brief", "outline", "scriptGeneration"]
    assert result.status == "completed"
    assert result.completed_modules == ("brief", "outline", "scriptGeneration")
    assert result.failed_module is None
    assert result.module_results["thumbnail"].status == "skipped"
    assert result.module_results["thumbnail"].skipped_reason == "disabled"
    assert result.artifact_ids == ("brief.json", "outline.json", "scriptGeneration.json")


def test_workflow_engine_fails_cleanly_on_missing_dependency_before_execution() -> None:
    order: list[str] = []
    registry = ModuleRegistry(
        [
            _definition("outline"),
            _definition("scriptGeneration", dependencies=(("outline",),)),
        ]
    )
    plan = ModuleExecutionPlan(
        steps=(
            ModuleExecutionStep(
                module_name="scriptGeneration",
                dependency_groups=(("outline",),),
                enabled=True,
                status="pending",
                retry_limit=0,
                artifact_outputs=("scriptGeneration.json",),
                resolved_dependencies=("outline",),
            ),
        )
    )
    modules = {
        "scriptGeneration": RecordingModule(
            definition=_definition("scriptGeneration", dependencies=(("outline",),)),
            order=order,
            expected_context_modules=(),
        )
    }
    engine = CoreWorkflowEngine(registry, modules)

    result = engine.run_plan(
        plan,
        workflow_run_id="workflow_run_2",
        workflow_config_id="workflow_config_2",
    )

    assert order == []
    assert result.status == "failed"
    assert result.failed_module == "scriptGeneration"
    assert "requires completed dependency outline" in result.failure_message
    assert result.module_results["scriptGeneration"].status == "failed"

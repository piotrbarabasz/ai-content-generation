from __future__ import annotations

from dataclasses import dataclass

from app.workflow.engine import CoreWorkflowEngine
from app.workflow.execution import ModuleResult
from app.workflow.module import ModuleDefinition
from app.workflow.registry import ModuleRegistry


def _definition(
    name: str,
    *,
    dependencies: tuple[tuple[str, ...], ...] = (),
    disabled_behavior: str = "fail",
    retry_limit: int = 1,
) -> ModuleDefinition:
    return ModuleDefinition(
        name=name,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        config_schema={"type": "object"},
        dependencies=dependencies,
        disabled_behavior=disabled_behavior,
        retry_limit=retry_limit,
        artifact_outputs=(f"{name}.json",),
    )


@dataclass(slots=True)
class AlwaysFailingModule:
    definition: ModuleDefinition
    attempts: int = 0

    def execute(self, context) -> ModuleResult:
        self.attempts += 1
        return ModuleResult(
            module_name=self.definition.name,
            status="failed",
            error_message="provider unavailable",
            output={"attempts": self.attempts, "workflowRunId": context.workflow_run_id},
        )


@dataclass(slots=True)
class RecordingModule:
    definition: ModuleDefinition
    attempts: int = 0

    def execute(self, context) -> ModuleResult:
        self.attempts += 1
        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=(f"{self.definition.name}.json",),
            output={"workflowRunId": context.workflow_run_id},
        )


def test_core_workflow_engine_records_required_module_failure_after_retries_are_exhausted() -> None:
    brief_definition = _definition("brief", retry_limit=1)
    outline_definition = _definition("outline", dependencies=(("brief",),))
    registry = ModuleRegistry([brief_definition, outline_definition])
    failing_module = AlwaysFailingModule(definition=brief_definition)
    outline_module = RecordingModule(definition=outline_definition)
    plan = registry.build_execution_plan(("brief", "outline"))
    engine = CoreWorkflowEngine(registry, {"brief": failing_module, "outline": outline_module})

    result = engine.run_plan(
        plan,
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
    )

    assert failing_module.attempts == 2
    assert outline_module.attempts == 0
    assert result.status == "failed"
    assert result.failed_module == "brief"
    assert result.failure_message == "provider unavailable"
    assert result.module_results["brief"].status == "failed"
    assert "outline" not in result.module_results


def test_optional_module_can_be_skipped_without_blocking_following_steps() -> None:
    brief_definition = _definition("brief")
    thumbnail_definition = _definition("thumbnail", disabled_behavior="skip")
    export_definition = _definition("export", dependencies=(("brief",),))
    registry = ModuleRegistry([brief_definition, thumbnail_definition, export_definition])
    brief_module = RecordingModule(definition=brief_definition)
    export_module = RecordingModule(definition=export_definition)
    plan = registry.build_execution_plan(("brief", "thumbnail", "export"), disabled_modules=("thumbnail",))
    engine = CoreWorkflowEngine(
        registry,
        {"brief": brief_module, "thumbnail": RecordingModule(definition=thumbnail_definition), "export": export_module},
    )

    result = engine.run_plan(
        plan,
        workflow_run_id="workflow_run_2",
        workflow_config_id="workflow_config_2",
    )

    assert brief_module.attempts == 1
    assert export_module.attempts == 1
    assert result.status == "completed"
    assert result.completed_modules == ("brief", "export")
    assert result.module_results["thumbnail"].status == "skipped"
    assert result.module_results["thumbnail"].skipped_reason == "disabled"

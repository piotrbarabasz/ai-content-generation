from __future__ import annotations

from dataclasses import dataclass

from app.workflow.engine import CoreWorkflowEngine
from app.workflow.execution import ModuleResult
from app.workflow.module import ModuleDefinition
from app.workflow.registry import ModuleRegistry


def _definition(name: str, *, retry_limit: int = 1) -> ModuleDefinition:
    return ModuleDefinition(
        name=name,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        config_schema={"type": "object"},
        retry_limit=retry_limit,
        artifact_outputs=(f"{name}.json",),
    )


@dataclass(slots=True)
class FlakyModule:
    definition: ModuleDefinition
    failures_before_success: int
    attempts: int = 0

    def execute(self, context) -> ModuleResult:
        self.attempts += 1
        if self.attempts <= self.failures_before_success:
            return ModuleResult(
                module_name=self.definition.name,
                status="failed",
                error_message="transient provider error",
            )

        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=(f"{self.definition.name}.json",),
            output={"attempts": self.attempts, "workflowRunId": context.workflow_run_id},
        )


def test_core_workflow_engine_retries_transient_failures_until_success() -> None:
    registry = ModuleRegistry([_definition("brief", retry_limit=1)])
    module = FlakyModule(definition=_definition("brief", retry_limit=1), failures_before_success=1)
    plan = registry.build_execution_plan(("brief",))
    engine = CoreWorkflowEngine(registry, {"brief": module})

    result = engine.run_plan(
        plan,
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
    )

    assert module.attempts == 2
    assert result.status == "completed"
    assert result.failed_module is None
    assert result.failure_message == ""
    assert result.completed_modules == ("brief",)
    assert result.module_results["brief"].status == "completed"
    assert result.module_results["brief"].output_artifact_ids == ("brief.json",)
    assert result.module_results["brief"].output["attempts"] == 2

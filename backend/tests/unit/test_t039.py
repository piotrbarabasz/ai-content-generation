from __future__ import annotations

from dataclasses import dataclass, field

from app.workflow.engine import CoreWorkflowEngine
from app.workflow.execution import ModuleResult
from app.workflow.module import ModuleDefinition
from app.workflow.registry import ModuleRegistry
from app.workflow.usage import NoopCostTracker, UsageTracker


def _module(name: str) -> ModuleDefinition:
    return ModuleDefinition(
        name=name,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        config_schema={"type": "object"},
        artifact_outputs=(f"{name}.json",),
    )


@dataclass(slots=True)
class RecordingModule:
    definition: ModuleDefinition
    usage_metadata: dict[str, object] | None = None

    def execute(self, context) -> ModuleResult:
        payload: dict[str, object] = {
            "module": self.definition.name,
            "workflowRunId": context.workflow_run_id,
        }
        kwargs = {
            "module_name": self.definition.name,
            "status": "completed",
            "output_artifact_ids": (f"{self.definition.name}.json",),
            "output": payload,
        }
        if self.usage_metadata is not None:
            kwargs["usage_metadata"] = self.usage_metadata
        return ModuleResult(**kwargs)


@dataclass(slots=True)
class RecordingUsageTracker:
    records: list[tuple[str, str, dict[str, object] | None]] = field(default_factory=list)

    def record(
        self,
        *,
        workflow_run_id: str,
        module_name: str,
        usage_metadata: dict[str, object] | None = None,
    ) -> None:
        self.records.append((workflow_run_id, module_name, usage_metadata))


def test_usage_tracker_records_optional_metadata() -> None:
    registry = ModuleRegistry([_module("brief")])
    tracker = RecordingUsageTracker()
    engine = CoreWorkflowEngine(
        registry,
        {"brief": RecordingModule(definition=_module("brief"), usage_metadata={"providerName": "mock", "inputTokens": 12})},
        usage_tracker=tracker,
    )
    plan = registry.build_execution_plan(("brief",))

    result = engine.run_plan(
        plan,
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
    )

    assert result.status == "completed"
    assert tracker.records == [
        ("workflow_run_1", "brief", {"providerName": "mock", "inputTokens": 12}),
    ]


def test_noop_cost_tracker_ignores_missing_usage_metadata() -> None:
    registry = ModuleRegistry([_module("brief")])
    engine = CoreWorkflowEngine(
        registry,
        {"brief": RecordingModule(definition=_module("brief"))},
        usage_tracker=NoopCostTracker(),
    )
    plan = registry.build_execution_plan(("brief",))

    assert isinstance(NoopCostTracker(), UsageTracker)

    result = engine.run_plan(
        plan,
        workflow_run_id="workflow_run_2",
        workflow_config_id="workflow_config_2",
    )

    assert result.status == "completed"
    assert result.module_results["brief"].usage_metadata == {}

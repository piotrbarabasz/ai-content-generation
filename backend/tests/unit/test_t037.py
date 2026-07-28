from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.approval import ApprovalCheckpoint
from app.workflow.engine import CoreWorkflowEngine
from app.workflow.execution import ModuleExecutionPlan, ModuleExecutionStep, ModuleResult
from app.workflow.module import ModuleDefinition
from app.workflow.registry import ModuleRegistry


def _definition(name: str, *, dependencies: tuple[tuple[str, ...], ...] = ()) -> ModuleDefinition:
    return ModuleDefinition(
        name=name,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        config_schema={"type": "object"},
        dependencies=dependencies,
        artifact_outputs=(f"{name}.json",),
    )


def _approval_checkpoint_payload(checkpoint: ApprovalCheckpoint) -> dict[str, object]:
    return {
        "id": checkpoint.id,
        "workflow_run_id": checkpoint.workflow_run_id,
        "checkpoint_type": checkpoint.checkpoint_type,
        "artifact_id": checkpoint.artifact_id,
        "status": checkpoint.status,
        "required": checkpoint.required,
        "resolved_at": checkpoint.resolved_at.isoformat() if checkpoint.resolved_at else None,
        "decision_history": [],
        "created_at": checkpoint.created_at.isoformat(),
    }


def _waiting_result(module_name: str, checkpoint: ApprovalCheckpoint) -> ModuleResult:
    return ModuleResult(
        module_name=module_name,
        status="waiting_for_approval",
        output_artifact_ids=(f"{module_name}.json",),
        output={
            "approval_checkpoint": _approval_checkpoint_payload(checkpoint),
        },
    )


@dataclass(slots=True)
class RecordingModule:
    definition: ModuleDefinition
    order: list[str]
    expected_approval_checkpoint_ids: tuple[str, ...] = ()

    def execute(self, context) -> ModuleResult:
        self.order.append(context.module_name)
        assert context.approval_checkpoint_ids == self.expected_approval_checkpoint_ids
        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=(f"{self.definition.name}.json",),
            output={"module": self.definition.name},
        )


def test_workflow_engine_pauses_on_pending_approval_and_records_checkpoint_id() -> None:
    order: list[str] = []
    checkpoint = ApprovalCheckpoint.create(
        workflow_run_id="workflow_run_1",
        checkpoint_type="script",
        artifact_id="script.json",
        required=True,
    )
    registry = ModuleRegistry([_definition("scriptGeneration"), _definition("export", dependencies=(("scriptGeneration",),))])
    engine = CoreWorkflowEngine(
        registry,
        modules={
            "scriptGeneration": RecordingModule(definition=_definition("scriptGeneration"), order=order),
            "export": RecordingModule(definition=_definition("export", dependencies=(("scriptGeneration",),)), order=order),
        },
    )
    plan = ModuleExecutionPlan(
        steps=(
            ModuleExecutionStep(
                module_name="scriptGeneration",
                dependency_groups=(),
                enabled=True,
                status="pending",
                retry_limit=0,
                artifact_outputs=("scriptGeneration.json",),
            ),
            ModuleExecutionStep(
                module_name="export",
                dependency_groups=(("scriptGeneration",),),
                enabled=True,
                status="pending",
                retry_limit=0,
                artifact_outputs=("export.json",),
                resolved_dependencies=("scriptGeneration",),
            ),
        )
    )

    result = engine.run_plan(
        plan,
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
        seed_module_results={"scriptGeneration": _waiting_result("scriptGeneration", checkpoint)},
    )

    assert order == []
    assert result.status == "waiting_for_approval"
    assert result.completed_modules == ()
    assert result.module_results["scriptGeneration"].status == "waiting_for_approval"
    assert result.approval_checkpoint_ids == (checkpoint.id,)
    assert "export" not in result.module_results


def test_workflow_engine_resumes_after_checkpoint_is_approved() -> None:
    order: list[str] = []
    checkpoint = ApprovalCheckpoint.create(
        workflow_run_id="workflow_run_2",
        checkpoint_type="script",
        artifact_id="script.json",
        required=True,
        status="approved",
        resolved_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
    )
    registry = ModuleRegistry([_definition("scriptGeneration"), _definition("export", dependencies=(("scriptGeneration",),))])
    engine = CoreWorkflowEngine(
        registry,
        modules={
            "scriptGeneration": RecordingModule(definition=_definition("scriptGeneration"), order=order),
            "export": RecordingModule(
                definition=_definition("export", dependencies=(("scriptGeneration",),)),
                order=order,
                expected_approval_checkpoint_ids=(checkpoint.id,),
            ),
        },
    )
    plan = ModuleExecutionPlan(
        steps=(
            ModuleExecutionStep(
                module_name="scriptGeneration",
                dependency_groups=(),
                enabled=True,
                status="pending",
                retry_limit=0,
                artifact_outputs=("scriptGeneration.json",),
            ),
            ModuleExecutionStep(
                module_name="export",
                dependency_groups=(("scriptGeneration",),),
                enabled=True,
                status="pending",
                retry_limit=0,
                artifact_outputs=("export.json",),
                resolved_dependencies=("scriptGeneration",),
            ),
        )
    )

    result = engine.run_plan(
        plan,
        workflow_run_id="workflow_run_2",
        workflow_config_id="workflow_config_2",
        seed_module_results={"scriptGeneration": _waiting_result("scriptGeneration", checkpoint)},
        approval_checkpoints={checkpoint.id: checkpoint},
    )

    assert order == ["export"]
    assert result.status == "completed"
    assert result.completed_modules == ("scriptGeneration", "export")
    assert result.module_results["scriptGeneration"].status == "completed"
    assert result.module_results["scriptGeneration"].output["approval_checkpoint"]["status"] == "approved"
    assert result.approval_checkpoint_ids == (checkpoint.id,)


def test_workflow_engine_keeps_run_paused_when_checkpoint_is_rejected_or_changes_requested() -> None:
    for status in ("rejected", "changes_requested"):
        order: list[str] = []
        checkpoint = ApprovalCheckpoint.create(
            workflow_run_id=f"workflow_run_{status}",
            checkpoint_type="script",
            artifact_id="script.json",
            required=True,
            status=status,
            resolved_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC) if status == "rejected" else None,
        )
        registry = ModuleRegistry([_definition("scriptGeneration"), _definition("export", dependencies=(("scriptGeneration",),))])
        engine = CoreWorkflowEngine(
            registry,
            modules={
                "scriptGeneration": RecordingModule(definition=_definition("scriptGeneration"), order=order),
                "export": RecordingModule(definition=_definition("export", dependencies=(("scriptGeneration",),)), order=order),
            },
        )
        plan = ModuleExecutionPlan(
            steps=(
                ModuleExecutionStep(
                    module_name="scriptGeneration",
                    dependency_groups=(),
                    enabled=True,
                    status="pending",
                    retry_limit=0,
                    artifact_outputs=("scriptGeneration.json",),
                ),
                ModuleExecutionStep(
                    module_name="export",
                    dependency_groups=(("scriptGeneration",),),
                    enabled=True,
                    status="pending",
                    retry_limit=0,
                    artifact_outputs=("export.json",),
                    resolved_dependencies=("scriptGeneration",),
                ),
            )
        )

        result = engine.run_plan(
            plan,
            workflow_run_id=f"workflow_run_{status}",
            workflow_config_id="workflow_config_3",
            seed_module_results={"scriptGeneration": _waiting_result("scriptGeneration", checkpoint)},
            approval_checkpoints={checkpoint.id: checkpoint},
        )

        assert order == []
        assert result.status == "waiting_for_approval"
        assert result.module_results["scriptGeneration"].status == "waiting_for_approval"
        assert result.approval_checkpoint_ids == (checkpoint.id,)
        assert "export" not in result.module_results

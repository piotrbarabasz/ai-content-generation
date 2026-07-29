from __future__ import annotations

from dataclasses import dataclass

import pytest

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


def _waiting_result(module_name: str, checkpoint: ApprovalCheckpoint) -> ModuleResult:
    return ModuleResult(
        module_name=module_name,
        status="waiting_for_approval",
        output_artifact_ids=(f"{module_name}.json",),
        output={
            "artifact": {
                "artifact_id": checkpoint.artifact_id,
                "checkpoint_type": checkpoint.checkpoint_type,
                "status": checkpoint.status,
            },
            "approval_checkpoint": {
                "id": checkpoint.id,
                "workflow_run_id": checkpoint.workflow_run_id,
                "checkpoint_type": checkpoint.checkpoint_type,
                "artifact_id": checkpoint.artifact_id,
                "status": checkpoint.status,
                "required": checkpoint.required,
                "resolved_at": checkpoint.resolved_at.isoformat() if checkpoint.resolved_at else None,
                "decision_history": [],
                "created_at": checkpoint.created_at.isoformat(),
            },
        },
    )


def _plan() -> ModuleExecutionPlan:
    return ModuleExecutionPlan(
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


def test_script_approval_pauses_then_resumes_workflow_and_preserves_artifact_id() -> None:
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
            "export": RecordingModule(
                definition=_definition("export", dependencies=(("scriptGeneration",),)),
                order=order,
                expected_approval_checkpoint_ids=(checkpoint.id,),
            ),
        },
    )

    paused = engine.run_plan(
        _plan(),
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
        seed_module_results={"scriptGeneration": _waiting_result("scriptGeneration", checkpoint)},
    )

    assert order == []
    assert paused.status == "waiting_for_approval"
    assert paused.module_results["scriptGeneration"].status == "waiting_for_approval"
    assert paused.module_results["scriptGeneration"].output["approval_checkpoint"]["artifact_id"] == "script.json"
    assert paused.approval_checkpoint_ids == (checkpoint.id,)

    checkpoint.approve(reviewer_id="reviewer_1", comment="Approved for export.")

    resumed = engine.run_plan(
        _plan(),
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
        seed_module_results={"scriptGeneration": _waiting_result("scriptGeneration", checkpoint)},
        approval_checkpoints={checkpoint.id: checkpoint},
    )

    assert order == ["export"]
    assert resumed.status == "completed"
    assert resumed.completed_modules == ("scriptGeneration", "export")
    assert resumed.module_results["scriptGeneration"].output["approval_checkpoint"]["status"] == "approved"
    assert resumed.module_results["scriptGeneration"].output["approval_checkpoint"]["artifact_id"] == "script.json"
    assert resumed.approval_checkpoint_ids == (checkpoint.id,)


@pytest.mark.parametrize(
    ("decision", "expected_status", "comment", "revised_artifact_id"),
    [
        ("reject", "rejected", "The scene plan needs revision.", None),
        ("request_changes", "changes_requested", "Please revise the opening scene.", "scene_plan_v2.json"),
    ],
)
def test_rejection_and_request_changes_keep_workflow_paused_and_record_decisions(
    decision: str,
    expected_status: str,
    comment: str,
    revised_artifact_id: str | None,
) -> None:
    order: list[str] = []
    checkpoint = ApprovalCheckpoint.create(
        workflow_run_id="workflow_run_2",
        checkpoint_type="scene_plan",
        artifact_id="scene_plan.json",
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

    if decision == "reject":
        checkpoint.reject(reviewer_id="reviewer_2", comment=comment)
    else:
        checkpoint.request_changes(
            reviewer_id="reviewer_2",
            comment=comment,
            revised_artifact_id=revised_artifact_id,
        )

    paused = engine.run_plan(
        _plan(),
        workflow_run_id="workflow_run_2",
        workflow_config_id="workflow_config_2",
        seed_module_results={"scriptGeneration": _waiting_result("scriptGeneration", checkpoint)},
        approval_checkpoints={checkpoint.id: checkpoint},
    )

    assert order == []
    assert paused.status == "waiting_for_approval"
    assert paused.module_results["scriptGeneration"].status == "waiting_for_approval"
    assert paused.module_results["scriptGeneration"].output["approval_checkpoint"]["artifact_id"] == "scene_plan.json"
    assert paused.module_results["scriptGeneration"].output["approval_checkpoint"]["status"] == expected_status
    assert checkpoint.artifact_id == "scene_plan.json"
    assert checkpoint.latest_decision is not None
    assert checkpoint.latest_decision.decision == decision
    assert checkpoint.latest_decision.comment == comment
    assert checkpoint.latest_decision.revised_artifact_id == revised_artifact_id


def test_final_export_approval_is_required_before_workflow_completion() -> None:
    checkpoint = ApprovalCheckpoint.create(
        workflow_run_id="workflow_run_3",
        checkpoint_type="export",
        artifact_id="manifest.json",
        required=True,
    )
    registry = ModuleRegistry([_definition("export")])
    engine = CoreWorkflowEngine(registry)

    paused = engine.run_plan(
        ModuleExecutionPlan(
            steps=(
                ModuleExecutionStep(
                    module_name="export",
                    dependency_groups=(),
                    enabled=True,
                    status="pending",
                    retry_limit=0,
                    artifact_outputs=("export.json",),
                ),
            )
        ),
        workflow_run_id="workflow_run_3",
        workflow_config_id="workflow_config_3",
        seed_module_results={"export": _waiting_result("export", checkpoint)},
    )

    assert paused.status == "waiting_for_approval"
    assert paused.module_results["export"].output["approval_checkpoint"]["checkpoint_type"] == "export"
    assert paused.module_results["export"].output["approval_checkpoint"]["status"] == "pending"

    checkpoint.approve(
        reviewer_id="reviewer_3",
        comment="Final export approved.",
        revised_artifact_id=None,
    )

    resumed = engine.run_plan(
        ModuleExecutionPlan(
            steps=(
                ModuleExecutionStep(
                    module_name="export",
                    dependency_groups=(),
                    enabled=True,
                    status="pending",
                    retry_limit=0,
                    artifact_outputs=("export.json",),
                ),
            )
        ),
        workflow_run_id="workflow_run_3",
        workflow_config_id="workflow_config_3",
        seed_module_results={"export": _waiting_result("export", checkpoint)},
        approval_checkpoints={checkpoint.id: checkpoint},
    )

    assert resumed.status == "completed"
    assert resumed.completed_modules == ("export",)
    assert resumed.module_results["export"].output["approval_checkpoint"]["status"] == "approved"
    assert resumed.approval_checkpoint_ids == (checkpoint.id,)

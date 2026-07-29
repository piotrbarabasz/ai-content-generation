from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.main import create_app
from app.api.routes.approvals import (
    ApprovalDecisionRequest,
    approve_workflow_run_approval,
    list_workflow_run_approvals,
    register_approval_checkpoint,
    reject_workflow_run_approval,
    request_changes_for_workflow_run_approval,
    reset_approval_state,
)
from app.api.routes.projects import create_project, get_workflow_run_or_404, reset_api_state
from app.api.routes.workflow_configs import create_workflow_config
from app.api.routes.workflow_runs import create_workflow_run, resume_workflow_run
from app.api.schemas import ProjectCreateRequest, WorkflowConfigCreateRequest, WorkflowRunCreateRequest
from app.domain.approval import ApprovalCheckpoint
from app.workflow.registry import build_mvp_workflow_preset_registry


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_api_state()
    reset_approval_state()
    yield
    reset_api_state()
    reset_approval_state()


def _create_workflow_run():
    preset_registry = build_mvp_workflow_preset_registry()
    preset = preset_registry.get("short_video")
    project = create_project(
        ProjectCreateRequest.model_validate(
            {
                "workspaceId": "workspace_1",
                "name": "Approval API Project",
                "contentType": "short_video",
                "contentGenre": "news",
                "targetPlatform": "youtube_shorts",
                "language": "en",
                "tone": "neutral",
            }
        )
    )
    workflow_config = create_workflow_config(
        project.id,
        WorkflowConfigCreateRequest.model_validate(
            preset.build_workflow_config_payload(project_id=project.id)
        ),
    )
    workflow_run = create_workflow_run(
        WorkflowRunCreateRequest.model_validate({"workflowConfigId": workflow_config.id})
    )
    return get_workflow_run_or_404(workflow_run.id)


def test_approval_api_records_request_changes_and_preserves_rejected_artifacts() -> None:
    workflow_run = _create_workflow_run()
    workflow_run.status = "waiting_for_approval"
    workflow_run.current_stage = "script_approval"

    script_checkpoint = register_approval_checkpoint(
        ApprovalCheckpoint.create(
            workflow_run_id=workflow_run.id,
            checkpoint_type="script",
            artifact_id="script.json",
        )
    )
    export_checkpoint = register_approval_checkpoint(
        ApprovalCheckpoint.create(
            workflow_run_id=workflow_run.id,
            checkpoint_type="export",
            artifact_id="manifest.json",
        )
    )

    approvals = list_workflow_run_approvals(workflow_run.id)
    assert [approval.checkpoint_type for approval in approvals] == ["script", "export"]
    assert approvals[0].status == "pending"

    changes_requested = request_changes_for_workflow_run_approval(
        workflow_run.id,
        script_checkpoint.id,
        ApprovalDecisionRequest.model_validate(
            {
                "reviewerId": "reviewer_1",
                "comment": "Revise the opening beat.",
                "revisedArtifactId": "script_v2.json",
            }
        ),
    )
    rejected = reject_workflow_run_approval(
        workflow_run.id,
        export_checkpoint.id,
        ApprovalDecisionRequest.model_validate(
            {"reviewerId": "reviewer_2", "comment": "The manifest is incomplete."}
        ),
    )

    assert changes_requested.status == "changes_requested"
    assert changes_requested.decision_history[0].decision == "request_changes"
    assert changes_requested.decision_history[0].revised_artifact_id == "script_v2.json"
    assert rejected.status == "rejected"
    assert rejected.artifact_id == "manifest.json"
    assert rejected.decision_history[0].comment == "The manifest is incomplete."
    assert rejected.decision_history[0].revised_artifact_id is None


def test_approval_api_blocks_resume_until_export_checkpoint_is_approved() -> None:
    workflow_run = _create_workflow_run()
    workflow_run.status = "waiting_for_approval"
    workflow_run.current_stage = "export_approval"

    export_checkpoint = register_approval_checkpoint(
        ApprovalCheckpoint.create(
            workflow_run_id=workflow_run.id,
            checkpoint_type="export",
            artifact_id="manifest.json",
        )
    )

    paths = set(create_app().openapi()["paths"])
    assert "/api/v1/workflow-runs/{workflow_run_id}/approvals" in paths
    assert "/api/v1/workflow-runs/{workflow_run_id}/resume" in paths

    with pytest.raises(HTTPException) as exc_info:
        resume_workflow_run(workflow_run.id)

    assert exc_info.value.status_code == 409
    assert "unresolved approval checkpoints: export" in str(exc_info.value.detail)

    approved = approve_workflow_run_approval(
        workflow_run.id,
        export_checkpoint.id,
        ApprovalDecisionRequest.model_validate(
            {"reviewerId": "reviewer_3", "comment": "Approved for final export."}
        ),
    )
    resumed = resume_workflow_run(workflow_run.id)

    assert approved.status == "approved"
    assert approved.decision_history[0].decision == "approve"
    assert resumed.status == "running"
    assert resumed.current_stage == "export_approval"

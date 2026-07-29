from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.main import create_app
from app.api.routes.approvals import (
    ApprovalDecisionRequest,
    APPROVAL_CHECKPOINTS_BY_RUN,
    approve_workflow_run_approval,
    list_workflow_run_approvals,
    register_approval_checkpoint,
    reject_workflow_run_approval,
    request_changes_for_workflow_run_approval,
    reset_approval_state,
)
from app.api.routes.projects import get_workflow_run_or_404, reset_api_state
from app.api.routes.workflow_configs import create_workflow_config
from app.api.routes.workflow_runs import create_workflow_run, resume_workflow_run
from app.api.routes.projects import create_project
from app.api.schemas import (
    ProjectCreateRequest,
    WorkflowConfigCreateRequest,
    WorkflowRunCreateRequest,
)
from app.domain.approval import ApprovalCheckpoint


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_api_state()
    reset_approval_state()
    yield
    reset_api_state()
    reset_approval_state()


def _create_workflow_run():
    project = create_project(
        ProjectCreateRequest.model_validate(
            {
                "workspaceId": "workspace_1",
                "name": "Approval Routes",
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
            {
                "projectId": project.id,
                "workflowPreset": "short_video",
                "contentType": "short_video",
                "contentGenre": "news",
                "durationProfile": "60s",
                "targetPlatform": "youtube_shorts",
                "language": "en",
                "tone": "neutral",
                "enabledModules": ["brief", "export"],
                "disabledModules": [],
                "providerConfig": {
                    "LLMProvider": {"providerName": "mock", "enabled": True},
                    "StorageProvider": {"providerName": "mock", "enabled": True},
                },
            }
        ),
    )
    workflow_run = create_workflow_run(
        WorkflowRunCreateRequest.model_validate({"workflowConfigId": workflow_config.id})
    )
    stored_run = get_workflow_run_or_404(workflow_run.id)
    stored_run.status = "waiting_for_approval"
    stored_run.current_stage = "script_approval"
    return stored_run


def test_approval_routes_are_registered_on_the_application() -> None:
    api_app = create_app()
    paths = set(api_app.openapi()["paths"])

    assert "/api/v1/workflow-runs/{workflow_run_id}/approvals" in paths
    assert "/api/v1/workflow-runs/{workflow_run_id}/approvals/{checkpoint_id}/approve" in paths
    assert "/api/v1/workflow-runs/{workflow_run_id}/approvals/{checkpoint_id}/reject" in paths
    assert "/api/v1/workflow-runs/{workflow_run_id}/approvals/{checkpoint_id}/request-changes" in paths
    assert "/api/v1/workflow-runs/{workflow_run_id}/resume" in paths


def test_approval_routes_list_checkpoints_and_record_decisions() -> None:
    workflow_run = _create_workflow_run()
    script_checkpoint = register_approval_checkpoint(
        ApprovalCheckpoint.create(
            workflow_run_id=workflow_run.id,
            checkpoint_type="script",
            artifact_id="script.json",
        )
    )
    scene_checkpoint = register_approval_checkpoint(
        ApprovalCheckpoint.create(
            workflow_run_id=workflow_run.id,
            checkpoint_type="scene_plan",
            artifact_id="scene_plan.json",
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
    assert [approval.checkpoint_type for approval in approvals] == [
        "script",
        "scene_plan",
        "export",
    ]
    assert APPROVAL_CHECKPOINTS_BY_RUN[workflow_run.id][script_checkpoint.id] is script_checkpoint

    approved = approve_workflow_run_approval(
        workflow_run.id,
        script_checkpoint.id,
        ApprovalDecisionRequest.model_validate(
            {"reviewerId": "reviewer_1", "comment": "Good to go."}
        ),
    )
    rejected = reject_workflow_run_approval(
        workflow_run.id,
        scene_checkpoint.id,
        ApprovalDecisionRequest.model_validate(
            {"reviewerId": "reviewer_1", "comment": "Needs a different structure."}
        ),
    )
    changes_requested = request_changes_for_workflow_run_approval(
        workflow_run.id,
        export_checkpoint.id,
        ApprovalDecisionRequest.model_validate(
            {
                "reviewerId": "reviewer_2",
                "comment": "Revise the export metadata.",
                "revisedArtifactId": "manifest_v2.json",
            }
        ),
    )

    assert approved.status == "approved"
    assert approved.decision_history[0].decision == "approve"
    assert rejected.status == "rejected"
    assert rejected.decision_history[0].comment == "Needs a different structure."
    assert changes_requested.status == "changes_requested"
    assert changes_requested.decision_history[0].revised_artifact_id == "manifest_v2.json"


def test_resume_route_blocks_unresolved_checkpoints_and_allows_resumed_runs_after_approval() -> None:
    workflow_run = _create_workflow_run()
    checkpoint = register_approval_checkpoint(
        ApprovalCheckpoint.create(
            workflow_run_id=workflow_run.id,
            checkpoint_type="script",
            artifact_id="script.json",
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        resume_workflow_run(workflow_run.id)

    assert exc_info.value.status_code == 409
    assert "unresolved approval checkpoints" in str(exc_info.value.detail)

    approve_workflow_run_approval(
        workflow_run.id,
        checkpoint.id,
        ApprovalDecisionRequest.model_validate(
            {"reviewerId": "reviewer_1", "comment": "Approved."}
        ),
    )

    resumed = resume_workflow_run(workflow_run.id)

    assert resumed.status == "running"
    assert resumed.current_stage == "script_approval"

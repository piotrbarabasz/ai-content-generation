from __future__ import annotations

import pytest

from app.api.main import create_app
from app.api.routes.approvals import (
    ApprovalDecisionRequest,
    approve_workflow_run_approval,
    list_workflow_run_approvals,
    register_approval_checkpoint,
    reset_approval_state,
)
from app.api.routes.artifacts import list_artifacts
from app.api.routes.projects import (
    create_project,
    get_project,
    get_workflow_run_or_404,
    reset_api_state,
)
from app.api.routes.workflow_configs import create_workflow_config
from app.api.routes.workflow_runs import create_workflow_run, resume_workflow_run
from app.api.schemas import (
    ProjectCreateRequest,
    WorkflowConfigCreateRequest,
    WorkflowRunCreateRequest,
)
from app.domain.approval import ApprovalCheckpoint
from app.workflow.registry import build_mvp_workflow_preset_registry


@pytest.fixture(autouse=True)
def _reset_api_state() -> None:
    reset_api_state()
    reset_approval_state()
    yield
    reset_api_state()
    reset_approval_state()


def test_api_smoke_path_drives_project_config_run_approval_and_resume_lifecycle() -> None:
    api_app = create_app()
    preset_registry = build_mvp_workflow_preset_registry()
    short_video_preset = preset_registry.get("short_video")

    paths = set(api_app.openapi()["paths"])
    assert "/api/v1/projects" in paths
    assert "/api/v1/projects/{project_id}/workflow-configs" in paths
    assert "/api/v1/workflow-runs" in paths
    assert "/api/v1/workflow-runs/{workflow_run_id}/approvals" in paths
    assert "/api/v1/workflow-runs/{workflow_run_id}/resume" in paths

    project = create_project(
        ProjectCreateRequest.model_validate(
            {
                "workspaceId": "workspace_1",
                "name": "Smoke Test Project",
                "contentType": "short_video",
                "contentGenre": "news",
                "targetPlatform": "youtube_shorts",
                "language": "en",
                "tone": "neutral",
            }
        )
    )
    assert get_project(project.id) == project

    workflow_config = create_workflow_config(
        project.id,
        WorkflowConfigCreateRequest.model_validate(
            short_video_preset.build_workflow_config_payload(project_id=project.id)
        ),
    )
    assert workflow_config.project_id == project.id

    workflow_run = create_workflow_run(
        WorkflowRunCreateRequest.model_validate({"workflowConfigId": workflow_config.id})
    )
    workflow_run_id = workflow_run.id

    assert workflow_run.status == "running"
    assert workflow_run.current_stage == "started"
    assert list_artifacts(workflow_run_id) == []

    stored_run = get_workflow_run_or_404(workflow_run_id)
    stored_run.status = "waiting_for_approval"
    stored_run.current_stage = "script_approval"

    approval_checkpoint = register_approval_checkpoint(
        ApprovalCheckpoint.create(
            workflow_run_id=workflow_run_id,
            checkpoint_type="script",
            artifact_id="script.json",
        )
    )

    approvals = list_workflow_run_approvals(workflow_run_id)
    assert [approval.checkpoint_type for approval in approvals] == ["script"]
    assert approvals[0].status == "pending"

    approved = approve_workflow_run_approval(
        workflow_run_id,
        approval_checkpoint.id,
        ApprovalDecisionRequest.model_validate(
            {"reviewerId": "reviewer_1", "comment": "Approved."}
        ),
    )
    assert approved.status == "approved"

    resumed = resume_workflow_run(workflow_run_id)
    assert resumed.status == "running"
    assert resumed.current_stage == "script_approval"

"""Workflow configuration endpoints for the AI Content Studio API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.schemas import WorkflowConfigCreateRequest, WorkflowConfigSchema

from .projects import PROJECTS, WORKFLOW_CONFIGS, get_project_or_404, register_api_router

router = APIRouter(tags=["workflow-configs"])


def _workflow_config_schema(workflow_config):
    return WorkflowConfigSchema.model_validate(workflow_config)


@router.post(
    "/projects/{project_id}/workflow-configs",
    response_model=WorkflowConfigSchema,
    status_code=status.HTTP_201_CREATED,
)
def create_workflow_config(project_id: str, request: WorkflowConfigCreateRequest) -> WorkflowConfigSchema:
    project = get_project_or_404(project_id)
    if request.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project id mismatch")

    workflow_config = request.to_domain()
    WORKFLOW_CONFIGS[workflow_config.id] = workflow_config
    project.workflow_config_ids.append(workflow_config.id)
    PROJECTS[project.id] = project
    return _workflow_config_schema(workflow_config)


register_api_router(router)

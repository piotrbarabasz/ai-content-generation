"""Artifact listing endpoints for the AI Content Studio API."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import ArtifactSchema

from .projects import ARTIFACTS_BY_RUN, get_workflow_run_or_404, register_api_router

router = APIRouter(tags=["artifacts"])


@router.get("/workflow-runs/{workflow_run_id}/artifacts", response_model=list[ArtifactSchema])
def list_artifacts(workflow_run_id: str) -> list[ArtifactSchema]:
    get_workflow_run_or_404(workflow_run_id)
    return [ArtifactSchema.model_validate(artifact) for artifact in ARTIFACTS_BY_RUN.get(workflow_run_id, [])]


register_api_router(router)

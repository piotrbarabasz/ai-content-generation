"""Workflow run endpoints for the AI Content Studio API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.schemas import ExportBundleSchema, WorkflowRunCreateRequest, WorkflowRunSchema
from app.domain.artifact import Artifact
from app.domain.export_bundle import ExportBundle

from .approvals import approval_checkpoints_are_resolved, get_blocking_approval_checkpoints
from .projects import (
    ARTIFACTS_BY_RUN,
    EXPORT_BUNDLES,
    PROJECTS,
    WORKFLOW_CONFIGS,
    WORKFLOW_RUNS,
    get_workflow_config_or_404,
    get_workflow_run_or_404,
    register_api_router,
    store_artifact,
)

router = APIRouter(tags=["workflow-runs"])


def _workflow_run_schema(workflow_run):
    return WorkflowRunSchema.model_validate(workflow_run)


def _export_bundle_schema(export_bundle: ExportBundle) -> ExportBundleSchema:
    return ExportBundleSchema.model_validate(export_bundle)


def _ensure_export_bundle(workflow_run_id: str) -> ExportBundle:
    existing = EXPORT_BUNDLES.get(workflow_run_id)
    if existing is not None:
        return existing

    workflow_run = get_workflow_run_or_404(workflow_run_id)
    workflow_config = get_workflow_config_or_404(workflow_run.workflow_config_id)

    manifest_artifact = Artifact.create(
        workflow_run_id=workflow_run.id,
        module_name="export",
        artifact_type="manifest",
        storage_key=f"artifacts/{workflow_run.id}/manifest.json",
        metadata={"bundle": workflow_run.id},
    )
    store_artifact(manifest_artifact)

    bundle = ExportBundle.create(
        workflow_run_id=workflow_run.id,
        manifest_path=manifest_artifact.storage_key,
        required_files=["manifest.json", "workflow_config.json", "workflow_run.json"],
        included_artifacts=[artifact.storage_key for artifact in ARTIFACTS_BY_RUN.get(workflow_run.id, [])],
        missing_optional_artifacts=[],
        approval_summary={"workflow_run_status": workflow_run.status},
        provider_summary={"workflow_preset": workflow_config.workflow_preset.value},
        status="created",
    )
    EXPORT_BUNDLES[workflow_run.id] = bundle
    return bundle


@router.post("/workflow-runs", response_model=WorkflowRunSchema, status_code=status.HTTP_201_CREATED)
def create_workflow_run(request: WorkflowRunCreateRequest) -> WorkflowRunSchema:
    workflow_config = get_workflow_config_or_404(request.workflow_config_id)
    if workflow_config.id not in WORKFLOW_CONFIGS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow config not found")

    workflow_run = request.to_domain()
    workflow_run.status = "running"
    workflow_run.current_stage = "started"
    WORKFLOW_RUNS[workflow_run.id] = workflow_run
    project = PROJECTS[workflow_config.project_id]
    project.workflow_run_ids.append(workflow_run.id)
    PROJECTS[project.id] = project
    return _workflow_run_schema(workflow_run)


@router.get("/workflow-runs/{workflow_run_id}", response_model=WorkflowRunSchema)
def get_workflow_run(workflow_run_id: str) -> WorkflowRunSchema:
    return _workflow_run_schema(get_workflow_run_or_404(workflow_run_id))


@router.post("/workflow-runs/{workflow_run_id}/export-bundle", response_model=ExportBundleSchema, status_code=status.HTTP_201_CREATED)
def request_export_bundle(workflow_run_id: str) -> ExportBundleSchema:
    bundle = _ensure_export_bundle(workflow_run_id)
    return _export_bundle_schema(bundle)


@router.get("/workflow-runs/{workflow_run_id}/export-bundle", response_model=ExportBundleSchema)
def get_export_bundle(workflow_run_id: str) -> ExportBundleSchema:
    bundle = _ensure_export_bundle(workflow_run_id)
    return _export_bundle_schema(bundle)


@router.post("/workflow-runs/{workflow_run_id}/resume", response_model=WorkflowRunSchema)
def resume_workflow_run(workflow_run_id: str) -> WorkflowRunSchema:
    workflow_run = get_workflow_run_or_404(workflow_run_id)
    blocking_checkpoints = get_blocking_approval_checkpoints(workflow_run_id)
    if not approval_checkpoints_are_resolved(workflow_run_id):
        blocking_types = ", ".join(
            checkpoint.checkpoint_type for checkpoint in blocking_checkpoints
        ) or "unknown"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Workflow run is blocked by unresolved approval checkpoints: {blocking_types}.",
        )

    if workflow_run.status == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Completed workflow runs cannot be resumed.",
        )

    workflow_run.status = "running"
    if not workflow_run.current_stage:
        workflow_run.current_stage = "resumed"
    WORKFLOW_RUNS[workflow_run.id] = workflow_run
    return _workflow_run_schema(workflow_run)


register_api_router(router)

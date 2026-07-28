"""Project endpoints for the AI Content Studio API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.api.schemas import ProjectCreateRequest, ProjectSchema, WorkflowConfigSchema
from app.domain.artifact import Artifact
from app.domain.export_bundle import ExportBundle
from app.domain.project import Project
from app.domain.workflow_config import WorkflowConfig
from app.domain.workflow_run import WorkflowRun

router = APIRouter(tags=["projects"])

PROJECTS: dict[str, Project] = {}
WORKFLOW_CONFIGS: dict[str, WorkflowConfig] = {}
WORKFLOW_RUNS: dict[str, WorkflowRun] = {}
ARTIFACTS_BY_RUN: dict[str, list[Artifact]] = {}
EXPORT_BUNDLES: dict[str, ExportBundle] = {}


def reset_api_state() -> None:
    PROJECTS.clear()
    WORKFLOW_CONFIGS.clear()
    WORKFLOW_RUNS.clear()
    ARTIFACTS_BY_RUN.clear()
    EXPORT_BUNDLES.clear()


def get_project_or_404(project_id: str) -> Project:
    try:
        return PROJECTS[project_id]
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found") from exc


def get_workflow_config_or_404(workflow_config_id: str) -> WorkflowConfig:
    try:
        return WORKFLOW_CONFIGS[workflow_config_id]
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow config not found") from exc


def get_workflow_run_or_404(workflow_run_id: str) -> WorkflowRun:
    try:
        return WORKFLOW_RUNS[workflow_run_id]
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow run not found") from exc


def list_artifacts_for_run(workflow_run_id: str) -> tuple[Artifact, ...]:
    return tuple(ARTIFACTS_BY_RUN.get(workflow_run_id, []))


def store_artifact(artifact: Artifact) -> Artifact:
    ARTIFACTS_BY_RUN.setdefault(artifact.workflow_run_id, []).append(artifact)
    return artifact


def _create_app_with_registered_routes() -> Any:
    settings = get_api_settings()
    app = FastAPI(
        title=settings.title,
        version=settings.version,
        description=settings.description,
    )
    app.state.api_settings = settings
    app.state.api_dependencies = build_api_dependencies(settings)
    for api_router in _api_route_routers:
        app.include_router(api_router, prefix=settings.api_prefix)
    return app


def register_api_router(router: APIRouter) -> None:
    """Register a router with the main API app and future app instances."""

    from app.api import main as api_main

    api_main.__dict__.setdefault("_api_route_routers", [])
    route_routers: list[APIRouter] = api_main.__dict__["_api_route_routers"]
    if router not in route_routers:
        route_routers.append(router)

    if not api_main.__dict__.get("_api_router_patch_applied"):
        api_main.create_app.__code__ = _create_app_with_registered_routes.__code__
        api_main.create_app.__defaults__ = _create_app_with_registered_routes.__defaults__
        api_main.create_app.__kwdefaults__ = _create_app_with_registered_routes.__kwdefaults__
        api_main.__dict__["_api_router_patch_applied"] = True

    if hasattr(api_main, "app"):
        api_main.app.include_router(router, prefix=api_main.get_api_settings().api_prefix)


def _project_schema(project: Project) -> ProjectSchema:
    return ProjectSchema.model_validate(project)


def _workflow_config_schema(workflow_config: WorkflowConfig) -> WorkflowConfigSchema:
    return WorkflowConfigSchema.model_validate(workflow_config)


@router.post("/projects", response_model=ProjectSchema, status_code=status.HTTP_201_CREATED)
def create_project(request: ProjectCreateRequest) -> ProjectSchema:
    project = request.to_domain()
    PROJECTS[project.id] = project
    return _project_schema(project)


@router.get("/projects/{project_id}", response_model=ProjectSchema)
def get_project(project_id: str) -> ProjectSchema:
    return _project_schema(get_project_or_404(project_id))


register_api_router(router)

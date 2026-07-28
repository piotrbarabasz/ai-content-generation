from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.main import create_app
from app.api.routes.projects import create_project, get_project, reset_api_state
from app.api.routes.workflow_configs import create_workflow_config
from app.api.routes.workflow_runs import (
    create_workflow_run,
    get_export_bundle,
    get_workflow_run,
    request_export_bundle,
)
from app.api.routes.artifacts import list_artifacts
from app.api.schemas import (
    ProjectCreateRequest,
    WorkflowConfigCreateRequest,
    WorkflowRunCreateRequest,
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_api_state()
    yield
    reset_api_state()


def test_minimal_api_endpoints_are_registered_on_the_application() -> None:
    api_app = create_app()
    paths = set(api_app.openapi()["paths"])

    assert "/api/v1/projects" in paths
    assert "/api/v1/projects/{project_id}" in paths
    assert "/api/v1/projects/{project_id}/workflow-configs" in paths
    assert "/api/v1/workflow-runs" in paths
    assert "/api/v1/workflow-runs/{workflow_run_id}" in paths
    assert "/api/v1/workflow-runs/{workflow_run_id}/artifacts" in paths
    assert "/api/v1/workflow-runs/{workflow_run_id}/export-bundle" in paths


def test_minimal_api_endpoints_create_and_return_projects_workflow_configs_runs_and_exports() -> None:
    project_request = ProjectCreateRequest.model_validate(
        {
            "workspaceId": "workspace_1",
            "name": "Launch Package",
            "contentType": "short_video",
            "contentGenre": "news",
            "targetPlatform": "youtube_shorts",
            "language": "en",
            "tone": "neutral",
        }
    )
    project = create_project(project_request)

    assert project.name == "Launch Package"
    assert project.content_type.value == "short_video"
    assert project.workflow_config_ids == []
    assert project.workflow_run_ids == []
    assert get_project(project.id) == project

    workflow_config_request = WorkflowConfigCreateRequest.model_validate(
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
            "disabledModules": ["voiceover"],
            "providerConfig": {
                "LLMProvider": {"providerName": "mock", "enabled": True},
                "StorageProvider": {"providerName": "mock", "enabled": True},
            },
        }
    )
    workflow_config = create_workflow_config(project.id, workflow_config_request)

    assert workflow_config.project_id == project.id
    assert workflow_config.workflow_preset.value == "short_video"
    assert workflow_config.enabled_modules == ["brief", "export"]

    workflow_run_request = WorkflowRunCreateRequest.model_validate(
        {"workflowConfigId": workflow_config.id}
    )
    workflow_run = create_workflow_run(workflow_run_request)

    assert workflow_run.workflow_config_id == workflow_config.id
    assert workflow_run.status == "running"
    assert workflow_run.current_stage == "started"
    assert get_workflow_run(workflow_run.id) == workflow_run

    assert list_artifacts(workflow_run.id) == []

    export_bundle = request_export_bundle(workflow_run.id)
    assert export_bundle.workflow_run_id == workflow_run.id
    assert export_bundle.manifest_path.endswith("manifest.json")
    assert export_bundle.required_files == ["manifest.json", "workflow_config.json", "workflow_run.json"]
    assert export_bundle.status == "created"
    assert get_export_bundle(workflow_run.id) == export_bundle

    artifacts = list_artifacts(workflow_run.id)
    assert len(artifacts) == 1
    assert artifacts[0].workflow_run_id == workflow_run.id
    assert artifacts[0].module_name == "export"
    assert artifacts[0].artifact_type == "manifest"
    assert artifacts[0].storage_key == f"artifacts/{workflow_run.id}/manifest.json"
    assert artifacts[0].metadata == {"bundle": workflow_run.id}


def test_minimal_api_endpoints_validate_request_payloads() -> None:
    with pytest.raises(ValidationError):
        ProjectCreateRequest.model_validate(
            {
                "workspaceId": "workspace_1",
                "name": "Bad Project",
                "contentType": "not_a_real_type",
                "contentGenre": "news",
                "targetPlatform": "youtube_shorts",
                "language": "en",
                "tone": "neutral",
            }
        )

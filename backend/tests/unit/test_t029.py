from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.base import DomainValidationError
from app.domain.enums import ContentGenre, ContentType, DurationProfile, WorkflowPreset
from app.domain.export_bundle import ExportBundle
from app.modules.export_manifest import ExportBundleManifest


def test_export_bundle_manifest_round_trips_through_payload() -> None:
    created_at = datetime(2026, 7, 28, 12, 30, tzinfo=UTC)

    manifest = ExportBundleManifest.create(
        export_id="export_1",
        project_id="project_1",
        workflow_run_id="workflow_run_1",
        workflow_preset=WorkflowPreset.SHORT_VIDEO,
        content_type=ContentType.SHORT_VIDEO,
        content_genre=ContentGenre.COMMENTARY,
        duration_profile=DurationProfile.SIXTY_SECONDS,
        created_at=created_at,
        included_artifacts=["script.txt", "scene_plan.json"],
        missing_optional_artifacts=["voiceover.wav"],
        module_results={
            "export": {"status": "completed"},
            "scriptGeneration": {"status": "completed"},
        },
        approval_summary={"export": "approved"},
        provider_summary={"llm": "mock"},
        artifact_references={"script": {"storageKey": "artifacts/run_1/script.txt"}},
    )

    payload = manifest.to_payload()
    restored = ExportBundleManifest.from_payload(payload)

    assert payload["schemaVersion"] == 1
    assert payload["workflowPreset"] == "short_video"
    assert payload["contentType"] == "short_video"
    assert payload["contentGenre"] == "commentary"
    assert payload["durationProfile"] == "60s"
    assert payload["includedArtifacts"] == ["script.txt", "scene_plan.json"]
    assert payload["missingOptionalArtifacts"] == ["voiceover.wav"]
    assert payload["moduleResults"]["export"]["status"] == "completed"
    assert payload["artifactReferences"]["script"]["storageKey"] == "artifacts/run_1/script.txt"
    assert manifest.to_payload() == restored.to_payload()
    assert restored.created_at == created_at
    assert ExportBundleManifest.REQUIRED_FILES == (
        "manifest.json",
        "workflow_config.json",
        "workflow_run.json",
    )


def test_export_bundle_manifest_rejects_missing_required_values() -> None:
    with pytest.raises(DomainValidationError, match="ExportBundleManifest export_id is required"):
        ExportBundleManifest.create(
            export_id=" ",
            project_id="project_1",
            workflow_run_id="workflow_run_1",
            workflow_preset="short_video",
            content_type="short_video",
            content_genre="news",
            duration_profile="60s",
        )

    with pytest.raises(DomainValidationError, match="is not a valid WorkflowPreset"):
        ExportBundleManifest.create(
            export_id="export_1",
            project_id="project_1",
            workflow_run_id="workflow_run_1",
            workflow_preset="not_real",
            content_type="short_video",
            content_genre="news",
            duration_profile="60s",
        )


def test_export_bundle_can_store_manifest_payload() -> None:
    manifest = ExportBundleManifest.create(
        export_id="export_1",
        project_id="project_1",
        workflow_run_id="workflow_run_1",
        workflow_preset="short_video",
        content_type="short_video",
        content_genre="news",
        duration_profile="60s",
        included_artifacts=["manifest.json", "workflow_config.json", "workflow_run.json"],
    )

    export_bundle = ExportBundle.create(
        workflow_run_id="workflow_run_1",
        manifest_path="artifacts/workflow_run_1/manifest.json",
        manifest=manifest.to_payload(),
        required_files=list(ExportBundleManifest.REQUIRED_FILES),
        included_artifacts=["manifest.json"],
        missing_optional_artifacts=["voiceover.wav"],
        approval_summary={"export": "pending"},
        provider_summary={"llm": "mock"},
        status="created",
    )

    assert export_bundle.manifest == manifest.to_payload()
    assert export_bundle.required_files == list(ExportBundleManifest.REQUIRED_FILES)
    assert export_bundle.included_artifacts == ["manifest.json"]
    assert export_bundle.missing_optional_artifacts == ["voiceover.wav"]
    assert export_bundle.status == "created"

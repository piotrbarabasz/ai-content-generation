from __future__ import annotations

from datetime import UTC, datetime

from app.domain.enums import ContentGenre, ContentType, DurationProfile, WorkflowPreset
from app.domain.export_bundle import ExportBundle
from app.modules.export_manifest import ExportBundleManifest


def test_export_bundle_manifest_captures_short_video_bundle_content() -> None:
    created_at = datetime(2026, 7, 28, 12, 30, tzinfo=UTC)

    manifest = ExportBundleManifest.create(
        export_id="export_short_video",
        project_id="project_1",
        workflow_run_id="workflow_run_1",
        workflow_preset=WorkflowPreset.SHORT_VIDEO,
        content_type=ContentType.SHORT_VIDEO,
        content_genre=ContentGenre.COMMENTARY,
        duration_profile=DurationProfile.SIXTY_SECONDS,
        created_at=created_at,
        included_artifacts=[
            "manifest.json",
            "workflow_config.json",
            "workflow_run.json",
            "scene_plan.json",
            "captions.json",
            "voiceover.wav",
            "speech_timeline.json",
            "render.mp4",
        ],
        module_results={
            "scenePlanning": {"status": "completed"},
            "voiceover": {"status": "completed"},
            "captions": {"status": "completed"},
            "videoRendering": {"status": "completed"},
            "export": {"status": "completed"},
        },
        approval_summary={"export": "approved"},
        provider_summary={"llm": "mock", "storage": "local"},
        artifact_references={
            "workflow_config.json": {
                "artifact_name": "workflow_config.json",
                "module_name": "export",
                "status": "completed",
            },
            "workflow_run.json": {
                "artifact_name": "workflow_run.json",
                "module_name": "export",
                "status": "completed",
            },
            "scene_plan.json": {
                "artifact_name": "scene_plan.json",
                "module_name": "scenePlanning",
                "status": "completed",
            },
            "captions.json": {
                "artifact_name": "captions.json",
                "module_name": "captions",
                "status": "completed",
            },
            "voiceover.wav": {
                "artifact_name": "voiceover.wav",
                "module_name": "voiceover",
                "status": "completed",
            },
            "render.mp4": {
                "artifact_name": "render.mp4",
                "module_name": "videoRendering",
                "status": "completed",
            },
        },
    )

    payload = manifest.to_payload()
    export_bundle = ExportBundle.create(
        workflow_run_id="workflow_run_1",
        manifest_path="artifacts/workflow_run_1/manifest.json",
        manifest=payload,
        required_files=list(ExportBundleManifest.REQUIRED_FILES),
        included_artifacts=payload["includedArtifacts"],
        missing_optional_artifacts=payload["missingOptionalArtifacts"],
        approval_summary=payload["approvalSummary"],
        provider_summary=payload["providerSummary"],
        status="created",
    )

    assert ExportBundleManifest.REQUIRED_FILES == (
        "manifest.json",
        "workflow_config.json",
        "workflow_run.json",
    )
    assert payload["createdAt"] == created_at.isoformat()
    assert payload["includedArtifacts"] == [
        "manifest.json",
        "workflow_config.json",
        "workflow_run.json",
        "scene_plan.json",
        "captions.json",
        "voiceover.wav",
        "speech_timeline.json",
        "render.mp4",
    ]
    assert payload["missingOptionalArtifacts"] == []
    assert payload["artifactReferences"]["voiceover.wav"]["module_name"] == "voiceover"
    assert payload["artifactReferences"]["render.mp4"]["artifact_name"] == "render.mp4"
    assert export_bundle.required_files == list(ExportBundleManifest.REQUIRED_FILES)
    assert export_bundle.included_artifacts == payload["includedArtifacts"]
    assert export_bundle.missing_optional_artifacts == []
    assert export_bundle.status == "created"


def test_export_bundle_manifest_tracks_missing_optional_long_form_content() -> None:
    created_at = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)

    manifest = ExportBundleManifest.create(
        export_id="export_long_form",
        project_id="project_2",
        workflow_run_id="workflow_run_2",
        workflow_preset=WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER,
        content_type=ContentType.LONG_FORM_VIDEO,
        content_genre=ContentGenre.DOCUMENTARY,
        duration_profile=DurationProfile.EIGHT_FIFTEEN_MINUTES,
        created_at=created_at,
        included_artifacts=[
            "manifest.json",
            "workflow_config.json",
            "workflow_run.json",
            "research.json",
            "dossier.json",
            "outline.json",
            "script.txt",
            "post_processed_script.txt",
            "qa_report.json",
        ],
        missing_optional_artifacts=[
            "voiceover.wav",
            "speech_timeline.json",
            "render.mp4",
        ],
        module_results={
            "research": {"status": "completed"},
            "dossier": {"status": "completed"},
            "outline": {"status": "completed"},
            "scriptGeneration": {"status": "completed"},
            "postProcessing": {"status": "completed"},
            "qa": {"status": "completed"},
            "export": {"status": "completed"},
        },
        approval_summary={"export": "pending"},
        provider_summary={"llm": "mock", "storage": "local"},
        artifact_references={
            "workflow_config.json": {
                "artifact_name": "workflow_config.json",
                "module_name": "export",
                "status": "completed",
            },
            "workflow_run.json": {
                "artifact_name": "workflow_run.json",
                "module_name": "export",
                "status": "completed",
            },
            "research.json": {
                "artifact_name": "research.json",
                "module_name": "research",
                "status": "completed",
            },
            "dossier.json": {
                "artifact_name": "dossier.json",
                "module_name": "dossier",
                "status": "completed",
            },
            "script.txt": {
                "artifact_name": "script.txt",
                "module_name": "scriptGeneration",
                "status": "completed",
            },
            "qa_report.json": {
                "artifact_name": "qa_report.json",
                "module_name": "qa",
                "status": "completed",
            },
        },
    )

    payload = manifest.to_payload()
    export_bundle = ExportBundle.create(
        workflow_run_id="workflow_run_2",
        manifest_path="artifacts/workflow_run_2/manifest.json",
        manifest=payload,
        required_files=ExportBundleManifest.REQUIRED_FILES,
        included_artifacts=payload["includedArtifacts"],
        missing_optional_artifacts=payload["missingOptionalArtifacts"],
        approval_summary=payload["approvalSummary"],
        provider_summary=payload["providerSummary"],
        status="created",
    )

    assert payload["createdAt"] == created_at.isoformat()
    assert payload["includedArtifacts"] == [
        "manifest.json",
        "workflow_config.json",
        "workflow_run.json",
        "research.json",
        "dossier.json",
        "outline.json",
        "script.txt",
        "post_processed_script.txt",
        "qa_report.json",
    ]
    assert payload["missingOptionalArtifacts"] == [
        "voiceover.wav",
        "speech_timeline.json",
        "render.mp4",
    ]
    assert "voiceover.wav" not in payload["artifactReferences"]
    assert "render.mp4" not in payload["artifactReferences"]
    assert export_bundle.required_files == [
        "manifest.json",
        "workflow_config.json",
        "workflow_run.json",
    ]
    assert export_bundle.included_artifacts == payload["includedArtifacts"]
    assert export_bundle.missing_optional_artifacts == [
        "voiceover.wav",
        "speech_timeline.json",
        "render.mp4",
    ]

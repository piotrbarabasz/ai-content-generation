from __future__ import annotations

from contextlib import contextmanager
import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.domain.approval import ApprovalCheckpoint
from app.domain.enums import ContentGenre, ContentType, DurationProfile, WorkflowPreset
from app.modules.export import ExportModule
from app.storage.local_store import LocalArtifactStore
from app.workflow.execution import ModuleExecutionContext, ModuleResult


@contextmanager
def _workspace_tempdir(name: str):
    root = Path(__file__).resolve().parents[3] / ".tmp" / name
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _artifact_payload(module_name: str, artifact_name: str) -> dict[str, str]:
    return {
        "name": artifact_name,
        "storage_key": f"artifacts/workflow_run_1/{artifact_name}",
        "module_name": module_name,
    }


def _module_result(module_name: str, *artifact_names: str) -> ModuleResult:
    output: dict[str, object] = {}
    for index, artifact_name in enumerate(artifact_names):
        key = "artifact" if index == 0 else f"{artifact_name.replace('.', '_')}_artifact"
        output[key] = _artifact_payload(module_name, artifact_name)
    return ModuleResult(
        module_name=module_name,
        status="completed",
        output_artifact_ids=artifact_names,
        output=output,
    )


def test_short_video_export_bundle_keeps_rejected_artifacts_and_required_files() -> None:
    with _workspace_tempdir("test_export_bundle_short_video") as store_root:
        store = LocalArtifactStore(store_root)
        module = ExportModule(artifact_store=store)
        workflow_run_id = "workflow_run_1"
        workflow_config_id = "workflow_config_1"
        workflow_run = {
            "id": workflow_run_id,
            "workflow_config_id": workflow_config_id,
            "status": "completed",
            "current_stage": "export",
        }
        workflow_config = {
            "id": workflow_config_id,
            "workflow_preset": WorkflowPreset.SHORT_VIDEO.value,
            "enabled_modules": [
                "brief",
                "scenePlanning",
                "voiceover",
                "captions",
                "videoRendering",
                "export",
            ],
        }
        checkpoint = ApprovalCheckpoint.create(
            workflow_run_id=workflow_run_id,
            checkpoint_type="scene_plan",
            artifact_id="scene_plan.json",
        )
        checkpoint.reject(reviewer_id="reviewer_1", comment="Revise the scene plan.")

        context = ModuleExecutionContext(
            workflow_run_id=workflow_run_id,
            workflow_config_id=workflow_config_id,
            module_name="export",
            enabled_modules=(
                "brief",
                "scenePlanning",
                "voiceover",
                "captions",
                "videoRendering",
                "export",
            ),
            inputs={
                "project_id": "project_1",
                "workflow_preset": WorkflowPreset.SHORT_VIDEO.value,
                "content_type": ContentType.SHORT_VIDEO.value,
                "content_genre": ContentGenre.COMMENTARY.value,
                "duration_profile": DurationProfile.SIXTY_SECONDS.value,
                "export_id": "export_short_video",
                "created_at": datetime(2026, 7, 28, 12, 30, tzinfo=UTC),
                "workflow_config": workflow_config,
                "workflow_run": workflow_run,
                "approval_summary": {"scene_plan": "rejected", "export": "pending"},
                "provider_summary": {"llm": "mock", "storage": "local"},
            },
            module_results={
                "brief": _module_result("brief", "brief.json"),
                "scenePlanning": _module_result(
                    "scenePlanning",
                    "scene_plan.json",
                    "render_scenes.json",
                ),
                "voiceover": _module_result(
                    "voiceover",
                    "voiceover.wav",
                    "speech_timeline.json",
                ),
                "captions": _module_result("captions", "captions.json"),
                "videoRendering": _module_result("videoRendering", "render.mp4"),
            },
        )

        result = module.execute(context)
        manifest = result.output["manifest"]
        export_bundle = result.output["export_bundle"]

        assert checkpoint.status == "rejected"
        assert checkpoint.artifact_id == "scene_plan.json"
        assert checkpoint.latest_decision is not None
        assert checkpoint.latest_decision.decision == "reject"
        assert result.status == "completed"
        assert manifest["includedArtifacts"] == [
            "manifest.json",
            "workflow_config.json",
            "workflow_run.json",
            "brief.json",
            "scene_plan.json",
            "render_scenes.json",
            "voiceover.wav",
            "speech_timeline.json",
            "captions.json",
            "render.mp4",
        ]
        assert manifest["missingOptionalArtifacts"] == []
        assert manifest["artifactReferences"]["scene_plan.json"]["module_name"] == "scenePlanning"
        assert manifest["artifactReferences"]["voiceover.wav"]["artifact_name"] == "voiceover.wav"
        assert export_bundle["required_files"] == [
            "manifest.json",
            "workflow_config.json",
            "workflow_run.json",
        ]
        assert export_bundle["included_artifacts"] == manifest["includedArtifacts"]
        assert export_bundle["missing_optional_artifacts"] == []
        assert {stored.name for stored in store.list_artifacts()} == {"manifest.json"}


def test_long_form_export_bundle_records_missing_optional_artifacts() -> None:
    with _workspace_tempdir("test_export_bundle_long_form") as store_root:
        store = LocalArtifactStore(store_root)
        module = ExportModule(artifact_store=store)
        workflow_run_id = "workflow_run_2"
        workflow_config_id = "workflow_config_2"
        workflow_run = {
            "id": workflow_run_id,
            "workflow_config_id": workflow_config_id,
            "status": "completed",
            "current_stage": "export",
        }
        workflow_config = {
            "id": workflow_config_id,
            "workflow_preset": WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER.value,
            "enabled_modules": [
                "brief",
                "research",
                "dossier",
                "outline",
                "scriptGeneration",
                "postProcessing",
                "qa",
                "export",
            ],
        }
        checkpoint = ApprovalCheckpoint.create(
            workflow_run_id=workflow_run_id,
            checkpoint_type="export",
            artifact_id="manifest.json",
        )
        checkpoint.reject(reviewer_id="reviewer_2", comment="Hold export until review is complete.")

        context = ModuleExecutionContext(
            workflow_run_id=workflow_run_id,
            workflow_config_id=workflow_config_id,
            module_name="export",
            enabled_modules=(
                "brief",
                "research",
                "dossier",
                "outline",
                "scriptGeneration",
                "postProcessing",
                "qa",
                "export",
            ),
            disabled_modules=("voiceover", "videoRendering"),
            inputs={
                "project_id": "project_2",
                "workflow_preset": WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER.value,
                "content_type": ContentType.LONG_FORM_VIDEO.value,
                "content_genre": ContentGenre.DOCUMENTARY.value,
                "duration_profile": DurationProfile.EIGHT_FIFTEEN_MINUTES.value,
                "export_id": "export_long_form",
                "created_at": datetime(2026, 7, 28, 13, 0, tzinfo=UTC),
                "workflow_config": workflow_config,
                "workflow_run": workflow_run,
                "approval_summary": {"export": "rejected"},
                "provider_summary": {"llm": "mock", "storage": "local"},
            },
            module_results={
                "brief": _module_result("brief", "brief.json"),
                "research": _module_result("research", "research.json"),
                "dossier": _module_result("dossier", "dossier.json"),
                "outline": _module_result("outline", "outline.json"),
                "scriptGeneration": _module_result(
                    "scriptGeneration",
                    "script.txt",
                    "script.json",
                    "narrative_segments.json",
                ),
                "postProcessing": _module_result(
                    "postProcessing",
                    "post_processed_script.txt",
                ),
                "qa": _module_result("qa", "qa_report.json"),
            },
        )

        result = module.execute(context)
        manifest = result.output["manifest"]
        export_bundle = result.output["export_bundle"]

        assert checkpoint.status == "rejected"
        assert checkpoint.artifact_id == "manifest.json"
        assert checkpoint.latest_decision is not None
        assert checkpoint.latest_decision.comment == "Hold export until review is complete."
        assert result.status == "completed"
        assert manifest["includedArtifacts"] == [
            "manifest.json",
            "workflow_config.json",
            "workflow_run.json",
            "brief.json",
            "research.json",
            "dossier.json",
            "outline.json",
            "script.txt",
            "script.json",
            "narrative_segments.json",
            "post_processed_script.txt",
            "qa_report.json",
        ]
        assert manifest["missingOptionalArtifacts"] == [
            "voiceover.wav",
            "speech_timeline.json",
            "render.mp4",
        ]
        assert "voiceover.wav" not in manifest["artifactReferences"]
        assert "render.mp4" not in manifest["artifactReferences"]
        assert export_bundle["required_files"] == [
            "manifest.json",
            "workflow_config.json",
            "workflow_run.json",
        ]
        assert export_bundle["included_artifacts"] == manifest["includedArtifacts"]
        assert export_bundle["missing_optional_artifacts"] == [
            "voiceover.wav",
            "speech_timeline.json",
            "render.mp4",
        ]

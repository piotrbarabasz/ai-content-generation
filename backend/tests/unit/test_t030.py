from __future__ import annotations

from contextlib import contextmanager
import shutil
from datetime import UTC, datetime
from pathlib import Path

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


def test_export_module_definition_matches_contract() -> None:
    with _workspace_tempdir("test_t030_definition") as store_root:
        module = ExportModule(artifact_store=LocalArtifactStore(store_root))

        assert module.definition.name == "export"
        assert module.definition.dependencies == ()
        assert module.definition.enabled_by_default is True
        assert module.definition.disabled_behavior == "fail"
        assert module.definition.retry_limit == 1
        assert module.definition.artifact_outputs == ("manifest.json",)
        assert module.definition.config_schema["properties"]["manifest_name"]["type"] == "string"


def test_export_module_builds_short_video_manifest_bundle() -> None:
    with _workspace_tempdir("test_t030_short_video") as store_root:
        store = LocalArtifactStore(store_root)
        module = ExportModule(artifact_store=store)
        workflow_run = {
            "id": "workflow_run_1",
            "workflow_config_id": "workflow_config_1",
            "status": "completed",
            "current_stage": "export",
        }
        workflow_config = {
            "id": "workflow_config_1",
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
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_1",
            workflow_config_id="workflow_config_1",
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
                "approval_summary": {"export": "approved"},
                "provider_summary": {"llm": "mock", "storage": "local"},
            },
            module_results={
                "brief": _module_result("brief", "brief.json"),
                "scenePlanning": _module_result(
                    "scenePlanning",
                    "render_scenes.json",
                    "scene_plan.json",
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
        artifact = result.output["artifact"]

        assert result.status == "completed"
        assert result.output_artifact_ids == ("manifest.json",)
        assert artifact["name"] == "manifest.json"
        assert artifact["artifact_type"] == "manifest"
        assert manifest["schemaVersion"] == 1
        assert manifest["exportId"] == "export_short_video"
        assert manifest["workflowPreset"] == WorkflowPreset.SHORT_VIDEO.value
        assert manifest["contentType"] == ContentType.SHORT_VIDEO.value
        assert manifest["contentGenre"] == ContentGenre.COMMENTARY.value
        assert manifest["durationProfile"] == DurationProfile.SIXTY_SECONDS.value
        assert manifest["includedArtifacts"] == [
            "manifest.json",
            "workflow_config.json",
            "workflow_run.json",
            "brief.json",
            "render_scenes.json",
            "scene_plan.json",
            "voiceover.wav",
            "speech_timeline.json",
            "captions.json",
            "render.mp4",
        ]
        assert manifest["missingOptionalArtifacts"] == []
        assert manifest["artifactReferences"]["workflow_config.json"]["id"] == "workflow_config_1"
        assert manifest["artifactReferences"]["workflow_run.json"]["status"] == "completed"
        assert manifest["artifactReferences"]["voiceover.wav"]["name"] == "voiceover.wav"
        assert export_bundle["required_files"] == [
            "manifest.json",
            "workflow_config.json",
            "workflow_run.json",
        ]
        assert export_bundle["included_artifacts"] == manifest["includedArtifacts"]
        assert export_bundle["missing_optional_artifacts"] == []
        assert {stored.name for stored in store.list_artifacts()} == {"manifest.json"}


def test_export_module_builds_long_form_manifest_bundle_with_missing_optionals() -> None:
    with _workspace_tempdir("test_t030_long_form") as store_root:
        store = LocalArtifactStore(store_root)
        module = ExportModule(artifact_store=store)
        workflow_run = {
            "id": "workflow_run_2",
            "workflow_config_id": "workflow_config_2",
            "status": "completed",
            "current_stage": "export",
        }
        workflow_config = {
            "id": "workflow_config_2",
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
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_2",
            workflow_config_id="workflow_config_2",
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
                "approval_summary": {"export": "pending"},
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
                "postProcessing": _module_result("postProcessing", "post_processed_script.txt"),
                "qa": _module_result("qa", "qa_report.json"),
            },
        )

        result = module.execute(context)
        manifest = result.output["manifest"]
        export_bundle = result.output["export_bundle"]

        assert result.status == "completed"
        assert manifest["exportId"] == "export_long_form"
        assert manifest["workflowPreset"] == WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER.value
        assert manifest["contentType"] == ContentType.LONG_FORM_VIDEO.value
        assert manifest["contentGenre"] == ContentGenre.DOCUMENTARY.value
        assert manifest["durationProfile"] == DurationProfile.EIGHT_FIFTEEN_MINUTES.value
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
        assert manifest["artifactReferences"]["script.txt"]["name"] == "script.txt"
        assert manifest["artifactReferences"]["workflow_run.json"]["id"] == "workflow_run_2"
        assert export_bundle["included_artifacts"] == manifest["includedArtifacts"]
        assert export_bundle["missing_optional_artifacts"] == [
            "voiceover.wav",
            "speech_timeline.json",
            "render.mp4",
        ]
        assert {stored.name for stored in store.list_artifacts()} == {"manifest.json"}

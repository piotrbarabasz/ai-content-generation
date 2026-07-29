from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path

from app.domain.enums import ContentGenre, ContentType, DurationProfile, TargetPlatform, WorkflowPreset
from app.domain.workflow_config import WorkflowConfig
from app.modules.brief import BriefModule
from app.modules.dossier import DossierModule
from app.modules.export import ExportModule
from app.modules.outline import OutlineModule
from app.modules.post_processing import PostProcessingModule
from app.modules.qa import QAModule
from app.modules.research import ResearchModule
from app.modules.script_generation import ScriptGenerationModule
from app.modules.voiceover import VoiceoverModule
from app.providers.mock_llm import MockLLMProvider
from app.providers.mock_tts import MockTTSProvider
from app.providers.mocks import build_mock_provider_registry
from app.storage.local_store import LocalArtifactStore
from app.workflow.engine import CoreWorkflowEngine
from app.workflow.presets import LONG_FORM_SCRIPT_VOICEOVER_PRESET
from app.workflow.registry import ModuleRegistry


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


def _build_modules(store: LocalArtifactStore) -> dict[str, object]:
    llm_provider = MockLLMProvider("mock")
    tts_provider = MockTTSProvider("mock")
    return {
        "brief": BriefModule(),
        "research": ResearchModule(llm_provider=llm_provider, artifact_store=store),
        "dossier": DossierModule(artifact_store=store),
        "outline": OutlineModule(artifact_store=store),
        "scriptGeneration": ScriptGenerationModule(
            llm_provider=llm_provider,
            artifact_store=store,
        ),
        "postProcessing": PostProcessingModule(artifact_store=store),
        "qa": QAModule(llm_provider=llm_provider, artifact_store=store),
        "voiceover": VoiceoverModule(tts_provider=tts_provider, artifact_store=store),
        "export": ExportModule(artifact_store=store),
    }


def _workflow_config_payload(
    *,
    project_id: str,
    disabled_modules: tuple[str, ...],
    provider_config: dict[str, object],
) -> dict[str, object]:
    payload = LONG_FORM_SCRIPT_VOICEOVER_PRESET.build_workflow_config_payload(project_id=project_id)
    payload["enabledModules"] = [
        module_name
        for module_name in LONG_FORM_SCRIPT_VOICEOVER_PRESET.module_sequence
        if module_name not in disabled_modules
    ]
    payload["disabledModules"] = list(disabled_modules)
    payload["providerConfig"] = provider_config
    return payload


def _run_long_form_workflow(
    store_root: Path,
    *,
    project_id: str,
    workflow_run_id: str,
    topic: str,
    disabled_modules: tuple[str, ...],
) -> tuple[object, LocalArtifactStore, dict[str, object]]:
    store = LocalArtifactStore(store_root)
    modules = _build_modules(store)
    registry = ModuleRegistry(module.definition for module in modules.values())
    plan = registry.build_execution_plan(
        LONG_FORM_SCRIPT_VOICEOVER_PRESET.module_sequence,
        disabled_modules=disabled_modules,
    )

    workflow_config_payload = _workflow_config_payload(
        project_id=project_id,
        disabled_modules=disabled_modules,
        provider_config={
            "LLMProvider": {"providerName": "mock", "enabled": True},
            "StorageProvider": {"providerName": "mock", "enabled": True},
        },
    )
    workflow_config = WorkflowConfig.from_payload(workflow_config_payload)
    engine = CoreWorkflowEngine(registry, modules)

    result = engine.run(
        plan,
        workflow_run_id=workflow_run_id,
        workflow_config_id=workflow_config.id,
        workflow_config=workflow_config,
        provider_registry=build_mock_provider_registry(),
        inputs={
            "project_id": project_id,
            "workflow_preset": WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER.value,
            "content_type": ContentType.LONG_FORM_VIDEO.value,
            "content_genre": ContentGenre.DOCUMENTARY.value,
            "duration_profile": DurationProfile.EIGHT_FIFTEEN_MINUTES.value,
            "target_platform": TargetPlatform.YOUTUBE.value,
            "topic": topic,
            "objective": "Create a detailed long-form launch narrative.",
            "audience": "Early adopters",
            "constraints": ["Keep the pacing focused."],
            "success_criteria": ["Produce a complete export bundle."],
            "workflow_config": workflow_config_payload,
            "workflow_run": {
                "id": workflow_run_id,
                "workflow_config_id": workflow_config.id,
                "status": "running",
                "current_stage": "export",
            },
        },
    )

    return result, store, workflow_config_payload


def test_long_form_workflow_executes_from_topic_with_research_enabled() -> None:
    with _workspace_tempdir("test_long_form_workflow_research_enabled") as store_root:
        result, store, workflow_config_payload = _run_long_form_workflow(
            store_root,
            project_id="project_1",
            workflow_run_id="workflow_run_1",
            topic="Launch teaser",
            disabled_modules=("voiceover",),
        )

        manifest_names = {manifest.name for manifest in store.list_artifacts()}
        export_manifest = result.module_results["export"].output["manifest"]
        qa_report = result.module_results["qa"].output["qa_report"]

        assert result.status == "completed"
        assert result.completed_modules == (
            "brief",
            "research",
            "dossier",
            "outline",
            "scriptGeneration",
            "postProcessing",
            "qa",
            "export",
        )
        assert result.module_results["brief"].output["source_kind"] == "topic"
        assert result.module_results["research"].status == "completed"
        assert result.module_results["research"].output["source_kind"] == "topic"
        assert result.module_results["dossier"].status == "completed"
        assert result.module_results["dossier"].output["source_kind"] == "topic"
        assert result.module_results["outline"].output["source_kind"] == "dossier"
        assert result.module_results["scriptGeneration"].output["source_kind"] == "outline"
        assert result.module_results["postProcessing"].output["source_kind"] == "outline"
        assert result.module_results["qa"].status == "completed"
        assert qa_report["next_stage"] == "export"
        assert result.module_results["voiceover"].status == "skipped"
        assert result.module_results["voiceover"].skipped_reason == "disabled"
        assert export_manifest["missingOptionalArtifacts"] == [
            "voiceover.wav",
            "speech_timeline.json",
        ]
        assert "voiceover.wav" not in export_manifest["artifactReferences"]
        assert manifest_names == {
            "research.json",
            "dossier.json",
            "outline.json",
            "script.txt",
            "script.json",
            "narrative_segments.json",
            "post_processed_script.txt",
            "qa_report.json",
            "manifest.json",
        }
        assert workflow_config_payload["disabledModules"] == ["voiceover"]


def test_long_form_workflow_executes_without_research_or_voiceover() -> None:
    with _workspace_tempdir("test_long_form_workflow_research_disabled") as store_root:
        result, store, workflow_config_payload = _run_long_form_workflow(
            store_root,
            project_id="project_2",
            workflow_run_id="workflow_run_2",
            topic="Launch teaser",
            disabled_modules=("research", "dossier", "voiceover"),
        )

        manifest_names = {manifest.name for manifest in store.list_artifacts()}
        export_manifest = result.module_results["export"].output["manifest"]
        qa_report = result.module_results["qa"].output["qa_report"]

        assert result.status == "completed"
        assert result.completed_modules == (
            "brief",
            "outline",
            "scriptGeneration",
            "postProcessing",
            "qa",
            "export",
        )
        assert result.module_results["brief"].output["source_kind"] == "topic"
        assert result.module_results["research"].status == "skipped"
        assert result.module_results["dossier"].status == "skipped"
        assert result.module_results["outline"].output["source_kind"] == "dossier"
        assert result.module_results["scriptGeneration"].output["source_kind"] == "outline"
        assert result.module_results["postProcessing"].output["source_kind"] == "outline"
        assert result.module_results["qa"].status == "completed"
        assert qa_report["next_stage"] == "export"
        assert result.module_results["voiceover"].status == "skipped"
        assert result.module_results["voiceover"].skipped_reason == "disabled"
        assert export_manifest["missingOptionalArtifacts"] == [
            "research.json",
            "dossier.json",
            "voiceover.wav",
            "speech_timeline.json",
        ]
        assert "research.json" not in export_manifest["artifactReferences"]
        assert "dossier.json" not in export_manifest["artifactReferences"]
        assert "voiceover.wav" not in export_manifest["artifactReferences"]
        assert manifest_names == {
            "outline.json",
            "script.txt",
            "script.json",
            "narrative_segments.json",
            "post_processed_script.txt",
            "qa_report.json",
            "manifest.json",
        }
        assert workflow_config_payload["disabledModules"] == ["research", "dossier", "voiceover"]

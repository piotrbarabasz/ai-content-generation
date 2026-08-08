from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

from app.domain.content_brief import ContentBrief
from app.domain.enums import ContentGenre, ContentType, DurationProfile, TargetPlatform, WorkflowPreset
from app.modules.brief import BriefModule
from app.modules.captions import CaptionsModule
from app.modules.scene_planning import ScenePlanningModule
from app.modules.script_generation import ScriptGenerationModule
from app.modules.voiceover import VoiceoverModule
from app.providers.mock_assets import MockAssetProvider
from app.providers.mock_captions import MockCaptionProvider
from app.providers.mock_llm import MockLLMProvider
from app.providers.mock_transcription import MockTranscriptionProvider
from app.providers.mock_tts import MockTTSProvider
from app.providers.registry import ProviderRegistry
from app.storage.local_store import LocalArtifactStore
from app.workflow.engine import CoreWorkflowEngine
from app.workflow.execution import ModuleExecutionContext
from app.workflow.registry import ModuleRegistry
from app.domain.workflow_config import WorkflowConfig


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


def _brief_result(store: LocalArtifactStore):
    module = BriefModule()
    context = ModuleExecutionContext(
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
        module_name="brief",
        inputs={
            "topic": "Launch teaser",
            "objective": "Create a concise teaser script",
            "audience": "Early adopters",
            "constraints": ["Keep the pacing fast"],
            "duration_profile": DurationProfile.SIXTY_SECONDS.value,
            "language": "en",
            "tone": "bold",
        },
    )
    return module.execute(context)


def _script_result(store: LocalArtifactStore, brief_payload: dict[str, object]):
    module = ScriptGenerationModule(
        llm_provider=MockLLMProvider("mock"),
        artifact_store=store,
    )
    context = ModuleExecutionContext(
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
        module_name="scriptGeneration",
        inputs={
            "brief": brief_payload,
            "topic": "Launch teaser",
        },
    )
    return module.execute(context)


def _scene_result(store: LocalArtifactStore, script_result):
    module = ScenePlanningModule(artifact_store=store)
    context = ModuleExecutionContext(
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
        module_name="scenePlanning",
        inputs={
            "topic": "Launch teaser",
            "target_platform": TargetPlatform.YOUTUBE_SHORTS.value,
            "aspect_ratio": "9:16",
        },
        module_results={"scriptGeneration": script_result},
    )
    return module.execute(context)


def _voiceover_result(store: LocalArtifactStore, brief_payload: dict[str, object]):
    module = VoiceoverModule(
        tts_provider=MockTTSProvider("mock"),
        artifact_store=store,
    )
    context = ModuleExecutionContext(
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
        module_name="voiceover",
        inputs={
            "brief": brief_payload,
            "voice_config": {"voice": "narrator"},
        },
    )
    return module.execute(context)


def test_captions_module_definition_matches_contract() -> None:
    with _workspace_tempdir("test_t027_definition") as store_root:
        module = CaptionsModule(
            caption_provider=MockCaptionProvider(),
            transcription_provider=MockTranscriptionProvider(),
            artifact_store=LocalArtifactStore(store_root),
        )

        assert module.definition.name == "captions"
        assert module.definition.dependencies == (("voiceover", "scriptGeneration"), ("scenePlanning",))
        assert module.definition.enabled_by_default is False
        assert module.definition.disabled_behavior == "skip"
        assert module.definition.retry_limit == 1
        assert module.definition.artifact_outputs == ("captions.json",)
        assert module.definition.config_schema["properties"]["default_style"]["type"] == "string"


def test_captions_module_generates_caption_track_and_json_artifact() -> None:
    with _workspace_tempdir("test_t027_captions") as store_root:
        store = LocalArtifactStore(store_root)
        brief = _brief_result(store)
        script_result = _script_result(store, brief.output["content_brief"])
        scene_result = _scene_result(store, script_result)
        voiceover_result = _voiceover_result(store, brief.output["content_brief"])
        module = CaptionsModule(
            caption_provider=MockCaptionProvider("mock"),
            transcription_provider=MockTranscriptionProvider("mock"),
            artifact_store=store,
        )
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_1",
            workflow_config_id="workflow_config_1",
            module_name="captions",
            inputs={
                "language": "en",
                "style": "compact",
            },
            module_results={
                "voiceover": voiceover_result,
                "scenePlanning": scene_result,
            },
        )

        result = module.execute(context)
        captions = result.output["captions"]
        caption_track = result.output["caption_track"]
        artifact = result.output["artifact"]
        stored_manifests = store.list_artifacts()

        assert result.status == "completed"
        assert result.output_artifact_ids == ("captions.json",)
        assert result.output["source_kind"] == "voiceover"
        assert captions["provider"] == "mock"
        assert captions["transcription_provider"] == "mock"
        assert captions["scene_plan"]["scene_plan_id"] == scene_result.output["scene_plan"]["scene_plan_id"]
        assert captions["captions_json"][0]["text"].startswith("Captions for")
        assert captions["captions_srt"].startswith(
            "1\r\n00:00:00,000 --> 00:00:02,000\r\n"
        )
        assert caption_track["caption_storage_key"].endswith("captions.json")
        assert caption_track["srt_storage_key"].endswith("captions.en.srt")
        assert artifact["name"] == "captions.json"
        assert artifact["artifact_type"] == "captions"
        assert {manifest.name for manifest in stored_manifests} == {
            "script.txt",
            "script.json",
            "narrative_segments.json",
            "render_scenes.json",
            "scene_plan.json",
            "voiceover.wav",
            "speech_timeline.json",
            "captions.json",
            "captions.en.srt",
        }
        stored_payload = json.loads(store.read_artifact(artifact["storage_key"]).decode("utf-8"))
        assert stored_payload["transcript_ref"].startswith("mock://transcript/")
        assert stored_payload["scene_plan"]["scene_plan_id"] == scene_result.output["scene_plan"]["scene_plan_id"]
        assert stored_payload["captions_json"] == captions["captions_json"]


def test_disabled_captions_module_is_skipped_without_caption_provider_validation() -> None:
    with _workspace_tempdir("test_t027_disabled") as store_root:
        store = LocalArtifactStore(store_root)
        brief_module = BriefModule()
        script_module = ScriptGenerationModule(
            llm_provider=MockLLMProvider("mock"),
            artifact_store=store,
        )
        scene_module = ScenePlanningModule(artifact_store=store)
        voiceover_module = VoiceoverModule(
            tts_provider=MockTTSProvider("mock"),
            artifact_store=store,
        )
        captions_module = CaptionsModule(
            caption_provider=MockCaptionProvider("mock"),
            transcription_provider=MockTranscriptionProvider("mock"),
            artifact_store=store,
        )
        registry = ModuleRegistry(
            [
                brief_module.definition,
                script_module.definition,
                scene_module.definition,
                voiceover_module.definition,
                captions_module.definition,
            ]
        )
        engine = CoreWorkflowEngine(
            registry,
            modules={
                "brief": brief_module,
                "scriptGeneration": script_module,
                "scenePlanning": scene_module,
                "voiceover": voiceover_module,
            },
        )
        workflow_config = WorkflowConfig.create(
            project_id="project_1",
            workflow_preset=WorkflowPreset.SHORT_VIDEO,
            content_type=ContentType.SHORT_VIDEO,
            content_genre=ContentGenre.NEWS,
            duration_profile=DurationProfile.SIXTY_SECONDS,
            target_platform=TargetPlatform.YOUTUBE_SHORTS,
            language="en",
            tone="bold",
            enabled_modules=["brief", "scriptGeneration", "scenePlanning", "voiceover"],
            disabled_modules=["captions"],
            provider_config={
                "LLMProvider": {"providerName": "mock", "enabled": True},
                "TTSProvider": {"providerName": "mock", "enabled": True},
                "AssetProvider": {"providerName": "mock", "enabled": True},
            },
        )
        provider_registry = ProviderRegistry(
            [
                MockLLMProvider("mock"),
                MockTTSProvider("mock"),
                MockAssetProvider("mock"),
            ]
        )
        plan = registry.build_execution_plan(
            ("brief", "scriptGeneration", "scenePlanning", "voiceover", "captions"),
            disabled_modules=("captions",),
        )

        result = engine.run(
            plan,
            workflow_run_id="workflow_run_2",
            workflow_config_id=workflow_config.id,
            workflow_config=workflow_config,
            provider_registry=provider_registry,
            inputs={
                "topic": "Launch teaser",
                "objective": "Create a concise teaser script",
                "audience": "Early adopters",
                "constraints": ["Keep the pacing fast"],
                "duration_profile": DurationProfile.SIXTY_SECONDS.value,
                "language": "en",
                "tone": "bold",
            },
        )

        assert result.status == "completed"
        assert result.module_results["captions"].status == "skipped"
        assert result.module_results["captions"].skipped_reason == "disabled"
        assert "captions.json" not in {manifest.name for manifest in store.list_artifacts()}

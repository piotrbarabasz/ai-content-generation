from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

from app.domain.content_brief import ContentBrief
from app.domain.enums import ContentGenre, ContentType, DurationProfile, TargetPlatform, WorkflowPreset
from app.domain.workflow_config import WorkflowConfig
from app.modules.brief import BriefModule
from app.modules.scene_planning import ScenePlanningModule
from app.modules.script_generation import ScriptGenerationModule
from app.modules.video_rendering import VideoRenderingModule
from app.modules.voiceover import VoiceoverModule
from app.providers.mock_llm import MockLLMProvider
from app.providers.mock_tts import MockTTSProvider
from app.providers.mock_video_renderer import MockVideoRendererProvider
from app.providers.registry import ProviderRegistry
from app.storage.local_store import LocalArtifactStore
from app.workflow.engine import CoreWorkflowEngine
from app.workflow.execution import ModuleExecutionContext
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


def _brief_result(store: LocalArtifactStore):
    brief = ContentBrief.create(
        project_id="project_1",
        topic="Launch teaser",
        objective="Create a launch teaser with a visual hook",
        audience="Early adopters",
        constraints=["Keep the pacing fast"],
        duration_profile=DurationProfile.SIXTY_SECONDS,
        success_criteria=["End with a direct call to action"],
    )
    module = BriefModule()
    context = ModuleExecutionContext(
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
        module_name="brief",
        inputs={"topic": "Launch teaser", "brief": brief},
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


def test_video_rendering_module_definition_matches_contract() -> None:
    with _workspace_tempdir("test_t028_definition") as store_root:
        module = VideoRenderingModule(
            video_renderer_provider=MockVideoRendererProvider("mock"),
            artifact_store=LocalArtifactStore(store_root),
        )

        assert module.definition.name == "videoRendering"
        assert module.definition.dependencies == (("scenePlanning",),)
        assert module.definition.enabled_by_default is False
        assert module.definition.disabled_behavior == "skip"
        assert module.definition.retry_limit == 1
        assert module.definition.artifact_outputs == ("render.mp4",)
        assert module.definition.config_schema["properties"]["default_codec"]["type"] == "string"


def test_video_rendering_module_creates_render_artifact_reference_from_scene_plan() -> None:
    with _workspace_tempdir("test_t028_render") as store_root:
        store = LocalArtifactStore(store_root)
        brief = _brief_result(store)
        script_result = _script_result(store, brief.output["content_brief"])
        scene_result = _scene_result(store, script_result)
        voiceover_result = _voiceover_result(store, brief.output["content_brief"])
        module = VideoRenderingModule(
            video_renderer_provider=MockVideoRendererProvider("mock"),
            artifact_store=store,
        )
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_1",
            workflow_config_id="workflow_config_1",
            module_name="videoRendering",
            inputs={
                "resolution": "1080x1920",
                "fps": 24,
                "codec": "h264",
                "assets": ["mock://asset/intro.png"],
            },
            module_results={
                "scenePlanning": scene_result,
                "voiceover": voiceover_result,
            },
        )

        result = module.execute(context)
        video_render = result.output["video_render"]
        artifact = result.output["artifact"]
        render_request = result.output["render_request"]
        stored_manifests = store.list_artifacts()

        assert result.status == "completed"
        assert result.output_artifact_ids == ("render.mp4",)
        assert video_render["render_storage_key"] == artifact["storage_key"]
        assert video_render["format"] == "mp4"
        assert artifact["name"] == "render.mp4"
        assert artifact["artifact_type"] == "video_render"
        assert artifact["metadata"]["resolution"] == "1080x1920"
        assert render_request["video_ref"].startswith("mock://video/")
        assert render_request["audio_ref"] == voiceover_result.output["voiceover"]["audio_ref"]
        assert render_request["scene_plan"]["scene_plan_id"] == scene_result.output["scene_plan"]["scene_plan_id"]
        assert result.output["assets"] == ["mock://asset/intro.png"]
        assert store.read_artifact(artifact["storage_key"]).decode("utf-8") == render_request["video_ref"]
        assert "render.mp4" in {manifest.name for manifest in stored_manifests}


def test_disabled_video_rendering_module_is_skipped_without_renderer_validation() -> None:
    with _workspace_tempdir("test_t028_disabled") as store_root:
        module = VideoRenderingModule(
            video_renderer_provider=MockVideoRendererProvider("mock"),
            artifact_store=LocalArtifactStore(store_root),
        )
        registry = ModuleRegistry([module.definition])
        engine = CoreWorkflowEngine(registry, modules={})
        workflow_config = WorkflowConfig.create(
            project_id="project_1",
            workflow_preset=WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER,
            content_type=ContentType.LONG_FORM_VIDEO,
            content_genre=ContentGenre.DOCUMENTARY,
            duration_profile=DurationProfile.EIGHT_FIFTEEN_MINUTES,
            target_platform=TargetPlatform.YOUTUBE,
            language="en",
            tone="informative",
            enabled_modules=["brief", "outline", "scriptGeneration", "qa", "export"],
            disabled_modules=["videoRendering"],
            provider_config={},
        )
        provider_registry = ProviderRegistry()
        plan = registry.build_execution_plan(
            ("videoRendering",),
            disabled_modules=("videoRendering",),
        )

        result = engine.run(
            plan,
            workflow_run_id="workflow_run_2",
            workflow_config_id=workflow_config.id,
            workflow_config=workflow_config,
            provider_registry=provider_registry,
            inputs={},
        )

        assert result.status == "skipped"
        assert result.module_results["videoRendering"].status == "skipped"
        assert result.module_results["videoRendering"].skipped_reason == "disabled"
        assert store_root.joinpath(".artifacts").exists()
        assert not any(store_root.glob("**/render.mp4"))

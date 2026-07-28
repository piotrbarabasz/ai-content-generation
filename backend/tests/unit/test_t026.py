from __future__ import annotations

from contextlib import contextmanager
import shutil
from pathlib import Path

from app.domain.content_brief import ContentBrief
from app.domain.enums import DurationProfile
from app.modules.scene_planning import ScenePlanningModule
from app.modules.script_generation import ScriptGenerationModule
from app.providers.mock_llm import MockLLMProvider
from app.storage.local_store import LocalArtifactStore
from app.workflow.execution import ModuleExecutionContext


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


def _script_generation_result(store: LocalArtifactStore):
    brief = ContentBrief.create(
        project_id="project_1",
        topic="Launch teaser",
        objective="Create a launch teaser with a visual hook",
        audience="Early adopters",
        constraints=["Keep the pacing fast"],
        duration_profile=DurationProfile.SIXTY_SECONDS,
        success_criteria=["End with a direct call to action"],
    )
    script_module = ScriptGenerationModule(
        llm_provider=MockLLMProvider("mock"),
        artifact_store=store,
    )
    script_context = ModuleExecutionContext(
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
        module_name="scriptGeneration",
        inputs={"brief": brief, "topic": "Launch teaser"},
    )
    return script_module.execute(script_context)


def test_scene_planning_module_definition_matches_contract() -> None:
    with _workspace_tempdir("test_t026_definition") as store_root:
        module = ScenePlanningModule(artifact_store=LocalArtifactStore(store_root))

        assert module.definition.name == "scenePlanning"
        assert module.definition.dependencies == (("scriptGeneration",),)
        assert module.definition.enabled_by_default is True
        assert module.definition.disabled_behavior == "fail"
        assert module.definition.retry_limit == 1
        assert module.definition.artifact_outputs == ("render_scenes.json", "scene_plan.json")
        assert module.definition.config_schema["properties"]["aspect_ratio"]["type"] == "string"


def test_scene_planning_module_builds_render_scenes_from_script_generation_output() -> None:
    with _workspace_tempdir("test_t026_scene_planning") as store_root:
        store = LocalArtifactStore(store_root)
        script_result = _script_generation_result(store)
        module = ScenePlanningModule(artifact_store=store)
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_1",
            workflow_config_id="workflow_config_1",
            module_name="scenePlanning",
            inputs={
                "topic": "Launch teaser",
                "target_platform": "youtube_shorts",
                "aspect_ratio": "9:16",
            },
            module_results={"scriptGeneration": script_result},
        )

        result = module.execute(context)
        scene_plan = result.output["scene_plan"]
        render_scenes = result.output["render_scenes"]
        scene_plan_artifact = result.output["artifact"]
        render_scenes_artifact = result.output["render_scenes_artifact"]
        stored_manifests = store.list_artifacts()

        assert result.status == "completed"
        assert result.output_artifact_ids == ("render_scenes.json", "scene_plan.json")
        assert result.output["source_kind"] == "brief"
        assert scene_plan["scene_plan_id"] == render_scenes["scene_plan_id"]
        assert scene_plan["scene_count"] == 3
        assert scene_plan["approval_required"] is False
        assert render_scenes["scenes"][0]["scene_plan_id"] == scene_plan["scene_plan_id"]
        assert render_scenes["scenes"][0]["visual_intensity"] == "high"
        assert render_scenes["scenes"][0]["scene_title"] == "Hook"
        assert "role" not in render_scenes["scenes"][0]
        assert "duration_estimate" not in render_scenes["scenes"][0]
        assert scene_plan["scenes"][0]["source_narrative_segment_order"] == 1
        assert scene_plan_artifact["name"] == "scene_plan.json"
        assert render_scenes_artifact["name"] == "render_scenes.json"
        assert {manifest.name for manifest in stored_manifests} == {
            "render_scenes.json",
            "scene_plan.json",
            "script.txt",
            "script.json",
            "narrative_segments.json",
        }
        assert scene_plan["render_scenes_storage_key"] == render_scenes_artifact["storage_key"]


def test_scene_planning_module_pauses_for_scene_plan_approval() -> None:
    with _workspace_tempdir("test_t026_approval") as store_root:
        store = LocalArtifactStore(store_root)
        script_result = _script_generation_result(store)
        module = ScenePlanningModule(artifact_store=store)
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_2",
            workflow_config_id="workflow_config_2",
            module_name="scenePlanning",
            inputs={
                "topic": "Launch teaser",
                "approval_required": True,
            },
            module_results={"scriptGeneration": script_result},
        )

        result = module.execute(context)
        approval_checkpoint = result.output["approval_checkpoint"]

        assert result.status == "waiting_for_approval"
        assert approval_checkpoint["checkpoint_type"] == "scene_plan"
        assert approval_checkpoint["status"] == "pending"
        assert approval_checkpoint["required"] is True
        assert approval_checkpoint["next_stage"] == "videoRendering"
        assert result.output["scene_plan"]["approval_required"] is True

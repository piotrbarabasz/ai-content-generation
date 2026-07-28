from __future__ import annotations

from contextlib import contextmanager
import shutil
from pathlib import Path

from app.domain.content_brief import ContentBrief
from app.domain.enums import DurationProfile
from app.providers.mock_llm import MockLLMProvider
from app.storage.local_store import LocalArtifactStore
from app.workflow.execution import ModuleExecutionContext
from app.modules.script_generation import ScriptGenerationModule


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


def test_script_generation_module_definition_matches_contract() -> None:
    with _workspace_tempdir("test_t025_definition") as store_root:
        module = ScriptGenerationModule(
            llm_provider=MockLLMProvider(),
            artifact_store=LocalArtifactStore(store_root),
        )

        assert module.definition.name == "scriptGeneration"
        assert module.definition.dependencies == (("outline", "dossier", "research", "brief"),)
        assert module.definition.enabled_by_default is True
        assert module.definition.disabled_behavior == "fail"
        assert module.definition.retry_limit == 1
        assert module.definition.artifact_outputs == (
            "script.txt",
            "script.json",
            "narrative_segments.json",
        )
        assert module.definition.config_schema["properties"]["script_name"]["type"] == "string"


def test_script_generation_module_builds_script_and_narrative_segments_from_outline_context() -> None:
    with _workspace_tempdir("test_t025_outline") as store_root:
        store = LocalArtifactStore(store_root)
        module = ScriptGenerationModule(
            llm_provider=MockLLMProvider("mock"),
            artifact_store=store,
        )
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_1",
            workflow_config_id="workflow_config_1",
            module_name="scriptGeneration",
            inputs={
                "outline": {
                    "topic": "Launch teaser",
                    "summary": "Open with an urgent hook. Explain the product value. End with a direct CTA.",
                    "sections": [
                        {"heading": "Hook", "text": "Open with an urgent hook."},
                        {"heading": "Value", "text": "Explain the product value."},
                        {"heading": "CTA", "text": "End with a direct CTA."},
                    ],
                },
                "language": "fr",
                "tone": "bold",
            },
        )

        result = module.execute(context)
        script = result.output["script"]
        narrative_segments = result.output["narrative_segments"]
        script_manifest = result.output["artifact"]
        script_json_manifest = result.output["script_json_artifact"]
        narrative_manifest = result.output["narrative_segments_artifact"]
        stored_manifests = store.list_artifacts()

        assert result.status == "completed"
        assert result.output_artifact_ids == ("script.txt", "script.json", "narrative_segments.json")
        assert result.output["source_kind"] == "outline"
        assert script["language"] == "fr"
        assert script["tone"] == "bold"
        assert script["generation_ref"].startswith("mock-llm:")
        assert script["text"].startswith("Hook: Open with a direct hook about Launch teaser")
        assert len(script["segments"]) == 3
        assert narrative_segments["segments"][0]["title"] == "Hook"
        assert narrative_segments["segments"][0]["role"] == "hook"
        assert "scene_plan_id" not in narrative_segments["segments"][0]
        assert "visual_intensity" not in narrative_segments["segments"][0]
        assert script_manifest["name"] == "script.txt"
        assert script_json_manifest["name"] == "script.json"
        assert narrative_manifest["name"] == "narrative_segments.json"
        assert {manifest.name for manifest in stored_manifests} == {
            "script.txt",
            "script.json",
            "narrative_segments.json",
        }
        assert store.read_artifact(script_manifest["storage_key"]).decode("utf-8").startswith(
            "Hook: Open with a direct hook about Launch teaser"
        )


def test_script_generation_module_uses_brief_context_when_no_outline_exists() -> None:
    with _workspace_tempdir("test_t025_brief") as store_root:
        module = ScriptGenerationModule(
            llm_provider=MockLLMProvider(),
            artifact_store=LocalArtifactStore(store_root),
        )
        brief = ContentBrief.create(
            project_id="project_1",
            topic="Launch teaser",
            objective="Create a concise teaser script",
            audience="Early adopters",
            constraints=["Keep the hook short"],
            duration_profile=DurationProfile.SIXTY_SECONDS,
            success_criteria=["Call to action at the end"],
        )
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_2",
            workflow_config_id="workflow_config_2",
            module_name="scriptGeneration",
            inputs={"brief": brief},
        )

        result = module.execute(context)
        script = result.output["script"]
        segments = result.output["narrative_segments"]["segments"]

        assert result.output["source_kind"] == "brief"
        assert script["topic"] == "Launch teaser"
        assert script["objective"] == "Create a concise teaser script"
        assert script["segments"][0]["text"].startswith("Open with a direct hook about Launch teaser")
        assert len(segments) == 3
        assert segments[1]["role"] == "body"
        assert segments[2]["role"] == "close"

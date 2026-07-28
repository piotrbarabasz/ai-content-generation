from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

from app.modules.post_processing import PostProcessingModule
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


def test_post_processing_module_definition_matches_contract() -> None:
    with _workspace_tempdir("test_t034_definition") as store_root:
        module = PostProcessingModule(artifact_store=LocalArtifactStore(store_root))

        assert module.definition.name == "postProcessing"
        assert module.definition.dependencies == (("scriptGeneration",),)
        assert module.definition.enabled_by_default is True
        assert module.definition.disabled_behavior == "fail"
        assert module.definition.retry_limit == 1
        assert module.definition.artifact_outputs == ("post_processed_script.txt",)
        assert module.definition.config_schema["properties"]["cleanup_rules"]["type"] == "object"


def test_post_processing_module_normalizes_script_and_preserves_source_artifacts() -> None:
    with _workspace_tempdir("test_t034_script_generation") as store_root:
        store = LocalArtifactStore(store_root)
        script_module = ScriptGenerationModule(
            llm_provider=MockLLMProvider("mock"),
            artifact_store=store,
        )
        script_result = script_module.execute(
            ModuleExecutionContext(
                workflow_run_id="workflow_run_1",
                workflow_config_id="workflow_config_1",
                module_name="scriptGeneration",
                inputs={
                    "outline": {
                        "topic": "Launch teaser",
                        "summary": "  Open with an urgent hook.  Explain the product value. End with a direct CTA.  ",
                        "sections": [
                            {"heading": "Hook", "text": "  Open with an urgent hook.  "},
                            {"heading": "Value", "text": "Explain the product value. "},
                            {"heading": "CTA", "text": " End with a direct CTA.  "},
                        ],
                    },
                    "tone": "bold",
                },
            )
        )

        module = PostProcessingModule(artifact_store=store)
        result = module.execute(
            ModuleExecutionContext(
                workflow_run_id="workflow_run_1",
                workflow_config_id="workflow_config_1",
                module_name="postProcessing",
                inputs={
                    "cleanup_rules": {
                        "normalize_punctuation_spacing": True,
                        "collapse_blank_lines": True,
                    }
                },
                module_results={"scriptGeneration": script_result},
            )
        )

        post_processed_script = result.output["post_processed_script"]
        artifact = result.output["artifact"]
        original_script_artifact = result.output["original_script_artifact"]
        normalized_segments = result.output["normalized_segments"]
        stored_cleaned_text = store.read_artifact(artifact["storage_key"]).decode("utf-8")
        original_script_text = store.read_artifact(original_script_artifact["storage_key"]).decode("utf-8")

        assert result.status == "completed"
        assert result.output_artifact_ids == ("post_processed_script.txt",)
        assert post_processed_script["cleaned_text"] == result.output["cleaned_script"]
        assert post_processed_script["original_script"]["text"] == script_result.output["script"]["text"]
        assert post_processed_script["normalized_segments"][0]["title"] == "Hook"
        assert post_processed_script["normalized_segments"][0]["text"].startswith(
            "Open with a direct hook about Launch teaser"
        )
        assert normalized_segments[1]["title"] == "Develop"
        assert normalized_segments[2]["role"] == "close"
        assert artifact["name"] == "post_processed_script.txt"
        assert artifact["artifact_type"] == "post_processed_script"
        assert stored_cleaned_text.startswith("Hook:")
        assert "  " not in stored_cleaned_text
        assert original_script_text == store.read_artifact(script_result.output["artifact"]["storage_key"]).decode("utf-8")
        assert original_script_artifact["name"] == "script.txt"
        assert {manifest.name for manifest in store.list_artifacts()} == {
            "script.txt",
            "script.json",
            "narrative_segments.json",
            "post_processed_script.txt",
        }

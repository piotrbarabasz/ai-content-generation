from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

from app.modules.post_processing import PostProcessingModule
from app.modules.qa import QAModule
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


def test_qa_module_definition_matches_contract() -> None:
    with _workspace_tempdir("test_t035_definition") as store_root:
        module = QAModule(
            llm_provider=MockLLMProvider("mock"),
            artifact_store=LocalArtifactStore(store_root),
        )

        assert module.definition.name == "qa"
        assert module.definition.dependencies == (("postProcessing",),)
        assert module.definition.enabled_by_default is True
        assert module.definition.disabled_behavior == "fail"
        assert module.definition.retry_limit == 1
        assert module.definition.artifact_outputs == ("qa_report.json",)
        assert module.definition.config_schema["properties"]["thresholds"]["type"] == "object"


def test_qa_module_generates_report_and_pending_script_approval_checkpoint() -> None:
    with _workspace_tempdir("test_t035_report") as store_root:
        store = LocalArtifactStore(store_root)
        llm_provider = MockLLMProvider("mock")
        script_module = ScriptGenerationModule(
            llm_provider=llm_provider,
            artifact_store=store,
        )
        script_result = script_module.execute(
            ModuleExecutionContext(
                workflow_run_id="workflow_run_1",
                workflow_config_id="workflow_config_1",
                module_name="scriptGeneration",
                inputs={
                    "outline": {
                        "topic": "Long-form launch",
                        "summary": "Hook the reader, explain the opportunity, and close on the next step.",
                        "sections": [
                            {"heading": "Hook", "text": "Hook the reader."},
                            {"heading": "Value", "text": "Explain the opportunity."},
                            {"heading": "Close", "text": "Close on the next step."},
                        ],
                    },
                    "topic": "Long-form launch",
                },
            )
        )

        post_processing_module = PostProcessingModule(artifact_store=store)
        post_processing_result = post_processing_module.execute(
            ModuleExecutionContext(
                workflow_run_id="workflow_run_1",
                workflow_config_id="workflow_config_1",
                module_name="postProcessing",
                module_results={"scriptGeneration": script_result},
            )
        )

        module = QAModule(
            llm_provider=llm_provider,
            artifact_store=store,
        )
        result = module.execute(
            ModuleExecutionContext(
                workflow_run_id="workflow_run_1",
                workflow_config_id="workflow_config_1",
                module_name="qa",
                enabled_modules=("brief", "outline", "scriptGeneration", "postProcessing", "qa", "voiceover", "export"),
                inputs={
                    "approval_required": True,
                    "quality_thresholds": {
                        "minimum_word_count": 0,
                        "minimum_outline_sections": 1,
                        "minimum_dossier_items": 1,
                    },
                },
                module_results={
                    "postProcessing": post_processing_result,
                    "outline": {
                        "sections": [
                            {"heading": "Hook", "text": "Hook the reader."},
                            {"heading": "Value", "text": "Explain the opportunity."},
                        ],
                        "summary": "Hook the reader, explain the opportunity, and close on the next step.",
                    },
                    "dossier": {
                        "facts": [
                            "Launch timing matters.",
                            "The opportunity is time sensitive.",
                        ],
                        "summary": "Launch timing matters.",
                    },
                },
            )
        )

        qa_report = result.output["qa_report"]
        artifact = result.output["artifact"]
        approval_checkpoint = result.output["approval_checkpoint"]
        stored_report = json.loads(store.read_artifact(artifact["storage_key"]).decode("utf-8"))

        assert result.status == "waiting_for_approval"
        assert result.output_artifact_ids == ("qa_report.json",)
        assert artifact["name"] == "qa_report.json"
        assert artifact["artifact_type"] == "qa_report"
        assert qa_report["module_name"] == "qa"
        assert qa_report["source_kind"] == "postProcessing"
        assert qa_report["quality_status"] == "passed"
        assert qa_report["approval_recommendation"] == "approved"
        assert qa_report["approval_state"] == "pending"
        assert qa_report["score"] == 100
        assert qa_report["checks"][0]["passed"] is True
        assert qa_report["checks"][3]["passed"] is True
        assert approval_checkpoint["checkpoint_type"] == "script"
        assert approval_checkpoint["status"] == "pending"
        assert approval_checkpoint["required"] is True
        assert approval_checkpoint["artifact_id"] == artifact["storage_key"]
        assert approval_checkpoint["next_stage"] == "voiceover"
        assert stored_report["approval_recommendation"] == "approved"
        assert "qa_report.json" in {manifest.name for manifest in store.list_artifacts()}


def test_qa_module_flags_quality_issues_without_forcing_approval() -> None:
    with _workspace_tempdir("test_t035_quality_issues") as store_root:
        store = LocalArtifactStore(store_root)
        module = QAModule(
            llm_provider=MockLLMProvider("mock"),
            artifact_store=store,
        )

        result = module.execute(
            ModuleExecutionContext(
                workflow_run_id="workflow_run_2",
                workflow_config_id="workflow_config_2",
                module_name="qa",
                inputs={
                    "script_text": "TODO write the final script.",
                    "approval_required": False,
                    "quality_thresholds": {
                        "minimum_word_count": 10,
                        "minimum_outline_sections": 1,
                    },
                },
                module_results={
                    "outline": {"sections": []},
                    "dossier": {"facts": []},
                },
            )
        )

        qa_report = result.output["qa_report"]
        artifact = result.output["artifact"]
        stored_report = json.loads(store.read_artifact(artifact["storage_key"]).decode("utf-8"))

        assert result.status == "completed"
        assert "approval_checkpoint" not in result.output
        assert qa_report["quality_status"] == "needs_changes"
        assert qa_report["approval_recommendation"] == "changes_requested"
        assert qa_report["approval_state"] == "not_required"
        assert qa_report["checks"][0]["passed"] is True
        assert qa_report["checks"][3]["passed"] is False
        assert stored_report["quality_status"] == "needs_changes"

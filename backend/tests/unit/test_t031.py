from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

from app.domain.content_brief import ContentBrief
from app.domain.enums import DurationProfile
from app.modules.research import ResearchModule
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


def test_research_module_definition_matches_contract() -> None:
    with _workspace_tempdir("test_t031_definition") as store_root:
        module = ResearchModule(
            llm_provider=MockLLMProvider("mock"),
            artifact_store=LocalArtifactStore(store_root),
        )

        assert module.definition.name == "research"
        assert module.definition.dependencies == ()
        assert module.definition.enabled_by_default is False
        assert module.definition.disabled_behavior == "skip"
        assert module.definition.retry_limit == 1
        assert module.definition.artifact_outputs == ("research.json",)
        assert module.definition.config_schema["properties"]["allow_research"]["type"] == "boolean"


def test_research_module_builds_research_artifact_and_links_run_and_job() -> None:
    with _workspace_tempdir("test_t031_enabled") as store_root:
        store = LocalArtifactStore(store_root)
        module = ResearchModule(
            llm_provider=MockLLMProvider("mock"),
            artifact_store=store,
        )
        brief = ContentBrief.create(
            project_id="project_1",
            topic="Launch teaser",
            objective="Clarify what makes the launch memorable",
            audience="Early adopters",
            constraints=["Keep the research concise"],
            duration_profile=DurationProfile.SIXTY_SECONDS,
            success_criteria=["Provide angles for a dossier"],
        )
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_1",
            workflow_config_id="workflow_config_1",
            module_name="research",
            inputs={
                "allow_research": True,
                "brief": brief,
                "topic": "Launch teaser",
                "source_manifest": [
                    {
                        "source_id": "source_1",
                        "title": "Customer interview",
                        "summary": "Fast pacing is the main demand.",
                    },
                    {
                        "source_id": "source_2",
                        "title": "Competitive scan",
                        "summary": "Competitors emphasize clarity over novelty.",
                    },
                ],
                "workflow_run": {
                    "id": "workflow_run_1",
                    "workflow_config_id": "workflow_config_1",
                    "status": "running",
                    "current_stage": "research",
                },
                "generation_job": {
                    "id": "generation_job_1",
                    "workflow_run_id": "workflow_run_1",
                    "module_name": "research",
                    "status": "running",
                    "attempt": 1,
                    "retry_count": 0,
                },
            },
        )

        result = module.execute(context)
        research = result.output["research"]
        artifact = result.output["artifact"]
        workflow_run = result.output["workflow_run"]
        generation_job = result.output["generation_job"]
        stored_payload = json.loads(store.read_artifact(artifact["storage_key"]).decode("utf-8"))

        assert result.status == "completed"
        assert result.output_artifact_ids == ("research.json",)
        assert result.output["source_kind"] == "topic"
        assert research["topic"] == "Launch teaser"
        assert research["source_summary"].startswith("Fast pacing is the main demand.")
        assert len(research["research_notes"]) == 2
        assert research["research_ref"].startswith("mock-llm:")
        assert workflow_run["id"] == "workflow_run_1"
        assert workflow_run["artifact_ids"] == ["research.json"]
        assert generation_job["id"] == "generation_job_1"
        assert generation_job["output_artifact_ids"] == ["research.json"]
        assert artifact["name"] == "research.json"
        assert artifact["artifact_type"] == "research"
        assert stored_payload["workflow_run"]["id"] == "workflow_run_1"
        assert stored_payload["generation_job"]["workflow_run_id"] == "workflow_run_1"
        assert stored_payload["research_notes"][0]["title"] == "Customer interview"
        assert {manifest.name for manifest in store.list_artifacts()} == {"research.json"}


def test_research_module_skips_cleanly_when_disabled() -> None:
    with _workspace_tempdir("test_t031_disabled") as store_root:
        store = LocalArtifactStore(store_root)
        module = ResearchModule(
            llm_provider=MockLLMProvider(),
            artifact_store=store,
        )
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_2",
            workflow_config_id="workflow_config_2",
            module_name="research",
            inputs={
                "allow_research": False,
                "topic": "Launch teaser",
                "source_manifest": [
                    {"title": "Ignored source", "summary": "This should not be written."}
                ],
            },
        )

        result = module.execute(context)

        assert result.status == "skipped"
        assert result.skipped_reason == "disabled"
        assert result.output == {}
        assert result.output_artifact_ids == ()
        assert store.list_artifacts() == ()

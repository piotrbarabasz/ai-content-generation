from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

from app.domain.content_brief import ContentBrief
from app.domain.enums import DurationProfile
from app.modules.dossier import DossierModule
from app.modules.outline import OutlineModule
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


def _brief() -> ContentBrief:
    return ContentBrief.create(
        project_id="project_1",
        topic="Launch teaser",
        objective="Create a long-form outline that leads into the script",
        audience="Early adopters",
        constraints=["Keep the pacing focused"],
        duration_profile=DurationProfile.EIGHT_FIFTEEN_MINUTES,
        success_criteria=["Provide a clear hook and close"],
    )


def _research_result(store: LocalArtifactStore):
    module = ResearchModule(
        llm_provider=MockLLMProvider("mock"),
        artifact_store=store,
    )
    context = ModuleExecutionContext(
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
        module_name="research",
        inputs={
            "allow_research": True,
            "brief": _brief(),
            "topic": "Launch teaser",
            "source_manifest": [
                {
                    "source_id": "source_1",
                    "title": "Customer interview",
                    "summary": "The launch needs a faster hook.",
                },
                {
                    "source_id": "source_2",
                    "title": "Positioning note",
                    "summary": "The outline should escalate from context to payoff.",
                },
            ],
        },
    )
    return module.execute(context)


def _dossier_result(store: LocalArtifactStore, research_result):
    module = DossierModule(artifact_store=store)
    context = ModuleExecutionContext(
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
        module_name="dossier",
        inputs={
            "allow_dossier": True,
            "research": research_result.output["research"],
        },
        module_results={"research": research_result},
    )
    return module.execute(context)


def test_outline_module_definition_matches_contract() -> None:
    with _workspace_tempdir("test_t033_definition") as store_root:
        module = OutlineModule(artifact_store=LocalArtifactStore(store_root))

        assert module.definition.name == "outline"
        assert module.definition.dependencies == (("dossier", "research", "brief"),)
        assert module.definition.enabled_by_default is True
        assert module.definition.disabled_behavior == "fail"
        assert module.definition.retry_limit == 1
        assert module.definition.artifact_outputs == ("outline.json",)
        assert module.definition.config_schema["properties"]["scene_count"]["type"] == "integer"


def test_outline_module_builds_outline_artifact_from_research_and_dossier_context() -> None:
    with _workspace_tempdir("test_t033_dossier") as store_root:
        store = LocalArtifactStore(store_root)
        research_result = _research_result(store)
        dossier_result = _dossier_result(store, research_result)
        module = OutlineModule(artifact_store=store)
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_1",
            workflow_config_id="workflow_config_1",
            module_name="outline",
            inputs={
                "brief": _brief(),
                "duration_profile": DurationProfile.EIGHT_FIFTEEN_MINUTES.value,
                "scene_count": 5,
            },
            module_results={
                "research": research_result,
                "dossier": dossier_result,
            },
        )

        result = module.execute(context)
        outline = result.output["outline"]
        narrative_outline = result.output["narrative_outline"]
        scene_outline = result.output["scene_outline"]
        artifact = result.output["artifact"]
        workflow_run = result.output["workflow_run"]
        generation_job = result.output["generation_job"]
        stored_payload = json.loads(store.read_artifact(artifact["storage_key"]).decode("utf-8"))

        assert result.status == "completed"
        assert result.output_artifact_ids == ("outline.json",)
        assert result.output["source_kind"] == "dossier"
        assert outline["topic"] == "Launch teaser"
        assert outline["duration_profile"] == DurationProfile.EIGHT_FIFTEEN_MINUTES.value
        assert outline["scene_count"] == 5
        assert len(outline["sections"]) == 5
        assert outline["sections"][0]["heading"] == "Hook"
        assert outline["sections"][0]["text"].startswith("Open with a direct hook about Launch teaser")
        assert outline["scene_outline"][0]["transition"] == "open"
        assert narrative_outline["outline_id"] == outline["outline_id"]
        assert narrative_outline["sections"][1]["heading"] == "Setup"
        assert scene_outline[0]["visual_intensity"] == "high"
        assert workflow_run["artifact_ids"] == ["outline.json"]
        assert generation_job["module_name"] == "outline"
        assert generation_job["output_artifact_ids"] == ["outline.json"]
        assert artifact["name"] == "outline.json"
        assert artifact["artifact_type"] == "outline"
        assert stored_payload["outline"]["topic"] == "Launch teaser"
        assert stored_payload["scene_outline"][2]["title"] == "Development"
        assert {manifest.name for manifest in store.list_artifacts()} == {"outline.json", "research.json", "dossier.json"}


def test_outline_module_falls_back_to_topic_and_brief_context() -> None:
    with _workspace_tempdir("test_t033_topic") as store_root:
        store = LocalArtifactStore(store_root)
        module = OutlineModule(artifact_store=store)
        brief = _brief()
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_2",
            workflow_config_id="workflow_config_2",
            module_name="outline",
            inputs={
                "topic": "Launch teaser",
                "brief": brief,
                "sceneCount": 4,
            },
        )

        result = module.execute(context)
        outline = result.output["outline"]
        artifact = result.output["artifact"]

        assert result.output["source_kind"] == "topic"
        assert outline["topic"] == "Launch teaser"
        assert outline["scene_count"] == 4
        assert len(outline["sections"]) == 4
        assert outline["sections"][-1]["heading"] == "Turning Point"
        assert outline["outline_text"].startswith("Outline for Launch teaser")
        assert artifact["name"] == "outline.json"
        assert {manifest.name for manifest in store.list_artifacts()} == {"outline.json"}

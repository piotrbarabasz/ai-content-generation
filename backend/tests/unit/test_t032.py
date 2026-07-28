from __future__ import annotations

from contextlib import contextmanager
import shutil
from pathlib import Path

from app.modules.dossier import DossierModule
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


def _research_result() -> ModuleResult:
    return ModuleResult(
        module_name="research",
        status="completed",
        output_artifact_ids=("research.json",),
        output={
            "research": {
                "research_id": "research_1",
                "workflow_run_id": "workflow_run_1",
                "workflow_config_id": "workflow_config_1",
                "module_name": "research",
                "topic": "Launch teaser",
                "source_kind": "topic",
                "source_summary": "Alice Johnson leads the launch in Berlin.",
                "research_ref": "research_ref_1",
                "research_notes": [
                    {
                        "order": 1,
                        "title": "Alice Johnson",
                        "summary": "Alice Johnson leads the launch in Berlin.",
                        "source_ref": "source_1",
                        "kind": "interview",
                        "people": ["Alice Johnson"],
                        "places": ["Berlin"],
                        "status": "confirmed",
                    },
                    {
                        "order": 2,
                        "title": "Launch date",
                        "summary": "A follow-up note flags a disputed launch date.",
                        "source_ref": "source_2",
                        "status": "disputed",
                    },
                ],
                "dossier_context": {
                    "recommended_angle": "Focus on the launch timeline and team lead.",
                    "source_count": 2,
                },
            }
        },
    )


def test_dossier_module_definition_matches_contract() -> None:
    with _workspace_tempdir("test_t032_definition") as store_root:
        module = DossierModule(artifact_store=LocalArtifactStore(store_root))

        assert module.definition.name == "dossier"
        assert module.definition.dependencies == (("research",),)
        assert module.definition.enabled_by_default is False
        assert module.definition.disabled_behavior == "skip"
        assert module.definition.retry_limit == 1
        assert module.definition.artifact_outputs == ("dossier.json",)
        assert module.definition.config_schema["properties"]["detail_level"]["type"] == "string"


def test_dossier_module_builds_dossier_artifact_from_research_output() -> None:
    with _workspace_tempdir("test_t032_enabled") as store_root:
        store = LocalArtifactStore(store_root)
        module = DossierModule(artifact_store=store)
        research_result = _research_result()
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_1",
            workflow_config_id="workflow_config_1",
            module_name="dossier",
            inputs={"allow_dossier": True, "research": research_result.output["research"]},
            module_results={"research": research_result},
        )

        result = module.execute(context)
        dossier = result.output["dossier"]
        artifact = result.output["artifact"]
        workflow_run = result.output["workflow_run"]
        generation_job = result.output["generation_job"]
        stored_payload = store.read_artifact(artifact["storage_key"]).decode("utf-8")

        assert result.status == "completed"
        assert result.output_artifact_ids == ("dossier.json",)
        assert result.output["source_kind"] == "topic"
        assert dossier["topic"] == "Launch teaser"
        assert dossier["research_ref"] == "research_ref_1"
        assert result.output["key_people"] == ["Alice Johnson"]
        assert result.output["key_places"] == ["Berlin"]
        assert result.output["confirmed_facts"][0] == "Alice Johnson leads the launch in Berlin."
        assert result.output["disputed_facts"] == ["A follow-up note flags a disputed launch date."]
        assert result.output["timeline"][0]["order"] == 1
        assert result.output["timeline"][1]["status"] == "disputed"
        assert workflow_run["id"] == "workflow_run_1"
        assert generation_job["module_name"] == "dossier"
        assert artifact["name"] == "dossier.json"
        assert artifact["artifact_type"] == "dossier"
        assert "\"dossier_id\"" in stored_payload
        assert {manifest.name for manifest in store.list_artifacts()} == {"dossier.json"}


def test_dossier_module_skips_cleanly_when_disabled() -> None:
    with _workspace_tempdir("test_t032_disabled") as store_root:
        store = LocalArtifactStore(store_root)
        module = DossierModule(artifact_store=store)
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_2",
            workflow_config_id="workflow_config_2",
            module_name="dossier",
            inputs={"allow_dossier": False, "research": _research_result().output["research"]},
        )

        result = module.execute(context)

        assert result.status == "skipped"
        assert result.skipped_reason == "disabled"
        assert result.output == {}
        assert result.output_artifact_ids == ()
        assert store.list_artifacts() == ()

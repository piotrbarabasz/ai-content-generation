from __future__ import annotations

import pytest

from app.domain.content_brief import ContentBrief
from app.workflow.execution import ModuleExecutionContext
from app.modules.brief import BriefModule


def test_brief_module_definition_matches_contract() -> None:
    module = BriefModule()

    assert module.definition.name == "brief"
    assert module.definition.dependencies == ()
    assert module.definition.enabled_by_default is True
    assert module.definition.disabled_behavior == "skip"
    assert module.definition.retry_limit == 1
    assert module.definition.artifact_outputs == ("brief.json",)
    assert module.definition.config_schema["properties"]["default_language"]["type"] == "string"


def test_brief_module_normalizes_topic_and_brief_inputs() -> None:
    module = BriefModule()
    source_brief = ContentBrief.create(
        project_id="project_1",
        topic="Legacy topic",
        objective="Old objective",
        audience="Existing audience",
        constraints=["Keep the hook short"],
        duration_profile="60s",
        success_criteria=["Old success"],
    )
    context = ModuleExecutionContext(
        workflow_run_id="workflow_run_1",
        workflow_config_id="workflow_config_1",
        module_name="brief",
        inputs={
            "brief": source_brief,
            "topic": "  New topic for the run  ",
            "objective": "Create a focused creative brief",
            "audience": "Marketing leads",
            "constraints": [" Keep it concise ", " Mention the CTA "],
            "success_criteria": ["Keep the output actionable"],
            "duration_profile": "15_30s",
            "language": "en",
            "tone": "confident",
        },
    )

    result = module.execute(context)
    payload = result.output["content_brief"]

    assert result.status == "completed"
    assert result.output_artifact_ids == ("brief.json",)
    assert result.output["source_kind"] == "topic"
    assert payload["topic"] == "New topic for the run"
    assert payload["objective"] == "Create a focused creative brief"
    assert payload["audience"] == "Marketing leads"
    assert payload["constraints"] == ["Keep it concise", "Mention the CTA"]
    assert payload["duration_profile"] == "15_30s"
    assert payload["success_criteria"] == ["Keep the output actionable"]
    assert result.output["artifact"]["name"] == "brief.json"
    assert result.output["artifact"]["artifact_type"] == "brief"
    assert result.output["workflow_snapshot"]["workflow_run_id"] == "workflow_run_1"


def test_brief_module_uses_transcript_fallback_and_defaults() -> None:
    module = BriefModule(default_language="fr", default_tone="thoughtful")
    context = ModuleExecutionContext(
        workflow_run_id="workflow_run_2",
        workflow_config_id="workflow_config_2",
        module_name="brief",
        inputs={
            "transcript": "This short transcript opens with a strong hook. It continues with more context.",
        },
    )

    result = module.execute(context)
    payload = result.output["content_brief"]

    assert result.output["source_kind"] == "transcript"
    assert payload["topic"] == "This short transcript opens with a strong hook"
    assert payload["language"] == "fr"
    assert payload["tone"] == "thoughtful"
    assert payload["objective"] == "Normalize the supplied transcript into a content brief about This short transcript opens with a strong hook."
    assert payload["constraints"] == [
        "language=fr",
        "tone=thoughtful",
        "duration_profile=60s",
        "source_kind=transcript",
    ]
    assert payload["success_criteria"] == [
        "Keep the brief focused on This short transcript opens with a strong hook.",
        "Preserve the user's intent in a structured format.",
    ]


def test_brief_module_requires_topic_brief_or_transcript_input() -> None:
    module = BriefModule()
    context = ModuleExecutionContext(
        workflow_run_id="workflow_run_3",
        workflow_config_id="workflow_config_3",
        module_name="brief",
        inputs={},
    )

    with pytest.raises(ValueError, match="BriefModule topic, brief or transcript is required"):
        module.execute(context)

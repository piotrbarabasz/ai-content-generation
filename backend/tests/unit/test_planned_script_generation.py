import json

import pytest

from app.application.planned_script_generation import PlanScriptGenerationService
from app.domain.base import new_id
from app.domain.narrative_segment import SectionRevision
from app.domain.plan_script import planned_section_identity
from app.domain.planned_script_schema import validate_planned_script_group
from app.domain.planned_script_schema import planned_script_group_schema
from app.domain.narration_quality import narration_issues


def test_v2_fixed_slots_ignore_object_order_and_never_accept_model_ids():
    schema = planned_script_group_schema(2)
    assert schema["$id"].endswith(":v2")
    assert schema["properties"]["sections"]["required"] == ["section_1", "section_2"]
    assert validate_planned_script_group({"sections": {"section_2": "Second narration.",
        "section_1": "First narration."}}, ("opaque-a", "opaque-b"), version=2) == ("First narration.", "Second narration.")


@pytest.mark.parametrize("sections", [
    {}, {"section_1": "First"}, {"section_1": "First", "section_2": "Second", "section_3": "Extra"},
    {"section_1": "First", "wrong-id": "Second"},
    {"section_1": {"planned_section_id": "replacement", "text": "First"}, "section_2": "Second"},
    {"section_1": "First", "section_2": " "}, {"section_1": "Same text", "section_2": "same  text"},
    [{"planned_section_id": "opaque-a", "text": "First"}],
])
def test_v2_rejects_missing_extra_incompatible_empty_duplicate_and_v1_payload(sections):
    with pytest.raises(ValueError):
        validate_planned_script_group({"sections": sections}, ("opaque-a", "opaque-b"), version=2)


@pytest.mark.parametrize("text", [
    "Framing the Question — Pose the central counterfactual question for the audience.",
    "Winter Crossing — Immersive opening showing exhausted troops and the peril of the crossing.",
    "Winter Crossing, The River in Darkness — Immersive opening showing exhausted troops.",
    "Explain how the army reached the river.", "Show the audience the stakes.",
    "# Winter Crossing", "Narrator: Washington approached the river.", "[Cut to the river] The army waited.",
    "Winter Crossing", "...",
])
def test_clear_instructional_content_is_not_narration(text):
    title = "Framing the Question" if text.startswith("Framing") else "Winter Crossing"
    assert narration_issues(text, title=title)


@pytest.mark.parametrize("text", [
    "What if Washington had failed that night?", "Imagine standing beside the river in December.",
    'The order was clear: "Show the audience no mercy," the actor recalled.',
    "Explain how? No one knew. The evidence had vanished.",
    "The soldiers were exhausted, and the river lay dark before them.",
])
def test_natural_narration_questions_quotations_and_viewer_imperatives_pass(text):
    assert not narration_issues(text, title="Winter Crossing")


def test_deterministic_plan_section_identity_keeps_new_revision_identity_random():
    first = SectionRevision.create_for_plan(project_id="project", planned_section_id="plan-section",
                                            title="Title", role="body", text="First draft")
    second = SectionRevision.create_for_plan(project_id="project", planned_section_id="plan-section",
                                             title="Title", role="body", text="Second draft")
    assert first.section_id == second.section_id == planned_section_identity("project", "plan-section")
    assert first.id != second.id
    assert second.revise(text="Manual edit").section_id == first.section_id


@pytest.mark.parametrize("payload", [
    {"sections": []},
    {"sections": [{"planned_section_id": "a", "text": "ok"}, {"planned_section_id": "a", "text": "ok"}]},
    {"sections": [{"planned_section_id": "b", "text": "ok"}]},
    {"sections": [{"planned_section_id": "a", "text": "ok", "extra": "no"}]},
])
def test_strict_group_output_rejects_missing_duplicate_extra_and_unknown_fields(payload):
    with pytest.raises(ValueError):
        validate_planned_script_group(payload, ("a",))

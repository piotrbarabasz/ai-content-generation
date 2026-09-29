import json

import pytest

from app.application.planned_script_generation import PlanScriptGenerationService
from app.domain.base import new_id
from app.domain.narrative_segment import SectionRevision
from app.domain.plan_script import planned_section_identity
from app.domain.planned_script_schema import validate_planned_script_group


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

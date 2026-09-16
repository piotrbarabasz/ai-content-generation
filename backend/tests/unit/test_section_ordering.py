"""D039 pure ordering, D005 impact and repository-neutral command coverage."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.application.section_ordering import SectionOrderingService
from app.domain.base import DomainValidationError
from app.domain.narrative_segment import SectionRevision
from app.domain.script import ScriptRevision
from app.domain.section_order import PROJECT_OUTPUTS, reorder_script


def script():
    sections = tuple(SectionRevision.create(project_id="project", title=name, text=f"{name} text", role="body")
                     for name in "ABC")
    return ScriptRevision.create(project_id="project", sections=sections)


def test_reorder_retains_exact_section_revisions_and_exposes_only_project_rebuilds():
    original = script()
    result = reorder_script(original, [original.sections[2].section_id, original.sections[0].section_id,
                                       original.sections[1].section_id])

    assert result.script.sections == (original.sections[2], original.sections[0], original.sections[1])
    assert result.script.parent_revision_id == original.id
    assert tuple(section.section_id for section in original.sections) == tuple(
        impact_id for impact_id in result.impact.previous_order
    )
    assert result.impact.reusable_section_ids == result.impact.section_order
    assert result.impact.project_outputs == PROJECT_OUTPUTS
    assert result.impact.order_fingerprint != reorder_script(original, [section.section_id for section in original.sections]).impact.order_fingerprint
    metadata = result.to_metadata()
    metadata["invalidation"]["section_order"].clear()
    assert result.impact.section_order == tuple(section.section_id for section in result.script.sections)
    json.dumps(result.to_metadata())


@pytest.mark.parametrize("order", [[], ["missing"], ["A", "A", "B"], "ABC", None])
def test_invalid_complete_permutation_cannot_change_parent(order):
    original = script()
    before = original.sections
    with pytest.raises(DomainValidationError):
        reorder_script(original, order)
    assert original.sections == before


def test_unchanged_order_is_a_no_write_no_invalidation_result():
    original = script()
    result = reorder_script(original, [section.section_id for section in original.sections])
    assert result.script is original
    assert not result.impact.changed
    assert result.impact.project_outputs == ()
    assert result.impact.parent_script_revision_id == result.impact.script_revision_id == original.id


class Repository:
    def __init__(self, current):
        self.current = current
        self.saved = []

    def active_script(self):
        return self.current

    def save_and_select(self, revision, *, expected_active_revision_id):
        self.saved.append((revision, expected_active_revision_id))
        self.current = revision


def test_service_requires_current_token_and_saves_exactly_one_changed_snapshot():
    repository = Repository(script())
    service = SectionOrderingService(repository)
    ids = [section.section_id for section in repository.current.sections]

    with pytest.raises(DomainValidationError, match="Expected active"):
        service.reorder(ids, expected_active_revision_id="")
    with pytest.raises(DomainValidationError, match="refresh"):
        service.reorder(ids, expected_active_revision_id="stale")
    assert repository.saved == []

    result = service.reorder(list(reversed(ids)), expected_active_revision_id=repository.current.id)
    assert repository.saved == [(result.script, result.impact.parent_script_revision_id)]
    no_op = service.reorder([section.section_id for section in result.script.sections],
                            expected_active_revision_id=result.script.id)
    assert not no_op.impact.changed and len(repository.saved) == 1


def test_ordering_module_does_not_import_storage_or_provider_code():
    code = """
import sys
from app.domain.section_order import reorder_script
for name in ('sqlite3', 'app.storage', 'app.providers', 'app.tts', 'PySide6', 'fastapi', 'torch'):
    assert name not in sys.modules, name
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr

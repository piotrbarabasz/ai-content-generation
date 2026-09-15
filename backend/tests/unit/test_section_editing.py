"""D038 pure topology, exact source lineage and invalidation metadata."""

from dataclasses import FrozenInstanceError, replace
import json
import subprocess
import sys

import pytest

from app.application.invalidation import Freshness, evaluate_freshness
from app.application.section_editing import SectionEditingService
from app.domain.base import DomainValidationError
from app.domain.dependencies import ArtifactDependency, DependencyDeclaration, InputEdge, RequestFingerprint, content_fingerprint
from app.domain.narrative_segment import SectionRevision
from app.domain.section_edit import (PROJECT_OUTPUTS, SECTION_OUTPUTS, SectionEditResult,
                                     describe_section_edit, merge_script, split_script)
from app.domain.script import ScriptRevision


def section(name, text=None, role="body"):
    return SectionRevision(name + "1", name, "project", "Title " + name, text or name + " text.", role)


def script(*sections):
    return ScriptRevision("script1", "script", "project", tuple(sections), "pl")


def test_split_preserves_exact_text_unaffected_revisions_and_source_ranges():
    parent = script(section("A"), section("B", "  Lewa część.\n\nPrawa część.  "), section("C"))
    boundary = parent.section("B").text.index("Prawa")
    result = split_script(parent, "B", boundary, left_title="Lewa", right_title="Prawa")
    left, right = result.script.sections[1:3]
    assert left.text + right.text == parent.section("B").text
    assert (left.text, right.text) == ("  Lewa część.\n\n", "Prawa część.  ")
    assert (left.title, right.title) == ("Lewa", "Prawa")
    assert left.section_id != right.section_id and {left.section_id, right.section_id}.isdisjoint({"A", "B", "C"})
    assert result.script.sections[0] is parent.sections[0] and result.script.sections[-1] is parent.sections[-1]
    assert result.script.parent_revision_id == parent.id and parent.sections == (section("A"), section("B", "  Lewa część.\n\nPrawa część.  "), section("C"))
    assert [(s.sources[0].start, s.sources[0].end) for s in result.lineages] == [(0, boundary), (boundary, len(parent.section("B").text))]
    assert all(s.sources[0].revision_id == "B1" for s in result.lineages)


def test_split_impact_marks_new_work_and_reuses_only_unchanged_sections():
    parent = script(section("A"), section("B", "one two"), section("C"))
    result = split_script(parent, "B", 4)
    impact = result.impact
    assert impact.operation == "split" and impact.retired_section_ids == ("B",)
    assert impact.reusable_section_ids == ("A", "C") and impact.project_outputs == PROJECT_OUTPUTS
    assert len(impact.created) == 2
    for changed, revision in zip(impact.created, result.script.sections[1:3]):
        assert changed.outputs == SECTION_OUTPUTS
        assert changed.text_fingerprint == content_fingerprint(revision.text)


@pytest.mark.parametrize("boundary", [0, 1, 2, 6, 7, -1, 99, True, 2.5])
def test_invalid_boundary_or_whitespace_side_fails_without_mutating_parent(boundary):
    parent = script(section("B", "  abc  "))
    before = parent
    with pytest.raises(DomainValidationError):
        split_script(parent, "B", boundary)
    assert parent == before


def test_split_at_python_text_offset_preserves_unicode_codepoints():
    parent = script(section("B", "Zażółć 🐍 gęślą"))
    boundary = parent.sections[0].text.index("🐍")
    result = split_script(parent, "B", boundary)
    assert "".join(s.text for s in result.script.sections) == parent.sections[0].text
    assert result.lineages[1].sources[0].start == boundary


def test_merge_two_or_many_adjacent_sections_retains_complete_ordered_sources():
    parent = script(section("A"), section("B", "B text"), section("C", "C text"), section("D"))
    result = merge_script(parent, ["B", "C"], separator=" | ", title="BC")
    merged = result.script.sections[1]
    assert merged.text == "B text | C text" and merged.title == "BC" and merged.role == "body"
    assert result.script.sections[0] is parent.sections[0] and result.script.sections[2] is parent.sections[3]
    assert [(s.section_id, s.revision_id, s.start, s.end) for s in result.lineages[0].sources] == [
        ("B", "B1", 0, 6), ("C", "C1", 0, 6)]
    assert result.impact.retired_section_ids == ("B", "C")
    assert result.impact.reusable_section_ids == ("A", "D")
    three = merge_script(parent, ["A", "B", "C"], separator="")
    assert three.script.sections[0].text == "A text.B textC text"
    assert len(three.lineages[0].sources) == 3


def test_different_roles_require_an_explicit_result_role():
    parent = script(section("A", role="hook"), section("B", role="body"))
    with pytest.raises(DomainValidationError, match="explicit result role"):
        merge_script(parent, ["A", "B"])
    result = merge_script(parent, ["A", "B"], role="body")
    assert result.script.sections[0].role == "body"


@pytest.mark.parametrize("ids", [["A"], ["A", "A"], ["A", "C"], ["B", "A"], ["A", "missing"], "AB", [], None])
def test_invalid_merge_selection_fails(ids):
    parent = script(section("A"), section("B"), section("C"))
    with pytest.raises(DomainValidationError):
        merge_script(parent, ids)


def test_lineage_and_impact_are_deeply_immutable():
    result = split_script(script(section("B", "left right")), "B", 5)
    for value, field, changed in ((result, "script", None), (result.lineages[0], "section_id", "x"),
                                  (result.lineages[0].sources[0], "start", 2),
                                  (result.impact.created[0], "outputs", ())):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, changed)
    metadata = result.to_metadata()
    assert json.loads(json.dumps(metadata)) == metadata
    assert metadata["invalidation"]["created"][0]["outputs"] == list(SECTION_OUTPUTS)
    metadata["lineages"][0]["sources"][0]["start"] = 99
    assert result.lineages[0].sources[0].start == 0


@pytest.mark.parametrize("operation", ["split", "merge"])
def test_describe_reconstructs_the_same_durable_lineage(operation):
    parent = script(section("A"), section("B", "left right"), section("C"))
    result = split_script(parent, "B", 5) if operation == "split" else merge_script(parent, ["A", "B"], separator=" // ")
    assert describe_section_edit(parent, result.script) == result


@pytest.mark.parametrize("kind", ["parent", "script", "project", "language", "edit", "reorder", "position", "partial-merge"])
def test_describe_rejects_non_split_merge_snapshot_changes(kind):
    parent = script(section("A"), section("B", "left right"), section("C"))
    result = split_script(parent, "B", 5).script
    if kind == "parent": result = replace(result, parent_revision_id="other")
    elif kind == "script": result = replace(result, script_id="other")
    elif kind == "project": result = replace(result, project_id="other", sections=tuple(replace(s, project_id="other") for s in result.sections))
    elif kind == "language": result = replace(result, language="en")
    elif kind == "edit": result = parent.edit_section("B", text="changed")
    elif kind == "reorder": result = parent.reorder(["C", "B", "A"])
    elif kind == "position": result = replace(result, sections=(result.sections[0], result.sections[3], *result.sections[1:3]))
    else:
        merged = merge_script(parent, ["A", "B"]).script.sections[0]
        result = replace(merge_script(parent, ["A", "B"]).script, sections=(replace(merged, text="A text.\n\npartial"), parent.sections[2]))
    with pytest.raises(DomainValidationError):
        describe_section_edit(parent, result)


def test_impact_fingerprints_drive_D005_missing_work_while_unaffected_stays_fresh():
    parent = script(section("A"), section("B", "left right"), section("C"))
    result = split_script(parent, "B", 5)
    requests, selected, artifacts = {}, {}, {}
    sources = {f"section:{s.section_id}:text": content_fingerprint(s.text) for s in result.script.sections}
    for section_value in result.script.sections:
        key = f"section:{section_value.section_id}:raw_audio"
        request = RequestFingerprint.create("tts", "1", inputs=[InputEdge("text", f"section:{section_value.section_id}:text",
                                                                           sources[f"section:{section_value.section_id}:text"])])
        requests[key] = request
        if section_value.section_id in result.impact.reusable_section_ids:
            artifact = ArtifactDependency("artifact-" + section_value.section_id, content_fingerprint(section_value.text),
                                          DependencyDeclaration(key, request))
            selected[key] = artifact.artifact_id
            artifacts[artifact.artifact_id] = artifact
    freshness = evaluate_freshness(requests=requests, sources=sources, selected=selected, artifacts=artifacts)
    assert all(freshness[f"section:{s}:raw_audio"].state == Freshness.FRESH for s in ("A", "C"))
    assert all(freshness[f"section:{w.section_id}:raw_audio"].state == Freshness.MISSING for w in result.impact.created)


class Repository:
    def __init__(self, current):
        self.current, self.saved = current, []
        self.scripts = {current.id: current}
    def active_script(self): return self.current
    def save_and_select(self, revision, *, expected_active_revision_id):
        self.saved.append((revision, expected_active_revision_id)); self.scripts[revision.id] = revision; self.current = revision
    def get_script(self, revision_id): return self.scripts[revision_id]


def test_application_service_requires_expected_revision_and_can_describe_result():
    repository = Repository(script(section("A"), section("B", "left right")))
    service = SectionEditingService(repository)
    with pytest.raises(DomainValidationError, match="Active script changed"):
        service.split("B", 5, expected_active_revision_id="stale")
    assert repository.saved == []
    result = service.split("B", 5, expected_active_revision_id="script1")
    assert repository.saved == [(result.script, "script1")]
    assert service.describe(result.script.id) == result


def test_application_import_does_not_load_storage_sqlite_providers_or_ui():
    code = """
import sys
from app.application.section_editing import SectionEditingService
for name in ('app.storage', 'sqlite3', 'app.providers', 'PySide6', 'fastapi'):
    assert not any(module == name or module.startswith(name + '.') for module in sys.modules), name
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

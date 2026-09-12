"""D001: stable sections, immutable selections and legacy compatibility."""

from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
import subprocess
import sys

import pytest

from app.domain.base import DomainValidationError
from app.domain.narrative_segment import NarrativeSegment, SectionRevision
from app.domain.script import Script, ScriptRevision


def section(name: str, *, project_id: str = "project_1") -> SectionRevision:
    return SectionRevision(
        id=f"{name}1",
        section_id=name,
        project_id=project_id,
        title=f"Section {name}",
        text=f"Original {name} text.",
        role="development",
    )


def script() -> ScriptRevision:
    return ScriptRevision.create(project_id="project_1", sections=[section(name) for name in "ABC"])


def legacy_segment(name: str, order: int) -> NarrativeSegment:
    return NarrativeSegment.create(
        workflow_run_id="run_1", order=order, title=name, text=f"Legacy {name}.", role="body"
    )


def test_editing_b_retains_section_ids_old_snapshot_and_selected_a_c() -> None:
    original = script()
    original_payload = asdict(original)

    edited = original.edit_section("B", text="Edited B text.")

    assert tuple(value.section_id for value in edited.sections) == ("A", "B", "C")
    assert edited.section("A") is original.section("A")
    assert edited.section("C") is original.section("C")
    assert edited.section("B").id != original.section("B").id
    assert edited.section("B").parent_revision_id == "B1"
    assert edited.section("B").text == "Edited B text."
    assert original.section("B").text == "Original B text."
    assert asdict(original) == original_payload
    assert edited.id != original.id
    assert edited.parent_revision_id == original.id
    assert edited.script_id == original.script_id
    assert edited.project_id == original.project_id


def test_reorder_changes_only_script_selection_order() -> None:
    original = script().edit_section("B", text="B2 text.")
    reordered = original.reorder(["C", "A", "B"])

    assert tuple(value.section_id for value in reordered.sections) == ("C", "A", "B")
    assert tuple(value.section_id for value in original.sections) == ("A", "B", "C")
    for section_id in "ABC":
        assert reordered.section(section_id) is original.section(section_id)
    assert reordered.script_id == original.script_id
    assert reordered.parent_revision_id == original.id
    assert reordered.reorder(["C", "A", "B"]) is reordered


def test_selecting_retained_b1_does_not_erase_b2() -> None:
    first = script()
    second = first.edit_section("B", text="B2 text.", title="Revised B", role="twist")
    restored = second.select_section_revision(first.section("B"))

    assert restored.section("B") is first.section("B")
    assert second.section("B").text == "B2 text."
    assert second.section("B").title == "Revised B"
    assert second.section("B").role == "twist"
    assert restored.parent_revision_id == second.id
    assert restored.select_section_revision(restored.section("B")) is restored
    third = second.edit_section("B", text="B3 text.")
    assert third.section("B").parent_revision_id == second.section("B").id
    assert first.section("B").id == "B1"


def test_snapshots_do_not_alias_mutable_input_collections_or_serialized_payloads() -> None:
    values = [section("A"), section("B")]
    snapshot = ScriptRevision(
        id="script_revision_1", script_id="script_1", project_id="project_1", sections=values
    )
    values.clear()
    assert tuple(value.section_id for value in snapshot.sections) == ("A", "B")
    payload = asdict(snapshot)
    payload["sections"][0]["text"] = "Changed outside the model."
    assert snapshot.section("A").text == "Original A text."

    with pytest.raises(FrozenInstanceError):
        snapshot.language = "pl"
    with pytest.raises(FrozenInstanceError):
        snapshot.section("A").text = "Mutated text."
    with pytest.raises(TypeError):
        snapshot.sections[0] = section("C")


def test_new_section_and_empty_script_need_no_workflow_run() -> None:
    revision = SectionRevision.create(project_id="project_1", title="Hook", text="Hello.", role="hook")
    assert revision.section_id.startswith("narrative_segment_")
    assert revision.id != revision.section_id
    assert revision.parent_revision_id is None
    draft = ScriptRevision.create(project_id="project_1")
    assert draft.sections == ()
    assert draft.reorder([]) is draft
    assert "workflow_run_id" not in asdict(revision)
    assert "workflow_run_id" not in asdict(draft)


@pytest.mark.parametrize(
    "order",
    [[], ["A", "C"], ["A", "B", "B"], ["A", "B", "D"],
     ["A", "B", "C", "D"], "ABC", ["A", [], "C"], None],
)
def test_invalid_reorder_cannot_drop_duplicate_or_introduce_sections(order) -> None:
    original = script()
    with pytest.raises(DomainValidationError):
        original.reorder(order)
    assert tuple(value.section_id for value in original.sections) == ("A", "B", "C")


@pytest.mark.parametrize("operation", ["read", "edit", "select"])
def test_unknown_section_references_are_rejected(operation: str) -> None:
    original = script()
    with pytest.raises(DomainValidationError, match="Unknown section"):
        if operation == "read":
            original.section("D")
        elif operation == "edit":
            original.edit_section("D", text="New text.")
        else:
            original.select_section_revision(section("D"))


@pytest.mark.parametrize("operation", ["create", "select"])
def test_cross_project_revision_is_rejected(operation: str) -> None:
    foreign = section("B", project_id="project_2")
    with pytest.raises(DomainValidationError, match="different project"):
        if operation == "create":
            ScriptRevision.create(project_id="project_1", sections=[foreign])
        else:
            script().select_section_revision(foreign)


def test_one_active_revision_per_section_and_unique_revision_ids() -> None:
    a = section("A")
    with pytest.raises(DomainValidationError, match="section more than once"):
        ScriptRevision.create(project_id="project_1", sections=[a, a.revise(text="A2.")])
    with pytest.raises(DomainValidationError, match="duplicate revision IDs"):
        ScriptRevision.create(project_id="project_1", sections=[a, replace(section("B"), id=a.id)])
    with pytest.raises(DomainValidationError, match="different content"):
        script().select_section_revision(replace(section("B"), text="Changed using B1 ID."))
    with pytest.raises(DomainValidationError, match="duplicate revision IDs"):
        script().select_section_revision(replace(section("B"), id=a.id))


@pytest.mark.parametrize("field", ["id", "section_id", "project_id", "title", "text", "role", "parent_revision_id"])
@pytest.mark.parametrize("invalid", [" ", []])
def test_section_direct_construction_rejects_blank_or_mutable_fields(field: str, invalid) -> None:
    with pytest.raises(DomainValidationError):
        replace(section("A"), **{field: invalid})


def test_invalid_edits_and_self_parent_preserve_original() -> None:
    original = script()
    with pytest.raises(DomainValidationError, match="text"):
        original.edit_section("B", text=" ")
    with pytest.raises(DomainValidationError, match="title"):
        original.edit_section("B", text="Edited", title="")
    with pytest.raises(DomainValidationError, match="own parent"):
        replace(section("A"), parent_revision_id="A1")
    with pytest.raises(DomainValidationError, match="own parent"):
        replace(original, parent_revision_id=original.id)
    assert original.section("B").text == "Original B text."


@pytest.mark.parametrize(
    "change",
    [{"id": ""}, {"script_id": ""}, {"project_id": ""}, {"language": []},
     {"parent_revision_id": []}, {"sections": None}, {"sections": ["unknown-revision"]}],
)
def test_invalid_script_construction_is_rejected(change) -> None:
    with pytest.raises(DomainValidationError):
        replace(script(), **change)


@pytest.mark.parametrize("values", [None, {"A": "revision-id"}, "revision-id"])
def test_factories_reject_nonsequence_payloads_instead_of_guessing_references(values) -> None:
    with pytest.raises(DomainValidationError, match="sequence"):
        ScriptRevision.create(project_id="project_1", sections=values)
    legacy = Script.create(workflow_run_id="run_1", text="Flat text.")
    with pytest.raises(DomainValidationError, match="sequence"):
        ScriptRevision.from_legacy(legacy, project_id="project_1", segments=values)


def test_legacy_import_preserves_ids_language_and_order_without_mutable_aliases() -> None:
    a, b, c = [legacy_segment(name, index) for index, name in enumerate("ABC", start=1)]
    legacy = Script.create(workflow_run_id="run_1", text="Legacy flattened text.", language="pl", version=3)
    old_script_payload = asdict(legacy)
    old_segments = [asdict(value) for value in (a, b, c)]

    imported = ScriptRevision.from_legacy(legacy, project_id="project_1", segments=[c, a, b])
    edited = imported.edit_section(b.id, text="B2 imported edit.")

    assert imported.script_id == legacy.id
    assert imported.language == "pl"
    assert tuple(value.section_id for value in imported.sections) == (a.id, b.id, c.id)
    assert imported.section(b.id).text == "Legacy B."
    assert edited.section(b.id).text == "B2 imported edit."
    assert asdict(legacy) == old_script_payload
    assert [asdict(value) for value in (a, b, c)] == old_segments

    b.text = "Legacy mutation after import."
    legacy.language = "en"
    assert imported.section(b.id).text == "Legacy B."
    assert imported.language == "pl"


@pytest.mark.parametrize("problem", ["empty", "foreign_run", "duplicate_order", "invalid_order", "duplicate_section", "wrong_type"])
def test_legacy_import_rejects_ambiguous_or_foreign_segments(problem: str) -> None:
    legacy = Script.create(workflow_run_id="run_1", text="Flat text.")
    a, b = legacy_segment("A", 1), legacy_segment("B", 2)
    segments = [a, b]
    if problem == "empty":
        segments = []
    elif problem == "foreign_run":
        b.workflow_run_id = "run_2"
    elif problem == "duplicate_order":
        b.order = a.order
    elif problem == "invalid_order":
        b.order = True
    elif problem == "duplicate_section":
        b.id = a.id
    else:
        segments = ["segment-id"]
    with pytest.raises(DomainValidationError):
        ScriptRevision.from_legacy(legacy, project_id="project_1", segments=segments)


def test_domain_can_be_used_without_importing_infrastructure() -> None:
    # A fresh isolated interpreter prevents the rest of pytest from masking imports.
    source = """
import sys
sys.path.insert(0, sys.argv[1])
class BlockInfrastructure:
    def find_spec(self, fullname, path=None, target=None):
        roots = ('PySide6', 'fastapi', 'sqlite3', 'torch', 'app.api', 'app.providers',
                 'app.storage', 'app.tts', 'app.workflow')
        if any(fullname == root or fullname.startswith(root + '.') for root in roots):
            raise AssertionError('Infrastructure imported: ' + fullname)
sys.meta_path.insert(0, BlockInfrastructure())
from app.domain.narrative_segment import SectionRevision
from app.domain.script import ScriptRevision
b1 = SectionRevision.create(project_id='p', title='B', text='B1', role='body')
s1 = ScriptRevision.create(project_id='p', sections=[b1])
s2 = s1.edit_section(b1.section_id, text='B2')
assert s1.section(b1.section_id).text == 'B1'
assert s2.section(b1.section_id).text == 'B2'
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", source, str(Path(__file__).resolve().parents[2])],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr

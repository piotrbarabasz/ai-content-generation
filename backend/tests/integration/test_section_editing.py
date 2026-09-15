"""D038 atomic SQLite split/merge, rollback, history and reopen behavior."""

from dataclasses import replace

import pytest

from app.application.projects import ProjectSession
from app.domain.narrative_segment import SectionRevision
from app.storage.project_repository import ProjectRepository


def create_project(root):
    session = ProjectSession.create(root, name="Editing", language="pl", repository_factory=ProjectRepository)
    draft = session.active_script
    sections = tuple(SectionRevision.create(project_id=session.project.id, title=name,
                                             text={"A": "Pierwsza.", "B": "Lewa. Prawa.", "C": "Trzecia."}[name],
                                             role="body") for name in "ABC")
    initial = replace(draft, id="script-abc", parent_revision_id=draft.id, sections=sections)
    session.save_script(initial, expected_active_revision_id=draft.id)
    return session, initial


def counts(repository):
    return tuple(repository._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                 for table in ("sections", "section_revisions", "script_revisions", "script_sections"))


def test_split_persists_new_identities_exact_lineage_and_unaffected_ids_after_reopen(tmp_path):
    session, original = create_project(tmp_path)
    source = original.sections[1]
    boundary = source.text.index("Prawa")
    result = session.split_section(source.section_id, boundary, expected_active_revision_id=original.id,
                                   left_title="Lewa", right_title="Prawa")
    left, right = result.script.sections[1:3]
    assert session.active_script == result.script
    assert (result.script.sections[0], result.script.sections[-1]) == (original.sections[0], original.sections[-1])
    assert session.repository.get_script(original.id) == original
    assert session.repository.get_section(source.id) == source
    assert session.repository.section_history(source.section_id) == (source,)
    assert session.repository.section_history(left.section_id) == (left,)
    assert session.describe_section_edit(result.script.id) == result
    session.close()
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        assert reopened.active_script == result.script
        assert reopened.describe_section_edit(result.script.id) == result
        assert reopened.repository.get_script(original.id) == original
        assert reopened.repository.get_section(source.id) == source


def test_merge_persists_all_sources_new_identity_and_exact_separator_after_reopen(tmp_path):
    session, original = create_project(tmp_path)
    a, b, c = original.sections
    result = session.merge_sections([a.section_id, b.section_id], expected_active_revision_id=original.id,
                                    separator="\n---\n", title="AB")
    merged = result.script.sections[0]
    assert merged.text == a.text + "\n---\n" + b.text and merged.title == "AB"
    assert merged.section_id not in {a.section_id, b.section_id, c.section_id}
    assert result.script.sections[1] == c
    assert [(source.section_id, source.revision_id) for source in result.lineages[0].sources] == [
        (a.section_id, a.id), (b.section_id, b.id)]
    assert session.repository.get_script(original.id) == original
    assert session.repository.get_section(a.id) == a and session.repository.get_section(b.id) == b
    session.close()
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        assert reopened.active_script == result.script
        assert reopened.describe_section_edit(result.script.id) == result
        assert reopened.repository.section_history(c.section_id) == (c,)


@pytest.mark.parametrize("operation", ["split", "merge"])
@pytest.mark.parametrize("failure", ["section_insert", "active_update"])
def test_sqlite_failure_rolls_back_every_new_identity_snapshot_and_pointer(tmp_path, operation, failure):
    session, original = create_project(tmp_path)
    repository, before = session.repository, counts(session.repository)
    if failure == "section_insert":
        real = repository._save_section
        def fail(section):
            real(section)
            if section.section_id not in {value.section_id for value in original.sections}:
                raise RuntimeError("injected section failure")
        repository._save_section = fail
    else:
        real = repository._set_active
        def fail(revision_id):
            real(revision_id)
            raise RuntimeError("injected pointer failure")
        repository._set_active = fail
    with pytest.raises(RuntimeError, match="injected"):
        if operation == "split":
            session.split_section(original.sections[1].section_id, 6, expected_active_revision_id=original.id)
        else:
            session.merge_sections([s.section_id for s in original.sections[:2]], expected_active_revision_id=original.id)
    assert repository.active_script() == original and counts(repository) == before
    session.close()
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        assert reopened.active_script == original and counts(reopened.repository) == before


@pytest.mark.parametrize("operation,argument", [
    ("split", ("missing", 1)), ("split", ("B", 0)),
    ("merge", ["A", "C"]), ("merge", ["B", "A"]), ("merge", ["A"]),
])
def test_invalid_selection_or_boundary_has_no_database_effect(tmp_path, operation, argument):
    session, original = create_project(tmp_path)
    before = counts(session.repository)
    ids = {section.title: section.section_id for section in original.sections}
    with pytest.raises(ValueError):
        if operation == "split":
            section_id, boundary = argument
            session.split_section(ids.get(section_id, section_id), boundary, expected_active_revision_id=original.id)
        else:
            session.merge_sections([ids.get(value, value) for value in argument], expected_active_revision_id=original.id)
    assert session.active_script == original and counts(session.repository) == before
    session.close()


@pytest.mark.parametrize("operation", ["split", "merge"])
def test_stale_expected_revision_fails_before_any_repository_write(tmp_path, operation):
    session, original = create_project(tmp_path)
    before = counts(session.repository)
    with pytest.raises(ValueError, match="Active script changed"):
        if operation == "split":
            session.split_section(original.sections[1].section_id, 6, expected_active_revision_id="stale")
        else:
            session.merge_sections([s.section_id for s in original.sections[:2]], expected_active_revision_id="stale")
    assert session.active_script == original and counts(session.repository) == before
    session.close()


def test_split_then_merge_retains_both_operation_steps_and_source_revisions(tmp_path):
    session, original = create_project(tmp_path)
    split = session.split_section(original.sections[1].section_id, 6, expected_active_revision_id=original.id)
    halves = split.script.sections[1:3]
    merged = session.merge_sections([s.section_id for s in halves], expected_active_revision_id=split.script.id,
                                    separator="", title="B restored")
    assert merged.script.sections[1].text == original.sections[1].text
    assert merged.script.sections[1].section_id not in {s.section_id for s in halves}
    assert session.describe_section_edit(split.script.id) == split
    assert session.describe_section_edit(merged.script.id) == merged
    assert session.repository.get_script(original.id) == original
    assert session.repository.get_script(split.script.id) == split.script
    assert session.repository.get_section(original.sections[1].id) == original.sections[1]
    session.close()

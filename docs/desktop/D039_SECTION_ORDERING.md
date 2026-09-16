# D039 — Persist section ordering changes

## Boundary and identity

D039 adds the pure `reorder_script` operation and a repository-neutral
`SectionOrderingService`, exposed through `ProjectSession.reorder_sections`.
The caller supplies a complete ordered list of the current stable section IDs and
the exact active script revision ID.

For `A-B-C` reordered to `C-A-B`, the child script snapshot selects exactly the
same three `SectionRevision` values in the new order. No section identity,
revision ID, text, title, role, audio, scene, prompt or image is changed. The
previous snapshot remains retained and selectable. Missing, duplicate, unknown,
partial, non-sequence and stale requests fail without a write.

## Persistence and dependency impact

The application compares the expected active revision before deriving the result;
D003 compares it again inside `save_and_select`. The child script, ordered
`script_sections` rows and active pointer therefore commit in one SQLite
transaction. A database failure rolls back the entire child snapshot and active
selection. Requesting the already-active order is explicit no-op: it returns the
same snapshot and creates no database row.

`SectionOrderImpact` records the previous and selected ID order and the exact
D005 `content_fingerprint(list(section_order))`. Every existing section ID is
reusable, so section-level media remains fresh when its own source inputs remain
unchanged. A changed order requires only `timeline` and `video_render` to be
rebuilt. The impact describes dependency work; it does not enqueue work, delete
artifacts, select a timeline, change a text revision or splice media.

## Example

```python
current = session.active_script
result = session.reorder_sections(
    [current.sections[2].section_id, current.sections[0].section_id,
     current.sections[1].section_id],
    expected_active_revision_id=current.id,
)

assert result.impact.project_outputs == ("timeline", "video_render")
assert result.script.sections[0] is current.sections[2]
```

## Validation evidence

**Status: PASS (2026-09-15).** Tests use Python 3.11.9 on Windows, temporary
SQLite projects and deterministic in-memory data; no provider, network, model or
private media access occurs.

Focused command:

```text
python -m pytest backend/tests/unit/test_section_ordering.py backend/tests/integration/test_section_ordering.py backend/tests/unit/test_editorial_revisions.py backend/tests/integration/test_durable_project.py backend/tests/integration/test_dependency_index.py
```

Results:

- new D039 unit/integration coverage: 14 passed;
- focused suite: 78 passed in 4.40 seconds;
- full `python -m pytest backend/tests`: 1435 passed, 11 skipped in 287.57
  seconds; skips are the existing optional D044 cases;
- `python -m compileall`, `pip check` and `git diff --check`: passed.

Acceptance criteria:

| Criterion | Result | Evidence |
| --- | --- | --- |
| Reorder A-B-C to C-A-B retains all section/revision identities and existing media | PASS | Pure tests retain exact values; SQLite reopen verifies identical revisions and published audio artifacts remain byte-identical and fresh. |
| Stale, missing and duplicate IDs are rejected | PASS | Service validates the active token before persistence; unit/integration tests assert stale, incomplete, unknown and duplicate requests leave snapshot/history unchanged. |
| Changed order carries correct dependency impact | PASS | The immutable order fingerprint changes with sequence; every section remains reusable and only timeline/render are declared for rebuilding. |

The direct command has deliberately narrow limits: it does not select or compile a
timeline, enqueue render work, reconcile a UI drag operation, or mutate
section-level selections. Additional project-wide outputs must be added to the
explicit impact list by the task that introduces them.

## Changed files

- `backend/app/domain/section_order.py`
- `backend/app/application/section_ordering.py`
- `backend/app/application/projects.py`
- `backend/tests/unit/test_section_ordering.py`
- `backend/tests/integration/test_section_ordering.py`
- `backend/tests/integration/test_durable_project.py`
- `docs/architecture/domain-model.md`
- `docs/architecture/api-and-storage.md`
- `docs/desktop/IMPLEMENTATION_PLAN.md`
- `docs/desktop/D039_SECTION_ORDERING.md`

# D038 — Transactional section split and merge

## Boundary and identity

D038 adds pure `split_script`, `merge_script` and `describe_section_edit` domain
operations plus repository-neutral `SectionEditingService` commands. The existing
`ProjectSession` exposes them for later desktop bindings. There is no UI, audio
splicing, provider/model call, job scheduling, artifact deletion, reorder change
or implicit LLM edit.

Split replaces one selected section at an explicit Python string offset. The
offset is a Unicode code-point position in the exact retained text. Both halves
must contain non-whitespace content; their bytes/text are not trimmed or
normalized. Concatenating the two new values reproduces the exact source. Titles
can be supplied independently and otherwise copy the source title; both roles
copy the source. Each half receives a new `section_id` and initial revision.

Merge accepts two or more unique section IDs in their current adjacent order.
It creates one new section identity at the first source position and joins exact
source texts with an explicit separator (default: two newlines). The first title
is used unless supplied. Equal source roles are retained; differing roles require
an explicit result role. Reverse, duplicate, missing and non-adjacent selections
fail before persistence.

Both operations create one new `ScriptRevision` whose parent is the exact current
snapshot. Sections outside the replacement retain the same immutable revision
values, stable IDs and relative order. Source sections/revisions and the parent
script remain historical records and are never overwritten.

## Durable lineage without a migration

The D003 version-1 schema already retains exact parent and child script snapshots.
Their topology provides durable split/merge lineage:

- a split parent has one source at the replacement position; its child has two
  new identities whose exact text concatenates to that source;
- a merge parent has adjacent sources at the replacement position; its child has
  one new identity containing every complete source in order with one explicit
  repeated separator.

`SectionSourceSlice` records the exact source section/revision and half-open text
range. Split descendants record `[0, boundary)` and `[boundary, len(text))`;
merge records each complete `[0, len(text))` source. `describe_section_edit`
reconstructs these immutable values after project reopen and rejects ordinary
edits, reorder, foreign script/project lineage, moved replacements, partial
sources and changed unaffected revisions.

New section revisions deliberately have no same-identity parent because their
identities did not previously exist. Cross-identity ancestry lives in the
parent/child script topology. This respects the existing D001 meaning of
`SectionRevision.parent_revision_id` and avoids a project schema migration before
D042. No sidecar state can disagree with the transaction.

## Invalidation metadata

`SectionEditImpact` identifies retired source section IDs, newly created section
revision IDs, exact D005 text fingerprints and unaffected reusable section IDs.
For each created identity, the metadata declares the downstream section outputs
that need work: raw/processed audio, scene plan/timing, visual prompts and scene
images. Timeline and video render are project outputs requiring rebuild.

This metadata describes impact; it does not schedule work or delete old results.
New identities have no selected output, so D005 evaluates their desired output as
`missing`. Unaffected section requests remain `fresh` when their actual inputs are
unchanged. Retired media remains historical under its old section identity. Split
or merge never claims that prior audio can be spliced or reused for new text
identities.

## Atomic application commands

Every command requires `expected_active_revision_id`. The application compares it
before creating the edit and D003 compares it again inside `save_and_select`.
The new section rows, script row, ordered references and active pointer commit in
that one SQLite transaction. Invalid input performs no repository write. Failure
during any section insert or after updating the active pointer rolls back all new
identities and references. No active pointer can expose a partial split/merge.

The project remains single-writer as defined by D003. The service is independent
of SQLite, Qt, HTTP, providers and storage composition.

## Example

```python
current = session.active_script
split = session.split_section(
    current.sections[1].section_id,
    boundary,
    expected_active_revision_id=current.id,
    left_title="Setup",
    right_title="Payoff",
)

merged = session.merge_sections(
    [split.script.sections[1].section_id, split.script.sections[2].section_id],
    expected_active_revision_id=split.script.id,
    separator="",
)

# Reconstruct exact source lineage after reopen.
assert session.describe_section_edit(merged.script.id) == merged
```

## Validation evidence

**Status: PASS (2026-09-15).** Tests use Python 3.11.9 on Windows, fresh
temporary SQLite projects and pure values only, without network, providers,
models or private media.

Focused command:

```text
python -m pytest backend/tests/unit/test_section_editing.py backend/tests/integration/test_section_editing.py backend/tests/unit/test_editorial_revisions.py backend/tests/unit/test_project_repository.py backend/tests/integration/test_durable_project.py backend/tests/unit/test_invalidation.py backend/tests/integration/test_dependency_index.py
```

Results:

- dependency baseline before implementation: 110 passed;
- new D038 unit and integration coverage: 50 passed;
- final focused suite: 160 passed in 3.02 seconds;
- full `python -m pytest backend/tests`: 1421 passed, 11 skipped in 268.91
  seconds; the skips are the existing optional D044 cases;
- `python -m compileall`, `pip check`, documentation/plan consistency checks and
  `git diff --check`: passed.

Acceptance criteria:

| Criterion | Result | Evidence |
| --- | --- | --- |
| Split/merge preserves original revisions and unaffected section IDs | PASS | Pure topology tests and SQLite reopen tests verify exact source revisions, snapshots, IDs, order and reconstructed lineage. |
| Invalid selection fails atomically | PASS | Invalid-input tests observe no writes; injected failures during section insertion and after the active-pointer update roll back both commands and remain clean after reopen. |
| Changed sections require appropriate downstream work | PASS | Immutable impact metadata records new D005 fingerprints and section/project outputs requiring rebuild while unaffected IDs remain reusable; D005 evaluates new desired outputs as missing and unchanged outputs as fresh. |

Known limits are deliberate task boundaries. Lineage reconstruction describes a
direct parent/child split or merge, while later ordinary revisions are inspected
at their own snapshot step. A rolled-back command may consume UUID values, but
does not persist them. Future section-level derivative families must extend the
explicit output list when introduced by their own task.

## Changed files

- `backend/app/domain/section_edit.py`
- `backend/app/application/section_editing.py`
- `backend/app/application/projects.py`
- `backend/tests/unit/test_section_editing.py`
- `backend/tests/integration/test_section_editing.py`
- `docs/architecture/domain-model.md`
- `docs/architecture/api-and-storage.md`
- `docs/desktop/IMPLEMENTATION_PLAN.md`
- `docs/desktop/D038_SECTION_SPLIT_MERGE.md`

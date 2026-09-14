# D014 — Structured script generation

D014 adds the desktop `ScriptGenerationService` over the implemented D001 values
and D003 project session. It uses the structural `generate_structured(prompt,
schema)` contract of `LLMProvider`; no provider factory, storage implementation,
HTTP, UI or optional model is imported by the application service.

## Exact structured response

The versioned schema ID is `urn:aics:script-sections:v1`. The response must be a
plain object containing exactly one nonempty ordered `sections` array. Every
section must contain exactly three nonempty string fields:

```json
{
  "sections": [
    {"title": "First argument", "role": "body", "text": "The provider's first paragraph."},
    {"title": "Second argument", "role": "body", "text": "The provider's second paragraph."},
    {"title": "Next step", "role": "cta", "text": "An optional call to action."}
  ]
}
```

The fixed validator enforces that entire contract before persistence, without
coercion, text normalization, partial salvage or template fallback. Extra fields,
empty arrays, missing keys, numbers/booleans, blank values and JSON strings in
place of objects are rejected. Every provider receives a fresh schema; mutating
that copy cannot weaken validation.

Array order, titles, roles and text are authoritative and copied exactly, including
Unicode, internal/leading/trailing whitespace and repeated text. Roles are
nonempty editorial labels, not unique keys or a forced hook/body/close sequence.
Repeated roles and optional CTA are supported. `include_cta` is a generation
instruction, not a rule that silently adds/removes content after validation.

## Revisions and manual input

`generate(request, expected_active_revision_id=..., include_cta=False)` explicitly
replaces the whole script with new section identities. It retains the project,
stable script ID and production language, creates a new script revision linked
to its parent and leaves previous snapshots available. It does not guess identity
correspondence from repeated roles. The prompt is a JSON envelope containing the
request, script language and CTA instruction; the service never calls
`generate_text` or converts the response into legacy fixed templates.

Manual operations need no provider:

- `replace_sections(payload, expected_active_revision_id=...)` uses the same
  contract for an explicit whole-script replacement.
- `append_text(text, title=..., role="body", expected_active_revision_id=...)`
  adds one section and preserves every existing section revision.
- `edit_text(section_id, text, expected_active_revision_id=..., title=None,
  role=None)` keeps the stable section ID, creates a linked section revision and
  preserves unrelated sections. No paragraphs are split or invented implicitly.

Expected active revision is checked before generation. D003's atomic
`save_and_select` compares it again inside the write transaction. An intervening
edit wins over a late generated response. Invalid output, provider exceptions,
missing provider and write failures cannot replace the active script or leave a
partially saved section set. No artifact, approval history, audio or scene history
is deleted. Rejected responses are not published as project revisions.

The service is synchronous and must execute on the owning project coordinator;
slow providers must not be invoked on the UI thread. Real provider APIs, worker
dispatch, cancel/retry orchestration and UI wiring are outside D014. No new queue,
database schema or Python dependency is introduced.

## Deterministic mock and compatibility

`MockLLMProvider` recognizes only the exact desktop schema. Its deliberately
simple offline response uses request paragraphs as section text, the first `hook`
and subsequent repeated `body` roles, plus a deterministic optional CTA fixture.
The mock does not claim model-quality writing, translation or factual research.
It returns fresh objects and identical content for identical requests.

Other structured schemas retain the previous mock response envelope, and
`generate_text` is unchanged. The legacy workflow `ScriptGenerationModule` keeps
its existing output/artifact contract and template behavior. The new desktop
service is the structured-output path; no unrelated research, script workflow,
scene, audio or rendering modules are rewritten.

## Validation evidence — 2026-09-14

- Baseline D001/D003, legacy script module and mocks: **77 passed**.
- Focused suite: **113 passed in 2.62 s**, including **36 new D014 cases**.
- `python -m pytest backend/tests`: **1077 passed in 116.70 s**.
- `git diff --check`, new-file whitespace/EOF/UTF-8 and relative documentation
  links: **PASS**. All eight changed/new files belong to D014; task-block comparison
  with HEAD confirms no other backlog task changed.
- Valid-output fixtures verify exact text/order/titles/roles, duplicated text and
  roles, custom labels, optional/multiple CTA, new section identities, parent
  linkage, unchanged production language and project reopen.
- Invalid-output fixtures reject malformed root/array/section shapes, unexpected
  fields, missing fields, blank strings and coerced values, including a malformed
  final section after valid earlier entries; prior selection/history stays intact.
- Failure tests exercise provider errors, stale requests before provider calls,
  an edit during generation, schema mutation and a database failure after saving
  the first section. D003 rolls back the complete transaction.
- Manual append/edit/replace tests preserve exact text and unrelated identities.
  Deterministic mock tests cover the schema path with and without CTA and retain
  legacy mock behavior. Fresh-process imports exclude providers, SQLite/storage
  and UI from the application layer.

Reproduce focused validation in the isolated Python environment:

```powershell
python -m pytest backend/tests/integration/test_structured_script.py backend/tests/unit/test_editorial_revisions.py backend/tests/unit/test_project_repository.py backend/tests/unit/test_t025.py backend/tests/unit/test_mock_providers.py
```

All tests are deterministic/offline and use isolated temporary projects. Only
D014's backlog entry changes; D002/D008/D012 pending manual gates remain unchanged.
D015 is not started. The task branch is unmerged; no commit or push is included.

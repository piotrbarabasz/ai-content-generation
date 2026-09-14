# D015 — Independent visual prompt revisions

D015 uses D013's retained accepted scenes, D014's structured LLM boundary and
D005's consumed-input declarations. It adds no image-generation calls, audio
operations, UI, model/network dependency, queue or database schema.

## Pinned inputs and revisions

`PromptContextRevision` retains a project-owned film brief or visual style. A
family has a stable `context_id`; each immutable revision has its own ID and
optional parent revision. Separate style families can serve different scenes.
`pin_context(kind, text, parent_revision_id=...)` creates a retained value without
changing an implicit global current context.

`VisualPromptRevision` stores exact prompt text, a parent prompt revision ID,
manual/generated provenance, the D005 request and immutable `PromptInputs`.
Those inputs identify the project, accepted plan, scene, section revision and
exact brief/style revisions. The retained payload contains:

- Scene text range, text, visual description and acceptance identity.
- Complete section text, title, role and revision identity.
- Film brief text, family ID and revision ID.
- Visual style text, family ID and revision ID.

The generation payload is constructed exclusively from those retained values.
The adapter reconstructs and compares it before saving a prompt; an altered text
under an unchanged context revision ID is rejected. No unversioned global context
is fed to the provider. Parent prompt references describe lineage; they are not
extra generation inputs, because generation does not consume prior prompt text.

## Application commands and manual ownership

`VisualPromptService` uses injected storage and the existing structural
`generate_structured(prompt, schema)` contract. A configured generation identity
records provider/model/settings separately from prompt inputs. Domain and
application imports remain independent of concrete providers, storage, TTS and UI.

Generation uses `urn:aics:visual-prompt:v1`: exactly one nonempty string field
`prompt`, with no additional fields or coercion. Provider text is retained exactly.
The deterministic mock recognizes only this schema, derives fixture text from
the supplied inputs and preserves the other mock/legacy contracts.

- `generate(acceptance_id, scene_id, brief_revision_id, style_revision_id)` creates
  a generated revision whose parent is the selected revision at generation start.
- `create_manual(...)` creates a first/manual variant without requiring a provider.
- `edit_manual(revision_id, text)` creates a child manual revision with the same
  pinned inputs and generation request, retaining the original and its history.
- `select(revision_id, expected_selection_id=...)` is a separate explicit command.
  Creation, generation and manual editing never change selection implicitly.
- `selected(scene_id)` exposes the retained selected revision. The storage adapter
  also exposes revision and selection history for reopen/discovery.

A generation that completes after a manual edit is retained as another variant;
it cannot replace that edit. If section text changes during generation, its old
source snapshot remains historical and the result cannot be selected as current.
An explicit selection can choose a retained earlier context/style variant, but
the accepted scene must still belong to the current section revision.

## Selection publication and recovery

`ProjectVisualPrompts` reuses D004's configured project artifact store/index.
Contexts, prompt revisions and selection events are immutable JSON artifacts.
A selection event identifies its scene, prompt revision and previous selection
event. The active choice is the unique complete chain head, not the artifact with
the latest timestamp or filename. The chain rejects branches, gaps and crossed
scene references, and survives reopen independently of manifest listing order.

The expected previous event ID is checked under D003's exclusive coordinator
session before publication. It detects both ordinary conflicts and changing from
A to B and back to A. A stale command cannot silently replace a newer choice.
No separate mutable active-selection file or schema is introduced.

Failed validation/provider calls and index publication leave prior selections and
bytes intact. D004 can retain an unindexed complete orphan for recovery. If index
publication succeeded but later staging cleanup failed, the adapter verifies the
exact registered bytes and returns the committed result truthfully. It does not
report that an already committed selection failed.

Operations are synchronous on the owning project coordinator. No heavy provider
should run on the UI thread; managed worker dispatch is outside this task.

## D005 freshness and selective impact

Each prompt publishes `desktop_dependencies` with its per-scene output key,
manual/generated provenance, algorithm/schema and generation identity. Four
source edges fingerprint the exact consumed scene, section context, brief and
style payloads. Brief/style source keys use stable family IDs, so a change to
one style family affects only its consumers. Other scene prompts, audio and
existing visual artifacts are untouched.

`freshness(...)` uses explicitly supplied desired brief/style revision IDs and
the active section context to derive status through D005. Active section reads
are for freshness only; they are never substituted into a pinned generation.
Creating a context revision alone changes no existing prompt or selection. Reusing
the old pinned revision remains reproducible; rebinding to the new revision makes
its consumers stale. A new revision ID is significant even with identical text.

For manual outputs, incompatible context means `review_required=True`; selection
and prompt bytes remain owned by the user. Existing downstream bytes that consume
the unchanged manual prompt retain D005 compatibility. Generation identity changes
can likewise be evaluated explicitly without rewriting prompts.

## Validation evidence — 2026-09-14

- Baseline D005/D013/D014 integration: **51 passed**.
- Focused suite: **84 passed in 17.22 s**, including **30 new D015 cases**.
- `python -m pytest backend/tests`: **1107 passed in 133.11 s**.
- `git diff --check`, new-file whitespace/EOF/UTF-8 and relative documentation
  links: **PASS**. All nine changed/new files belong to D015; task-block comparison
  with HEAD confirms no other backlog task changed.
- Tests cover separate prompts for scenes, exact output/pinned payloads, strict
  invalid output and manual input rejection, manual-only operation, revision
  lineage, explicit selection, unchanged existing PCM audio/history and reopen.
- Race cases cover manual edits during generation, late results after section
  edits, stale selection tokens and the A/B/A selection case. Manifest reordering
  does not change the selected revision.
- Context tests verify style-family consumers, shared brief revisions, section-B
  edits leaving A/C fresh, manual review flags, and downstream compatibility of
  unchanged manual bytes using D005 and its artifact index.
- Failure tests cover provider errors, foreign/unknown context, wrong context
  kinds, cross-scene parentage, unpinned modified payloads, corrupted artifacts,
  index failure and truthful success after post-commit cleanup failure.
- Mock/schema tests verify deterministic content and frozen generation identity;
  a fresh-process import excludes providers, SQLite/storage, TTS and UI.

Focused reproduction:

```powershell
python -m pytest backend/tests/integration/test_visual_prompts.py backend/tests/integration/test_dependency_index.py backend/tests/integration/test_scene_plans.py backend/tests/integration/test_structured_script.py backend/tests/unit/test_mock_providers.py
```

All tests are isolated and offline. No image model or network connection was used.
Only D015's backlog entry changes; D002/D008/D012 manual gates remain unchanged.
The task branch is unmerged and no later task, commit or push is included.

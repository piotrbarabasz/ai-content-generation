# D005: artifact dependencies and freshness

D005 adds pure request identity and freshness derivation over the immutable
artifact catalog introduced by D004. It does not connect legacy generators or
desktop UI, execute jobs, replace selections, delete variants or implement D006.

## Request identity and recorded inputs

`app.domain.dependencies` contains frozen values. `RequestFingerprint.create`
captures an operation, algorithm version, explicitly named input edges, relevant
settings and effective generation identity. Callers supply the resolved provider,
model revision, device and reference checksums where consumed; this layer does
not resolve providers or infer relevance from configuration field names.
An optional `relevant_settings` allowlist excludes unrelated settings and rejects
missing declared fields. Without it, every supplied setting participates.

The version-1 project encoding hashes strict JSON with SHA-256: sorted object
keys, inputs sorted by unique binding name, ordered arrays, exact text/numeric
types and finite numbers. It freezes nested data and rejects implicit object
coercion. This is not RFC 8785. Existing TTS `stable_hash` and cache contracts
remain unchanged.

Source edges contain content fingerprints, not incidental revision IDs. Retitling
a section with unchanged narration therefore preserves a text-only TTS request.
Consumers of title, order, style or global context must explicitly declare those
inputs. Artifact edges record both the consumed artifact ID and checksum. A new
selected variant invalidates its dependents even when bytes happen to match.
Request identity excludes output identity/checksum: equal requests do not promise
equal new AI samples or authorize cache reuse without checking stored bytes.

## Storage mapping

Pass `DependencyDeclaration(output_key, request, provenance).to_metadata()` to
the existing artifact save/import API, alongside any other producer metadata.
The `desktop_dependencies` namespace has an explicit format version. D004 freezes
and publishes that declaration with its immutable manifest; no new database,
table, mutable artifact model or migration is introduced.

`ArtifactDependencyIndex(ProjectArtifactIndex(repository)).records()` reads the
live project's registered manifests. It checks consumed artifact IDs/checksums
and, where the referenced artifact is tracked, its logical output binding.
Malformed versions, absent references and mismatches are rejected without writes.
Legacy manifests remain readable by D004 but have no inferred dependencies.
The adapter validates catalog relationships, not codec correctness or external
tampering with files; byte-integrity verification remains a storage/consumer duty.

## Freshness and manual ownership

`evaluate_freshness` receives four snapshots: desired requests keyed by logical
output, current source fingerprints, selected artifact IDs and indexed dependency
records. It rebinds inputs, compares request fingerprints and propagates stale or
missing inputs only along declared artifact edges. Every referenced output needs
a current request; incomplete graphs, cycles and crossed selected bindings fail
explicitly. It never mutates caller snapshots, selections or stored artifacts.

- `fresh`: a selected tracked artifact matches the current request and inputs.
- `stale`: a selected tracked artifact is retained but has incompatible or missing
  inputs, changed settings/identity, or a stale generated dependency.
- `missing`: there is no selected tracked result. Untracked legacy selections
  cannot be declared compatible by this service and must be handled explicitly
  by their caller; this state does not assert that their files were deleted.

A stale manual artifact has `review_required=True` and keeps its selection.
It is a propagation boundary: consumers of that unchanged, deliberately retained
variant can stay fresh. Changing the manual variant itself still invalidates its
consumers through the artifact identity/checksum. No automatic regeneration or
selection occurs. This implements the plan's reusable manual prompt/image choice;
the UI must expose the review flag when that later task integrates these values.

`FailedAttempt` is separate evidence attached by output key. A failed new attempt
can coexist with a fresh, stale or missing selected result; it never replaces that
result or creates a fourth freshness state. Durable attempt lifecycle is D006.
Desired requests, current source snapshots and active media selections are caller
inputs; D005 persists consumed declarations only. Conditional publication and
revision-aware automatic selection remain D040.

## Validation and acceptance

The table-driven A/B/C pipeline covers text, voice, tempo, prompt, image variant,
section order, export settings and unchanged content. Separate cases cover an
explicit global-context consumer, manual prompts, failed attempts, missing inputs,
cycles, relevant settings and effective provider/model/device/reference changes.
Integration tests publish through D004, edit and reopen a D003 project, retain
the original D001 revision and bytes, and reject corrupt dependency metadata.
A subprocess checks that importing invalidation loads no SQLite, storage, Qt,
HTTP or provider modules.

| Acceptance criterion | Result |
| --- | --- |
| Changing B affects only actual dependents | PASS |
| Prompt edit preserves audio | PASS |
| Tempo change preserves raw TTS | PASS |
| Recorded global-context edges invalidate on context changes | PASS |
| Manual choices persist and changed context flags review | PASS |
| Relevant settings matter; unchanged consumed content remains fresh | PASS |
| Failed attempts remain independent of retained result freshness | PASS |

Focused validation (135 passed, including 34 D005 cases):

```text
python -m pytest backend/tests/unit/test_invalidation.py backend/tests/integration/test_dependency_index.py backend/tests/unit/test_editorial_revisions.py backend/tests/unit/test_project_repository.py backend/tests/integration/test_durable_project.py backend/tests/unit/test_streaming_artifacts.py backend/tests/integration/test_artifact_publication.py
```

Full validation: `python -m pytest backend/tests` — **646 passed**, 34.24 s.
`git diff --check` and new-file whitespace validation — PASS.
Environment: Windows, isolated Python 3.11, 2026-09-12. D005 tests use synthetic
values and temporary files, without provider/network calls or private media.

## Review boundary and remaining risks

Freshness is only as complete as producers' declared consumed inputs and the
caller's current snapshots. D005 deliberately does not guess hidden global
context, provider defaults or legacy input provenance. Future producers must
supply effective identities and only their actual inputs. No desktop or legacy
workflow freshness integration is claimed. The current implementation is a
synchronous read-only calculation under the existing single-session project
boundary; concurrency, job persistence and stale-job publication are later tasks.

Review should check these declarations, manual propagation semantics and the
existing storage contract. No D006 implementation, commit, push or merge belongs
to this run.

# D040 — Revision-aware publication gate

## Scope and composition

Implemented against master `7c1a685` (D009, PR #67), on
`feat/d040-revision-aware-publication-gate`. D004, D005 and D006 were checked in
code and exercised by 49 baseline integration tests before implementation.
No section audio generation, model calls, planner, UI or renderer is included.

`application.result_publication.ResultPublicationService` depends on publication
and byte-store ports. Its immutable values live in `domain.publication`; neither
module imports SQLite, Qt, HTTP, runtime infrastructure or concrete providers.
Compose it on the owning project coordinator thread:

```python
jobs = JobRepository(session.repository)
index = ResultArtifactIndex(session.repository, jobs)
store = LocalArtifactStore(index.root, index=index)
store.recovery_report = store.recover()
publication = ResultPublicationService(index, store)
```

The new index extends `ProjectArtifactIndex`. It uses the existing D004 manifest,
unique byte keys, streaming transfer, checksums, publication journal and recovery.
It does not introduce another store. `d040_format` independently versions the
extension; `d040_heads` retains generation reservations and selections, and
`d040_results` retains immutable decisions. Project, base artifact-index and queue
schema versions stay at 1. Unknown extension versions and WAL publication are
rejected; no general migration or implicit repair is introduced.

## Enqueue and selection contract

`enqueue(output_key, request, expected_sections=..., ...)` requires explicit
section IDs and expected revision IDs. It captures the persisted complete section
values, project ownership and a unique generation token into the existing
immutable D006 `JobRequest.input_snapshot_json`. An optional expected script
revision pins script-wide dependencies; a B-only request does not become obsolete
merely because unrelated A changes. Additional operation inputs are copied into
strict JSON. The trusted operation must declare all consumed inputs; D040 does
not infer undeclared settings, scene dependencies or provider inputs.

The request uses D005 fingerprints, including versioned editorial source bindings
`section:<id>:revision` and optional `project:script_revision`. Artifact metadata
retains both `desktop_dependencies` and the exact enqueue snapshot. Retry uses the
same immutable job, not the current script at retry/completion time.

Enqueue and reservation of the output's generation token commit together. Newer
enqueue supersedes older in-flight generations even on the same section revision.
Reservation keeps the prior selected artifact. At publication the gate compares:

1. Actual current section/script revisions against the saved snapshot.
2. Consumed artifact identities, checksums, logical bindings and known generated
   editorial ancestry against current selections/revisions. D005 manual bytes
   remain reusable when their context changes.
3. The saved generation token against the latest reservation for that output.
4. The current running, uncanceled attempt and coordinator claim token.

Eligible output is selected. Valid obsolete output is still indexed under its
original inputs and completes the job, with reason `obsolete_revisions`,
`obsolete_inputs` or `superseded_generation`. Failed generation/publication does
not clear the previous selection or implicitly promote an older generation.

`selected()` exposes retained selection pointers, not a claim that every pointer
is fresh after a later edit. D005 derives freshness from current requests/sources
and these pointers. `history(output_key)` returns committed decisions; all bytes
remain retrievable through the existing store. `selected_at_publication` records
the decision at that instant, not permanent currentness. A replay with the same
claim returns that original decision without reading replacement bytes, creating
another artifact or changing selection. A different/stale claim is rejected.

## Transactions and failure boundaries

The D003 exclusive session and same-thread guard serialize project edits and the
final revision comparison. D040 opens the existing artifact-index SQLite database,
attaches that session's existing D006 queue, checks ownership and uses
`BEGIN IMMEDIATE`. Artifact registration, conditional selection, decision history
and job success are one database commit. Queue attachment also validates the
actual attached file, project/session identity and rollback-journal format.

Both databases are on disk, use DELETE journals and FULL synchronization. SQLite
documents atomic commit across attached databases under these conditions; WAL is
explicitly excluded. See the [SQLite ATTACH transaction contract](https://www.sqlite.org/lang_attach.html).

The artifact file is published **before** this database transaction. SQLite and
the filesystem are not one transaction. The existing D004 journal owns recovery:

| Failure/crash point | Recovery and durable result |
| --- | --- |
| Transfer or before final file move | Incomplete staging discarded; prior selection retained |
| After file move, before registration | Complete unindexed orphan reported and retained, never selected/adopted |
| After index insertion, selection update or queue update, before commit | Index, selection and job success roll back together; file remains an orphan |
| After commit, before journal cleanup | Artifact, selection and completed job survive together; recovery cleans the committed stage |

An owning process dying before completion leaves its running attempt `interrupted`
when the project is reopened, using D006 recovery. Repeated completion after a
committed crash is idempotent. Orphans remain unavailable through the indexed read
API; D040 does not automatically adopt them or delete historical results.

## D007 integration and compatibility

`WorkerSupervisor` accepts an optional trusted synchronous `completion_handler`.
It runs on the coordinator thread only after verified protocol success, EOF, exit
zero and child/pipe cleanup. The handler validates/opens its operation's result
and calls the publication service. Worker-reported references are hints, never
selection authorization or executable paths. No worker protocol change is needed.

Ordinary D006 completion rejects jobs carrying the D040 snapshot; absent a handler,
the supervisor records a publication failure rather than false success. Existing
jobs without D040 metadata retain their previous completion behavior. Handler
failure before commit records failure and preserves selection. A cleanup exception
after durable publication preserves the already committed success for D004 recovery.
Future media operations must validate actual media before invoking publication and
schedule blocking publication on their owning coordinator outside the UI thread.

Legacy artifact imports and old workflow/API jobs continue to use their existing
contracts. The base D004 reader can read D040 artifacts. No project revision,
approval record, unrelated artifact or existing test was removed or weakened.

## Validation evidence (2026-09-13)

`backend/tests/integration/test_result_publication.py` exercises real temporary
SQLite databases and files, offline fixture streams and actual child processes.
It covers normal publication/reopen, B1 completion after B2, retained bytes and
snapshot history, unchanged A/C, both completion orders for revisions/generations,
duplicate replay, stale enqueue, cancellation/ownership, metadata substitution,
enqueue rollback, consumed-artifact replacement/ancestry, manual inputs, retry,
base format compatibility and unsupported publication format.

Six subprocess tests terminate with `os._exit(17)` at file/index/selection/queue
boundaries; they reopen the project and verify recovery, selected bytes, history
and job state. Five supervisor cases exercise real protocol processes, trusted
publication after cleanup, concurrent edit, failure, post-commit cleanup error and
the missing-handler guard. A fresh-process import test checks layer independence.

Final validation on Windows, isolated Python 3.11.9:

- Focused integration suites for D003/D004/D005/D006/D007/D040 plus worker protocol
  tests: **139 passed in 28.19 s**, including **39 new D040 cases**.
- `python -m pytest backend/tests`: **914 passed in 56.35 s**.
- `git diff --check`, new-file whitespace, relative documentation links and task
  status scope: **PASS**. Only D009 status synchronization and D040 implementation
  changed task entries; D002/D008 remain Partial.

No TTS, network, model or hardware acceptance is required by D040.
Process-crash evidence does not claim to simulate storage-controller failure
or arbitrary filesystem corruption. D044 hostile-path hardening, future migrations
and orphan garbage collection remain separate work.

# D006: durable local job queue

The queue retains operation inputs and truthful attempt outcomes across project
restart. It builds on the live D003 project session and D005 request/failure
values. It does not execute providers, start workers, schedule GPU resources,
resume inference, change active artifacts or connect legacy workflow execution.

## Model and composition

The existing `GenerationJob` DTO and its legacy statuses/serialization remain
unchanged. `JobRequest` is an immutable desktop operation: opaque job ID, logical
output key, D005 `RequestFingerprint`, strict JSON input snapshot, creation time
and prior artifact IDs. The snapshot contains the actual consumed text/values and
revision/artifact references supplied by its producer, not merely their hashes.
Producers must capture matching snapshots/fingerprints and omit credentials and
machine-specific paths. Editing caller-owned nested data cannot change a job.

Each enqueue creates attempt 1. `JobAttempt` has a unique attempt ID, job ID,
number, status, timestamps, owner, claim token, cancel flag, progress, outcome
references and error. Explicit retry after failure/cancellation/interruption
creates a new numbered attempt; it never resets history or changes the request.
A new variant after success is a new job, even if its request fingerprint matches.

`JobCoordinator` uses an injected `JobRepositoryPort` and timezone-aware clock.
It only issues queue commands; an external executor will claim, read the frozen
request and report events. The D006 tests simulate those events offline.

```python
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository

# session is an open D003 ProjectSession; request is a D005 RequestFingerprint.
queue = JobRepository(session.repository)
jobs = JobCoordinator(queue)
pending = jobs.enqueue("section-B:raw", request,
                       {"section_revision_id": revision.id, "text": revision.text})
claim = jobs.claim_next("executor-1")
if claim is not None:
    captured = queue.get_job(claim.job_id)
    # A later execution adapter consumes captured.input_snapshot_json.
    # It reports progress/outcome using this exact claim, never a fresh selection.
```

## Storage and ownership

`jobs.sqlite` is local to the configured project workspace, with schema version 1
and application ID `0x4149434A` (AICJ). It contains a project/session owner and
pause flag, immutable job request rows, and attempt rows. It does not migrate or
alter D003's project schema or D004's artifact index. Unsupported/unversioned or
foreign-project queues are rejected without repair. An interrupted first-time
initialization can leave an unversioned file requiring inspection.

D003 now gives each opened repository instance an ephemeral `session_id`, with
no persisted project-schema change. Queue commands verify that the owning
repository is still open on its coordinator thread. Reconstructing queue adapters
within that same session preserves claims. A new session can open the project
only after D003's exclusive writer lock is released; this is the recovery boundary.
There is no timeout-based lease that could expire during legitimate long inference.

Every mutating command uses `BEGIN IMMEDIATE`, the DELETE rollback journal and
`synchronous=FULL`. Claiming selects the oldest queued attempt and conditionally
sets its running state, owner and unique token in one transaction. Pause/cancel
and claim decisions are serialized by that same write lock. A partial unique
index allows at most one queued/running attempt per job. Contention returns
`JobQueueBusyError`; callers retry a command explicitly after contention ends.
Tokens prevent stale/foreign outcome events; they are local coordination values,
not authentication credentials or an exactly-once external-execution guarantee.

## State transitions

| Current state | Command/event | Persisted result |
| --- | --- | --- |
| queued | claim while unpaused | running with owner, token and start time |
| queued | cancel | canceled immediately, no executor claim |
| running | progress with matching token | running, updated phase/counts |
| running | cancel request | running with cancel_requested; no false terminal outcome |
| running, cancel requested | acknowledgement with matching token | canceled |
| running, no cancel request | completion with matching token | completed with reported result IDs |
| running | failure with matching token | failed with error; previous artifacts retained |
| running in an ended session | first queue open in new session | interrupted with retained progress and recovery reason |
| failed/canceled/interrupted | explicit retry | old attempt unchanged; new queued attempt |
| terminal | duplicate/late progress or outcome | rejected; terminal history unchanged |

Canceling an already terminal attempt is an idempotent no-op. A running cancel
request wins over a later completion event; completion is rejected until an
appropriate canceled/failed outcome is reported. Forced worker termination and
grace periods belong to D007. Pause persists and prevents new claims, while
already running work can still report progress or finish. It does not suspend
arbitrary computation.

Progress stores an explicit phase with optional non-negative integer completed
and total counts. Counts cannot regress within the same phase, and completed
cannot exceed a declared total. Loading can have no numeric estimate. Timestamps
use the injected clock and never regress below the attempt's last recorded time
if wall time moves backward. Recovery marks only running attempts, preserves
queued/terminal states and pause, and commits the new session identity together
with interruption outcomes so repeated opens are idempotent.

## Artifact and freshness boundary

No queue method writes/deletes artifact bytes, changes their metadata, or updates
active selections. Prior artifact IDs stay in the immutable request. Attempt
completion stores the executor's reported opaque references, not proof of media
validation or permission to select them. D004 validates/publishes files; D040
will gate revision-aware publication/selection before real generation is wired.

`JobAttempt.failure(job)` supplies D005 failure evidence. Failed work can coexist
with a fresh retained result; restart does not erase either. An interrupted
attempt cannot report completion after restart. Explicit retry uses a new attempt
and token; cached chunk reuse would require its own byte validation later.

## Validation and acceptance

Tests use a fake clock, synthetic executor command/events, temporary SQLite files
and actual child-process exits. They make no provider or network calls.

| Acceptance criterion | Evidence | Result |
| --- | --- | --- |
| Restart retains pending work | FIFO, frozen inputs and pause survive close/open and project movement | PASS |
| Abandoned active attempts become interrupted | Same-session adapter check, new-session recovery, actual process exits and repeated reopen | PASS |
| Two claims cannot acquire the same attempt | Independent SQLite write contention, two adapters, conditional claim and token checks | PASS |
| Failed attempts retain prior artifacts | D004 bytes/catalog and D005 fresh result survive failure and reopen | PASS |
| State, pause and cancel semantics | Queued cancel, cooperative active cancel, blocked late completion, terminal immutability and explicit retry | PASS |

Four subprocess crash cases exit before/after claim commit and before/after
completion commit. Uncommitted claims remain queued; committed running claims
become interrupted; only committed completions retain reported result IDs.
Additional checks cover enqueue rollback, duplicate IDs, invalid events/progress,
schema/owner refusal, D003 compatibility and imports without UI/storage/providers.

Focused validation (103 passed, including 26 D006 cases):

```text
python -m pytest backend/tests/integration/test_durable_jobs.py backend/tests/unit/test_t006.py backend/tests/unit/test_project_repository.py backend/tests/integration/test_durable_project.py backend/tests/unit/test_invalidation.py backend/tests/integration/test_dependency_index.py backend/tests/integration/test_artifact_publication.py
```

Full validation: `python -m pytest backend/tests` — **672 passed**, 44.92 s.
`git diff --check` and new-file whitespace checks — PASS.
Environment: Windows, isolated Python 3.11, 2026-09-12; synthetic fixtures only.

## Self-review and limits

The queue owns only job metadata and does not infer current editorial selections.
The D003 change is limited to session identity; legacy `GenerationJob` behavior,
provider contracts, workflow code and artifact schemas remain unchanged. Backup
or relocation must include the closed workspace's queue, project and artifact
databases plus referenced files. This remains a single-writer local project,
not a multi-process worker database or network-share service.

Recovery proves that the old coordinator lost its project session, not that an
external model process has been terminated. D007 must own worker lifecycle and
reject/stop obsolete workers. D006 has no heartbeats, automatic retries, polling
loop, GPU lease, UI, Redis/Celery or media validation. Power-loss durability on
unusual filesystems is not established by process-exit tests.

Recommended review: transaction and session boundaries, cancellation ordering,
retained attempt history and D005 failure mapping. No D007 or later task is part
of this branch, and this run does not commit, push or merge.

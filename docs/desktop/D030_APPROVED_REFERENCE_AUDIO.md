# D030 — Approved reference-audio intake

## Implemented boundary

The installed Chatterbox composition owns one `ProjectReferenceAudio` service per
open project. Intake accepts a bounded PCM WAV selected by the user, validates its
container and measured duration/sample rate/channel count, and publishes the bytes
as an immutable project artifact. Only the source filename, measurements, checksum
and opaque artifact ID are retained; the selected external path is not persisted.

Approval and rejection are separate immutable JSON artifacts. Every decision binds
the project, source ID and source checksum and points to the preceding decision.
The current decision is the head of that validated, non-branching chain. A later
rejection does not delete the source or any earlier approval, and a later approval
appends another decision rather than changing history.

## Resolution and synthesis

Selections contain only `reference_audio_artifact_id` and frozen approval metadata
(`checksum`, `approval_label`, `approved`). Before preview or enqueue, the project
service verifies the current approval and retained source bytes, then materializes
an immutable checksum-named WAV below the configured private reference cache.
Existing cache bytes are rechecked and tampering fails closed.

Preview and production both use the same prepared Chatterbox voice identity. The
controlled path is passed only across the trusted composition/worker boundary; it
is absent from selection JSON, job snapshots, manifests and provider identity.
The worker resolves the opaque ID plus checksum inside its private cache and the
provider records the reference content checksum in its effective identity. A
changed approval/checksum no longer matches the frozen request, while a new import
receives a new opaque source identity.

The desktop audio panel exposes the minimum workflow: import a WAV, inspect its
pending/approved/rejected state, append an approval or rejection label, and select
only currently approved references as Chatterbox voices. Piper and compositions
without a reference service keep these controls disabled. XTTS production policy
is unchanged.

## Validation evidence

Offline tests cover bounded/invalid intake, pending and rejected resolution,
append-only reapproval history, source and cache tampering, changed imports, project
close/reopen, UI state transitions, and the same approved opaque ID through managed
preview and section narration. The integration assertion also verifies that the
external source path is absent from the selection and queued job snapshot.

No private speaker sample was supplied for this task, so no new speaker-reference
recording or generated output was retained or committed. D029's separate real GPU
runtime evidence remains authoritative for the Chatterbox installation; the D030
reference-flow acceptance is deterministic and offline.

Final validation on 2026-09-23 (Windows, isolated Python 3.11):

- Focused reference, desktop audio, Chatterbox, preview/selection and packaging
  checks: 98 passed in the implementation run.
- Full `python -m pytest backend/tests -o addopts='' -q --tb=short`:
  1650 passed, 11 existing optional tests skipped in 768.09 s; exit code 0.
- `compileall`, `pip check` and `git diff --check`: PASS.

The existing D021 manual desktop smoke remains a separate dependency gate; this
offline D030 acceptance does not close that gate.

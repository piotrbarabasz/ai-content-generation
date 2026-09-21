# D024 — Selective regeneration composition

`RegenerationService` executes a finite graph of configured project outputs.
Rebuild-all inspects each output; a selected output visits only its prerequisite
closure. Each stage reports reused, rebuilt, review, unavailable, blocked,
failed, canceled or obsolete. A project revision change stops the remaining
operation. Inspection runs again after execution: completing a process alone
does not establish that its output is current.

`ProjectRegeneration` composes existing services on the owning project thread:

- D010 audio uses the configured voice selection and complete prepared request,
  including effective provider identity. D005 compares the bound request with
  retained dependencies, and checksum verification precedes reuse.
- D013 suggests a missing scene plan once. Acceptance remains an explicit
  editorial action. With an accepted plan, timing is regenerated only when its
  selected raw recording changes.
- D015 generates missing/stale automatic prompts using explicit brief/style
  revisions. Manual incompatibilities require review. Selection uses the token
  captured before generation, so late output cannot replace a manual edit.
- D017 generates missing images and selects them only if the prior choice is
  unchanged and D040 accepted the result. Imported and selected image variants
  remain retained choices. Incompatible generated-image prompts/settings/provider
  identities require review. Imported images have no prompt-generation edge.
- D023 timelines remain explicit pinned editorial snapshots. Missing or stale
  timelines require review through Timeline Lite rather than replacement.
- D046 proxies and D019 final renders consume that exact snapshot. Fresh proxy
  cache entries and renders are reused. Final render jobs continue through D040.

The standard image settings match the existing desktop defaults (512×512, seed
0). Composition can inject per-scene settings. Audio uses original narration;
this operation does not silently switch a processed timeline to raw narration.
Optional services are injected. Unconfigured stages report unavailable; no mock,
model installation or provider fallback is selected by the operation.

## Recovery and ownership

The operation does not add a second queue or database schema. Running rebuild
again after cancellation/restart re-evaluates persisted inputs and completed
outputs. D006 recovers running jobs as interrupted on project reopen. The latest
job for an output is retried only when its complete bound request matches;
queued matching work is continued, and different pending work is left alone.
Audio retries retain the same job ID and D010 chunk workspace. Image/render
retries use their existing services. D040 remains the publication authority.

Synchronous prompt/proposal writes are reused through their existing histories.
A crash between prompt generation and selection can leave an unselected
revision and require generation again; retained history is never discarded.
This is safe restart with persisted stage results, not an exactly-once guarantee
across provider calls and filesystem/SQLite writes.

The Qt dock offers all outputs or one target, cancel and per-output outcomes.
Dirty drafts or active audio/preview work block starting the operation. Project
switch/close is blocked until cleanup finishes. Existing synchronous D015/D017
provider calls retain their bounded-call contract: cancellation is cooperative
between those calls; D007/FFmpeg jobs retain their process-level cancellation.

## Validation

Offline integration tests use real SQLite, artifact bytes, D010 sentence/chunk
synthesis, D013 plans/timing, D015 prompt history and D017 image publication with
deterministic providers. They cover A–B–C reuse, B-only generations, retained
checksums, review gates, selected target closure, matching-job retries, reopen
recovery, unrelated queued jobs, late B1 publication and late prompt selection.
Qt tests exercise dirty-draft, busy-project and close guards and outcome labels.
Final commands and counts are recorded in the implementation plan.

# D071 Shorts analysis visibility repair

The subsequent [robust selection repair](D071_SHORTS_SELECTION_REPAIR.md) supersedes
the duplicate/overlap rejection described here. Malformed/unknown-ID responses
still fail; conflicts between known verified preferences now use deterministic
fallback with explicit provenance and no second service-level provider request.

Inspected branch: `perf/indexed-artifact-reads`, base `a792c6a`, 2026-10-18.
The existing untracked preview audit was left untouched. This is the user-selected
D071 repair; it does not change D072 or regenerate narration, images or video.

## Confirmed diagnosis

A disposable replay using the **unmodified HEAD** panel/service and a blocked
mock LLM produced `callback_fired=True, busy=True, timer_active=True, candidates=0`
while the status remained `Open Shorts to verify the final source and restore
retained edits.` A malformed mock response then showed `Invalid structured Short
analysis.` Editing the empty title replaced that error with `Unsaved Short edits;
save before export.`, with zero candidates and `dirty=True`.

The callback and timer work. Previously, the coroutine synchronously executed
source/currentness checks and the whole pool construction on the Qt thread before
its first await. Neither starting nor awaiting analysis reported a phase. Only
successful completion refreshed the ComboBox. Failures left it empty, and edit
signals could hide errors even without a candidate. Refresh only loads analyses
that were actually saved.

Read-only inspection of the existing Soviet Moon project's unlocked databases
and retained artifacts found **64 clips, 627.8 seconds, zero retained analyses**.
Its retained plan/boundary discovery produces **40 valid 30-60 second intervals
across five chapters**, taking 3.478 seconds in the diagnostic run. This rules out
an empty retained chapter/boundary pool; it does not establish the open editor's
live source-currentness state. The original attempt's provider response/error
was not retained, so its exact pre-publication failure remains unknown. No real
provider call was made to infer it.

The old LLM prompt supplied overlapping alternatives without their time coverage.
It now supplies verified coverage and explicitly requests non-overlap. Validation
still rejects malformed, foreign, duplicate or overlapping suggestions. No
automatic paid repair or retry was introduced.

## Fix and ownership

- Immediate Checking source; Finding excerpts; AI analysis (or labeled heuristics);
  source recheck; Saving candidates; Completed with the retained count.
- Phase-specific errors survive passive edit/load signals. Empty controls cannot
  become dirty. Zero intervals explain which Timeline coverage/boundaries to review.
- Success selects the first current retained candidate, preserving stale history.
- Busy blocks duplicate dispatch. Successful analyses are reused for matching
  source/pool, count, target and provider identity. Impossible distinct-candidate
  counts fail before a provider call. Failure/cancellation/save errors retain history.

Original-source analysis captures `ProjectVideoRender.frozen()` on the SQLite
owner. Detached publishing/plan/binding/timeline adapters use its in-memory catalog
and repository. Source validation, media/pool reads and LLM execution use workers.
Progress callbacks, live-input comparisons and append-only persistence stay on
the owner. Source checksums and current inputs are rechecked before saving; the
verified original-currentness receipt and boundaries are reused for restoration.
Source integrity and timestamp/domain contracts remain unchanged.

Derived sources keep their owner-thread lineage checks because their composition
can include a separate channel-library database; discovery uses detached media.
Initial tab activation/refresh and render staging retain their existing synchronous
behavior. This repair targets Analyze in Original Excerpt mode.

## Offline validation

```powershell
.\.venv-ci311\Scripts\python.exe -m pytest backend/tests/unit/test_shorts.py backend/tests/integration/test_shorts_panel.py backend/tests/integration/test_shorts_analysis_snapshot.py
.\.venv-ci311\Scripts\python.exe -m pytest backend/tests
git diff --check
```

Focused: **53 passed**. Tests cover actual Qt button/timer execution, three valid
saved/displayed candidates, all phases, a Qt heartbeat during slow discovery,
provider/malformed/save failures, persistent errors, refresh/reopen, empty dirty
state, duplicate suppression, cache/input changes, impossible requests, cancellation
and stale-history selection. Real SQLite-backed synthetic projects verify detached
source/boundary reads and corrupt-MP4 rejection. Panel tests forbid live OpenAI
calls. Production databases/media were not written.

Full suite: **2,580 passed, 12 skipped, one pre-existing duration-repair failure**
in **1,785.89 seconds (29:45)**. `git diff --check` passes. No new failing tests
were introduced. A separate offscreen visual check at 1050x850 confirmed the
selected first candidate, populated details/boundaries and visible Completed status.

The known unrelated
`test_d078_cloud_measurements_drive_duration_repair_without_images` also fails
on a fresh untouched `git archive HEAD` snapshot of `a792c6a` with
`Section synthesis incomplete; valid chunks remain available for retry.`
The isolated baseline reproduction took 6.94 seconds; this repair does not change
the duration-repair/audio path.

## Test the existing project without a paid request

1. Close the old app. From a new PowerShell terminal in the repository, launch:

   ```powershell
   $env:AICS_LLM_PROVIDER = ' '
   .\.venv\Scripts\python.exe run_app.py
   ```

   Whitespace is intentionally stripped to an empty provider selection and prevents
   `.env` from restoring OpenAI in this process. No settings file is edited.
2. Open **What If the Soviet Union Landed on the Moon First?** normally. Leave
   narration, images, Timeline and final export unchanged. Open **Shorts** and
   confirm the verified original is 1920x1080, approximately 627.8 seconds.
3. Set **Original Excerpt**, **original**, **Candidates 3**, **Target seconds 45**.
   Click **Analyze for Shorts** once. Checking source must appear immediately;
   watch discovery, labeled heuristics, saving and Completed. Controls disable
   during analysis; Cancel remains available.
4. Expect three retained candidates with the first current candidate selected,
   populated details/boundaries and no unsaved-edits warning. This appends a Shorts
   analysis; it does not render or regenerate parent media.
5. Click **Refresh verified source / retained Shorts**, then close/reopen the
   project and return to Shorts. Candidates remain available. Unchanged Analyze
   reuses the successful analysis.
6. Exercise provider/malformed failures with the offline tests. A configured live
   OpenAI run is optional and was not used as acceptance evidence. If another app
   instance owns the project, close it normally; do not bypass its database lock.

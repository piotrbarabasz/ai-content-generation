# D071 robust Shorts selection

Inspected clean branch `perf/indexed-artifact-reads`, HEAD `691f711` (visibility
repair), 2026-10-18. This user-selected repair changes candidate selection only.
Production projects/media and narration/image/Timeline generation are untouched.

## Reproduction and cause

An offline synthetic project has three 60-second chapters, each with a complete
chapter interval and two overlapping 40-second alternatives. A new regression
returned the first verified interval ID three times. Before the change it failed
at `application/shorts.py:225` with the exact reported error:
`Suggestions repeat or overlap; review distinct intervals.`

The service previously merged all model choices into a list and rejected the
entire list at the final uniqueness/overlap gate. The pool was valid; one conflict
prevented publication of all usable choices. The old tests explicitly expected
duplicate/overlapping responses to fail. Those expectations are superseded here.

## Selection policy

The provider schema remains strict: exact requested response count, bounded
assessment strings and known interval IDs. Malformed structure/fields produce
`ShortAnalysisError.category=invalid_response`; unknown IDs produce
`unknown_candidate_id`. Either rejects the entire response without substitution.
Insufficient non-overlapping pool capacity produces `insufficient_candidates`
with instructions to lower Candidates or review Timeline chapter coverage.
Capacity is checked before a provider request, including the no-LLM path.

For valid known preferences, their order is their rank. Keep compatible choices
in rank order; record discarded ranks/IDs as `repeated_id` or
`overlapping_interval`, including conflicting selected IDs. Global comparison uses
exact source-video intervals, including frame-padding overlap across chapters.

Remaining slots come deterministically from the same verified pool. Prefer unused
chapters, then existing pool order, which favors whole chapters/sections and target
duration. A capacity lookahead prevents a long preference or fallback from making
the exact requested count impossible. A preference that would block completion is
recorded as `overlap_prevents_completion`. This keeps the highest-ranked feasible
preferences while still returning exactly the requested count when possible.

AI choices retain the model's assessment and
`selection_provenance={method: llm, rank: N}`. Fallback choices retain the pool's
original heuristic title, reason, hook assessment, headline, text and timestamps,
with `method: verified_fallback` and no model rank. No-LLM discovery uses
`method: heuristic`. Every new candidate remains REVIEW; explicit editorial/audio
acceptance still gates rendering. Previous decisions, edits and assessments are
not rewritten. Older candidates without provenance are labeled as unrecorded.

Example UI summary: **Completed: 3 candidates. 1 AI-selected, 2 verified fallback
selections. Editorial review required.** Candidate details identify heuristic
fallbacks as not AI-ranked. Success populates and selects the first current
retained candidate through the existing visibility repair.

## Retained evidence and paid-call safety

The existing analysis fingerprint is unchanged. Matching successful complete
analyses, including legacy successes, are reused without a provider call.
No second service-level request is made to repair a ranking.

After structural/field/known-ID checks, but before conflict resolution and final
source checks, persist an append-only `shorts_analysis_response` receipt. It contains
only version, fingerprint, bounded known selection/assessment fields and
`UNREVIEWED_SELECTION_EVIDENCE`. No prompt, transport body, headers, credentials or
exception text is retained. The receipt is neither a candidate analysis nor an
approval and cannot enter the candidates list by itself.

Receipts are checksum-verified and matched to the same fingerprint on retry/reopen.
The full response contract and IDs are revalidated against the freshly verified
pool. Corrupt/malformed/foreign-ID evidence fails closed without another request.
Source integrity and currentness checks still run before publishing an analysis.
Thus a later source-check or analysis-save failure preserves recoverable paid work.
The final analysis references its receipt and records discarded preference reasons.

Receipt appends are excluded from the snapshot's source-input catalog comparison;
all other live input comparisons remain. Reads stay detached; receipt and analysis
writes stay on the SQLite owner. Timestamp/boundary mapping, source checksums,
branding restrictions and render approval checks are unchanged.

Provider failures before a valid response, malformed/unknown-ID outputs, or a
failure to persist the receipt cannot provide a recoverable selection receipt.
No secret-bearing raw response is saved to address those cases. Responses lost
before this repair cannot be reconstructed from absent evidence. All earlier
analyses and media remain intact.

## Validation

```powershell
.\.venv-ci311\Scripts\python.exe -m pytest backend/tests/unit/test_shorts_selection.py backend/tests/unit/test_shorts.py backend/tests/integration/test_shorts_panel.py backend/tests/integration/test_shorts_analysis_snapshot.py --tb=short
.\.venv-ci311\Scripts\python.exe -m pytest backend/tests
git diff --check
```

Focused: **72 passed**. Includes valid three-choice ranking, duplicate IDs, two and
three overlapping choices, frame-padding overlap between chapters, chapter
preference, blocking preferences, exact count, insufficient capacity, malformed
and unknown-ID rejection, legacy success reuse, accurate per-candidate provenance,
UI summary/first selection, receipt replay after publication/source failure and
reopening, receipt corruption/revalidation, and unchanged parent files/Timeline/
script. Real SQLite-backed synthetic storage confirms receipt publication does
not invalidate source verification or modify existing artifact bytes. Providers
are mocked; live OpenAI calls are forbidden in these regression modules.

Full suite: **2,599 passed, 12 skipped, one pre-existing failure** in **1,222.84
seconds (20:22)**. The failure is
`test_d078_cloud_measurements_drive_duration_repair_without_images`, with
`Section synthesis incomplete; valid chunks remain available for retry.` It is the
same baseline failure previously reproduced on untouched `a792c6a`; its cloud-audio
implementation and test are unchanged between that base and `691f711`, and by this
repair. There are no new test failures. `git diff --check` passes. No live OpenAI
calls or production-media modifications were performed; changes remain unmerged.

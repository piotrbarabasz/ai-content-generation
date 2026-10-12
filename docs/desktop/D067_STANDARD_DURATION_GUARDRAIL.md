# D067 — Standard Duration Guardrail

The previous Automatic loop completed narration and visual work one section at a
time. Read-only diagnostics reported out-of-profile narration as REVIEW, but no
workflow gate consumed that result. Visual work could therefore begin before all
narration was measured.

For a selected Standard plan, Automatic now completes or reuses every current
section's narration before scene planning, prompts, images, motion masters or
timeline building. The driver uses existing playback validation (checksum, PCM
parameters, section revision and selected voice), then reads SectionAudio metadata.
Duration is the exact sum of PCM frame-count/sample-rate ratios, independent of
word counts and planning estimates. Social and legacy ordering are unchanged.

PLAN_DURATION is OK within the existing inclusive 540–660 second profile. Outside
that profile, Automatic stops with a controlled PLAN_DURATION error showing the
plan target, measured duration, allowed range, target and nearest-boundary
deviations, and the required additional narration or reduction. Missing, stale or
invalid measurements are PENDING; they cannot authorize downstream work. Existing
status and summary widgets display these results without new UI surfaces.

The guardrail makes no script edits, selection changes or database migrations.
Run / Resume repeats current validation; existing script-group and audio caches
remain authoritative. Cancellation during narration retains completed artifacts.
Existing dependency identities still determine downstream reuse after edits.

## Validation

Offline tests cover 540/600/660-second passes, 489/700-second blocking, one-sample
boundary deviations, missing/stale/corrupt narration, current revision/voice
checks, downstream ordering, selective resume, repeated reuse, cancellation,
Social/legacy behavior and the existing Qt error summary. No external providers,
GPU or FFmpeg are used by the added tests.

Validation on 2026-10-12 with the existing isolated Python 3.11 environment:

- Final focused duration/orchestration/diagnostics/script-resume/editor/header run: **71 passed**.
- Broader focused run including existing audio-job and motion-master acceptance regressions: **87 passed** before the final header follow-up; all were also covered by the final full suite.
- `python -m pytest backend/tests`: **2,046 passed, 11 skipped** in 633.75 seconds.
- `git diff --check`: passed.

Interactive acceptance on the real 10-minute project remains pending. Acceptance
project databases and work directories were not opened or modified; implementation
and tests use an isolated worktree and temporary fixtures. This task measures
narration only; it does not change final-render timing or automatically expand
scripts. Processed audio remains subject to the existing downstream timing policy.

# D077 — Automatic Narration Duration Repair

The Standard plan's 132 words/minute is an editorial estimate, not a promise
about synthesized speech. The America 1776 project has all nine script groups
and eighteen narration artifacts, but its verified PCM totals 476.64 seconds.
Completeness therefore cannot imply the required 540–660 seconds. D067 correctly
stopped before visuals; the missing capability was a measured correction step.
The retained script contains 1,358 words: 170.95 measured words/minute overall,
with section rates from 147.81 to 202.06. At 132 words/minute those same words
would be estimated at 617.27 seconds. The drift is explained by actual speaking
rate, not missing narration or a faulty frame summation.

## Operator workflow

Choose the existing Standard plan and original/processed voice selection, then
use **Preview duration repair** in Plan. Preview reads verified selected audio
through playback validation and makes no LLM calls. It records frame counts,
sample rates, checksums, exact revision identities, section words, measured
speaking rates, planned versus actual seconds and the proposed correction.

Enable **Allow automatic duration repairs (paid LLM)** for this project/plan to
authorize bounded execution, then **Continue Automatic**. The service targets
the plan duration (normally 600 seconds), preferably near 590–610, while D067's
mandatory 540–660 range remains unchanged. Passing narration continues the normal
scene/image workflow. Preview alone never launches visuals or paid requests.

The planner ranks substantive chapters by measured deficit/excess, contextual
relevance and duration. It protects the cold open, introduction and conclusion,
distributes correction over at most three chapter groups, and calibrates each
section's proposed words using its own measured rate. A section grows by at most
80% or shrinks by at most 40% per round. Remaining correction stays visible;
an estimate can never pass the duration gate.

## Integrity and convergence

Version 1 policy and proposals use `measured-duration-budget-v1`. Repair words
are a separate budget, checked within 10% (minimum two-word tolerance). Ordinary
D064 generation retains its original ±25% validation. Continuity review uses a
repair budget only for the exact retained replacement revision. Plans, planned
section IDs, stable section identities and ordering remain immutable.

The configured structured provider receives the outline, full retained script,
narrative premises and adjacent context, with explicit per-slot word limits.
Validation checks fixed slots, existing narration quality, correction direction,
duplicate sentences, repeated-word padding and new spoken content. These checks
cannot certify historical truth or literary quality; retained responses and
proposals remain available for editorial review.

Only changed sections receive child revisions. An append-only selection intent
precedes atomic script selection, allowing recovery with the same revision IDs
after interruption. Existing `AudioServices` publication and dependency checks
reject stale narration, reuse unaffected selected audio, and regenerate changed
sections with the existing compatible frozen-job chunk recovery. No new synthesis
cache, global plan mutation, time stretching or fabricated silence is introduced.

Limits persist across cancellation, restart, timeout and checkbox toggles:

- Two rounds, three changed groups per round, two output attempts per group.
- Twelve structured calls per project/plan campaign, reserved before dispatch.
- At most two configured transport retries per call, hence at most 36 transport
  attempts for the default campaign; a timeout can still be billed.
- Lower limits may be set through `DurationRepairPolicy`; no UI budget reset.
- Stop for stale source/voice/provider identity, invalid narration, exhausted
  attempts, or improvement below two seconds or 5% of the previous target error.

Accepted text survives TTS failures; Resume generates missing/stale narration
before remeasuring. Paid responses and accepted group outputs survive interrupted
repair. Manual edits supersede automatic source identity and require review.
PASS requires newly verified selected PCM. The Plan tab retains measured results,
section calibration, proposed changes, round progress and status history.

## Acceptance evidence

Recorded on 2026-10-15 using isolated Python 3.11 and the configured structured
LLM/local managed Chatterbox runtime. Only the explicit real-copy acceptance used
an external connection; backend regressions remained offline.

Offline regressions cover calibration, in-range/no-call behavior, expansion and
compression, structure and lineage, duplicate/editorial/padding rejection,
selective audio reuse, stale audio rejection, timeout/cancellation/restart,
selection-intent recovery, two-round convergence/exhaustion, request limits,
historical D075/D067 behavior and prevention of premature images. Offscreen Qt
tests cover persisted opt-in, free preview and Continue after tab reparenting.

The initial live trial in `.tmp/d077a` retained two rejected LLM responses and
stopped at the configured group-attempt limit without changing script/audio.
It exposed insufficiently explicit slot budgets. The corrected prompt includes
each slot's minimum, maximum and target, overrides original editorial estimates,
and returns all observed counts in quality feedback. The failed campaign is
preserved; its limits were not reset. A fresh source backup `.tmp/d077b` is the
acceptance candidate for the corrected implementation. Generated project data,
private audio, runtime caches and model bytes stay ignored and outside Git.

The corrected candidate passed in one round:

| Measurement | Before | After |
| --- | ---: | ---: |
| Verified narration sections | 18 | 18 |
| PCM frames at 24,000 Hz | 11,439,360 | 14,352,000 |
| Exact narration seconds | 476.64 | **598.00** |
| Difference from 600-second target | −123.36 | **−2.00** |
| Script words | 1,358 | 1,752 |
| Measured words/minute | 170.95 | 175.79 |

Six sections in three substantive chapter groups received child revisions and
new verified audio; twelve original selected audio artifacts were reused. Five
structured calls were reserved, including two quality corrections; one call also
had a bounded transport timeout retry. The first local worker's startup timeout
recovered through the existing frozen-job retry. No LLM calls were repeated by
TTS recovery and no image provider was composed or invoked.

A separate process reopened the project, verified the same selected PCM and
obtained `PLAN_DURATION OK`. It found no further repair proposal and the paid-call
counter remained 5→5, with zero synthesis/image calls. All **63 original project
files** matched their pre-copy SHA-256 hashes. The candidate's detailed evidence
is retained locally in `.tmp/d077b/d077-evidence.json`, `d077-result.json` and
`d077-restart.json`; the original remains at its original location.

A second fresh process used the verified managed Chatterbox runtime and actual
production audio/provider composition. Run/Resume again returned 598.00 seconds
without any new provider or synthesis call (exit 0), verified checksums for all
**60 original artifacts**, retained the original script and child-revision
lineage, and preserved the twelve unchanged audio selections.

Focused validation: 96 tests passed across repair, workflow, Plan UI, measured
duration, continuity and planned generation (159.99s); the updated repair service
suite passed 23 tests (43.63s), the legacy recovery/driver suite passed 20
(19.69s), and the exact-revision continuity-budget check passed (3.90s).
Production-composition restart and `git diff --check` passed. The complete backend
suite passed **2,493 tests, with 12 skipped**, in 1,754.99s (29:14):

```powershell
.venv-ci311/Scripts/python.exe -m pytest backend/tests -o addopts='' -q --basetemp .tmp/d7g
git diff --check
```

This is narration-duration acceptance, separate from image generation, rendering
or the broader D075 human narrative/listening acceptance. No automatic merge was
performed.

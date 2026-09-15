# D018 — Immutable timeline description

## Boundary and decisions

Compile explicit ordered scene inputs into frozen versioned values. The domain
and application import only the standard library and existing domain values;
the compiler receives a `TimelineMediaPort`. No provider, job, UI, FFmpeg, new
dependency, schema or timeline publication is introduced. The legacy workflow
`RenderScene` and its estimated timing remain unchanged.

The read-only `ProjectTimelineMedia` adapter bridges the existing project-owned
contracts: explicitly chosen D013 timing/accepted scene, D011 original or
processed audio, and D016 selected image event. It verifies ownership, current
section revision, registered audio measurements/checksum and retained image
bytes. Timing must refer to the exact selected WAV and sample measurements;
changing voice or tempo requires explicit retiming. A missing processed variant
does not fall back to original audio. An imported/generated candidate is not a
selected image. Existing imported and generated image values use the same path.

Snapshot fields pin project/section/revision/scene/plan/acceptance/timing IDs,
timing quality, image selection event, immutable image measurements and artifact
ID/checksum, audio artifact ID/checksum, variant, sample rate, total source sample
frames and half-open source range `[start_sample, end_sample)`. File paths are
resolved by the configured existing store and never embedded in the snapshot.

The compiler resolves inputs again before returning and rejects a changed
selection. This is a synchronous operation on the owning project coordinator,
using D003's single-writer project session. It does not promise a new concurrent
multi-client transaction API. After return, subsequent edits/selections do not
mutate the snapshot or its pinned media. Future rendering must resolve these
exact artifact IDs and verify checksums, rather than reading current heads.

## Timing and ordering

Audio sample frames and output video frames are separate units. `AudioSpan`
validates integral positive measurements and `0 <= start < end <= frame_count`.
Duration is the exact `Fraction(end - start, sample_rate)`. Each clip's audio
offset is the sum of preceding exact durations, supporting mixed source sample
rates without resampling, floating-point drift or modifying source WAV files.
Recorded approximate tempo boundary quality remains approximate; exact sample
coordinates do not claim exact word alignment.

`OutputTimebase` is reduced positive **seconds per video frame**, e.g. `1/25` or
`1001/30000`. For each exact cumulative time, video boundaries round to nearest
frame, with ties up. Every clip starts at the preceding end; there are no output
gaps or overlaps. Quantizing each duration independently is deliberately avoided:
100 clips of 0.06 s at 25 FPS occupy 150 frames, with alternating 2/1-frame spans.
Total video duration differs from exact narration by at most half an output
frame. A clip whose boundaries coincide fails explicitly; no implicit padding,
sample trimming or scene dropping occurs. D019 must honor the full audio spans
and the recorded quantization when producing media.

Input order is explicit and scene IDs must be unique. Selecting a subset is
allowed: an omitted scene is a caller choice, while the remaining clips start
at output zero and retain their original nonzero source sample positions.
Reordering moves each exact audio range together with its image. Raw/processed
choice is explicit per input and must match that input's timing set.

The global `fit_policy` accepts `fit` (preserve the whole image within the output
canvas) or `fill` (cover the output canvas with aspect-preserving crop). D019 owns
the fixed output dimensions/profile and actual image transform; D018 records
the policy without resizing or rendering. Tracks, keyframes, transitions and
timeline UI are outside this task.

## Identity and serialization

`TimelineRevision.id` is `timeline_` plus a SHA-256 of canonical version-1 JSON:
ordered pinned inputs, exact rational offsets/durations, output frame bounds,
timebase, fit/fill and the named rounding algorithm. Recompiling unchanged
inputs yields the same identity; reorder, selected event/artifact, timing quality
or render-setting changes yield a different identity without generating media.

`to_payload()` returns an independent JSON-compatible value; `from_payload()`
reconstructs frozen nested values and validates version, rational canonical form,
measurements, ownership, unique ordering, contiguous offsets and content identity.
Ratios are `[numerator, denominator]`, never approximate seconds. The caller can
retain the payload using existing immutable storage; this task does not create
an active-timeline pointer or automatically save/replace history.

## Composition

```python
from app.application.timeline import TimelineCompiler, TimelineSceneInput
from app.domain.timeline import OutputTimebase
from app.storage.timeline import ProjectTimelineMedia

# index/store are the existing owning D040 project artifact pair.
compiler = TimelineCompiler(ProjectTimelineMedia(index, store))
snapshot = compiler.compile(
    project_id,
    [TimelineSceneInput(timing_id, scene_id, "original")],
    timebase=OutputTimebase(1001, 30000),
    fit_policy="fit",
)
payload = snapshot.to_payload()
```

## Validation

Windows, isolated `.venv-ci311`, Python 3.11.9, 2026-09-15. Tests use temporary
project stores, synthetic PCM/PNG, fake providers and a fake tempo process.

- Dependency baseline (D011/D013/D016): **99 passed**.
- Final focused D018: **70 passed in 15.68 s** (56 unit and 14 integration cases).
- First full backend run: **1 failed, 1332 passed, 11 skipped in 315.53 s**.
  The failure was the existing
  `test_t064.py::test_fifteen_minute_resume_invalidates_provider_and_reference_content_identity`,
  reporting a failed first TTS chunk. All 70 D018 tests passed. The complete
  `test_t064.py` then passed independently: **3 passed in 2.08 s**. No TTS code or
  tests were changed; the transient failure's cause is unconfirmed.
- Confirming full backend run: **1333 passed, 11 skipped in 320.05 s**. The 11
  skips are the existing D044 Windows symlink cases requiring unavailable
  privilege (`WinError 1314`); no D018 test was skipped.
- `git diff --check`, UTF-8/whitespace/EOF, task-field/dependency/acyclicity,
  milestone/P0 closure, changed-document link and D018-only status checks: PASS.

Focused reproduction:

```text
python -m pytest backend/tests/unit/test_timeline.py backend/tests/integration/test_timeline.py
```

| Acceptance criterion | Evidence | Result |
| --- | --- | --- |
| Every clip resolves exact selected inputs | Actual D011/D013/D016 project flow pins selected image/event and measured audio/checksum/sample span. Missing selections/candidates, corrupt or absent bytes, unknown/foreign IDs, changed sections and old timings fail. Processed audio requires its own explicit timing. Reopen gives the same snapshot. | PASS |
| Invalid/out-of-range spans fail | Negative, empty, non-integral and out-of-range source intervals fail. Pure fixtures reject output gaps, overlaps, duplicate scenes, incorrect rounding, noncanonical ratios and zero-video-frame clips. Mixed 44.1/48 kHz spans retain exact durations and cumulative offsets. | PASS |
| Reorder changes identity without regenerating media | Reverse order changes identity and offsets while preserving each image/audio binding, all retained artifact bytes, selection heads, job IDs and provider call counts. Old snapshots remain unchanged after later image choices. | PASS |

Additional tests cover 100-clip cumulative rounding without drift, ties up,
rational NTSC timebase, fit/fill and selection-sensitive identity, exact subset
source spans, nested immutability, validated independent JSON roundtrip,
selection changes during compilation and provider/storage/UI-free application
imports. Existing approximate processed timing quality is retained explicitly.

## Changed files

- `backend/app/domain/timeline.py`
- `backend/app/application/timeline.py`
- `backend/app/storage/timeline.py`
- `backend/tests/unit/test_timeline.py`
- `backend/tests/integration/test_timeline.py`
- `docs/architecture/domain-model.md`
- `docs/desktop/IMPLEMENTATION_PLAN.md`
- `docs/desktop/D018_IMMUTABLE_TIMELINE.md`

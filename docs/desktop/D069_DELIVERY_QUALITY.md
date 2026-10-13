# D069 — YouTube Export Quality & Audio Mastering

## Integration and retained contracts

DeliveryExportService consumes the selected verified project final MP4 or the current
D068 branded artifact. Branded selection uses D068's exact current-input fingerprint
and byte verification, never a cache/work-directory filename. D069 publishes separate
`delivery_settings` and `delivery_video` artifacts through existing atomic storage.
Original and branded selection, Timeline, narration, images, captions, motion and
historical renderer request identities remain unchanged. The branch is stacked on
local D068 (`f0a5e51`), including D067; neither dependency was merged in verified
origin/master (`92c1432`) at implementation time.

The Export tab adds compact Delivery Quality controls with explicit source, preset,
normalization, LUFS/true-peak targets and CRF. Existing branding/original actions remain
available. The source summary names its artifact, dimensions, FPS, duration and size.
Delivery results show measured loudness/peaks, bitrate, actual size and PASS/REVIEW.
No final-size estimate or visual quality score is promised. A small shared immutable
copy helper preserves D068's QSaveFile/checksum/path protections for D069 actions.

## Presets

Frozen DeliveryPreset values are centralized in `domain/delivery.py`; every operation
captures them and DeliveryConfig in its identity.

| Setting | Source / Original | YouTube High Quality |
| --- | --- | --- |
| Video | Byte-identical source copy | libx264, medium, CRF 18, High, yuv420p, progressive |
| Resolution/FPS/aspect | Unchanged bytes | Preserved, without upscaling or forced frame duplication |
| SDR | Unchanged bytes | Preserve known source color/range tags; no invented tags/tonemapping |
| Audio | Unchanged bytes; no normalization | AAC-LC, 48 kHz; mono 128 kb/s or stereo 320 kb/s |
| MP4 layout | Unchanged bytes | +faststart |

CRF is adjustable from 0–51. Measured bitrate is diagnostic; there is no minimum
video bitrate, artificial padding or guarantee of output size. Existing mono stays
mono. Source-copy builds require no encoder and publish a new delivery artifact with
the source checksum. Their status is SOURCE_COPY, not a new loudness-quality PASS.

## Loudness and verification

Defaults are **editorial choices**, not YouTube requirements: -16 LUFS, -2 dBTP and
11 LU LRA. With mastering enabled, FFmpeg analyzes source audio using structured
`loudnorm` JSON, then uses measured I/TP/LRA/threshold/offset with `linear=true` during
encoding. FFmpeg's documented dynamic fallback remains available. An explicit 48 kHz
resample, sample-aligned source span and continuous audio timestamps prevent filter
tail padding or timestamp gaps from extending delivery duration. See the primary
[FFmpeg loudnorm documentation](https://www.ffmpeg.org/ffmpeg-filters.html#loudnorm).

After AAC encoding, a fresh analysis records **input** loudness/true peak of the
encoded output, not the analysis filter's hypothetical normalized output. Actual
mode, pre/post measurements and target differences are retained. Non-finite silence
is represented as unavailable measurements, never valid narration. Sources below
-40 LUFS or requiring more than 24 dB gain skip normalization to avoid aggressive
noise amplification; the result is explicitly REVIEW. Normalization may be disabled.

Publication requires a nonempty MP4 with exactly one progressive H.264 High/yuv420p
video and one AAC-LC mono/stereo audio stream, expected dimensions/FPS/aspect/color,
exact video frame count, 48 kHz audio and source-compatible duration/synchronization.
Video tolerance is one source frame; audio tolerance is one 1024-sample AAC packet
plus two microseconds of probe rounding. Streaming packet audits validate contiguous
presentation times, frame count, audio duration and normal encoder priming. Full
audio/video decoding, stable size and SHA-256 checks precede publication.

Structural/probe/decode/analysis failures publish nothing. Structurally valid media
is REVIEW if final true peak exceeds -1.0 dBTP, normalization misses the integrated
target by more than 1 LU, or source/output is silent/unmeasurable. Review outputs are
retained with reasons and can be played/copied; the UI never labels them PASS. With
normalization disabled, loudness target difference is informational, but true-peak
safety and silence checks still apply. Bitrate is never a structural pass threshold.

## Identity, cancellation and reporting

Identity includes source artifact ID/checksum/size, exact preset/settings, CRF,
normalization parameters, algorithm version and effective FFmpeg/FFprobe hashes.
Matching verified results (including explicitly labeled REVIEW) are reused. Changed
source or preferences stale only dependent delivery output; previous histories remain.
Reports retain codec/preset settings, rate/dimensions, measured bitrate, A/V durations
and discrepancy, pre/post loudness and peaks, validation outcome, size and checksum.
The report accessor supplies its enclosing immutable output artifact ID.

Work directories are unique and contained. Inputs are checked again before atomic
publication. Cancellation/failure retain source and earlier outputs; retry starts a
fresh encode. Reopening restores preferences and resolves valid outputs by identity.
MediaProcess's stdout return behavior is unchanged; optional bounded stderr capture
supports measurement JSON and optional non-buffered stdout supports long packet
audits. Existing timeout/Windows Job/cancellation cleanup applies. No database
migration, provider installation, uploader, music or audio cleanup is introduced.

## Validation and limits

Changed files:

- Core: `application/delivery_export.py`, `domain/delivery.py`,
  `providers/ffmpeg_delivery.py`, `storage/delivery.py` (under `backend/app/`).
- Export UI: `desktop/delivery_panel.py`, `desktop/artifact_export.py`,
  `desktop/export_panel.py`, `desktop/branding_panel.py`, `desktop/editor.py`.
- Process capture: `backend/app/runtime/media_process.py`.
- Tests: `backend/tests/unit/test_delivery_export.py`,
  `backend/tests/unit/test_media_process_capture.py`,
  `backend/tests/integration/test_delivery_panel.py`,
  `backend/tests/integration/test_delivery_media.py`.
- Documentation: this file and `docs/desktop/IMPLEMENTATION_PLAN.md`.

Tests use fake processes, temporary projects, offscreen Qt and short synthetic CPU
FFmpeg clips. They cover byte-identical copy, options/two-pass measurements,
mono/stereo, silent/quiet sources, REVIEW thresholds, caches/staleness/cancellation,
reopening, original/branded source isolation and current export compatibility.
Real media covers SDR dimensions/rate, full decode, packet continuity, measured AAC,
1080p30 and delivery mastering of an actual D068 finishing result. The broad focused
rendering/export run passed 105 tests; the final focused follow-up passed 69 tests.
The full `python -m pytest backend/tests` run passed **2,116 tests**, with **11 skipped**
in 773.51 seconds. `git diff --check` passed.

The selected MP4 from `acceptance-standard-10min-001` was read through a read-only
artifact-index connection, verified against its manifest, and copied to an isolated
ignored work directory. Its HQ build passed: 1920×1080, 25 FPS, 12,236 frames,
489.44 seconds for both streams, H.264 High/yuv420p and mono AAC-LC at 48 kHz.
Source measured -17.90 LUFS / +0.42 dBTP; final AAC measured **-16.12 LUFS / -1.70
dBTP**, with dynamic loudnorm fallback, zero A/V duration discrepancy and successful
packet/full-decode checks. Output size was 146,131,670 bytes. Original MP4 and index
database SHA-256 values were unchanged. No branded artifact existed in this project;
the separate real D068 integration test covers that source.

The snapshot exercises the real encoder and validator, not an application project
session. Interactive opening/playback/save-copy acceptance remains pending; controls,
copy, reuse and changed-settings behavior are covered by isolated automated tests.
The original acceptance project was not opened or modified. Supported HQ
inputs are verified progressive SDR H.264/yuv420p and mono/stereo AAC-LC MP4s, not
arbitrary external/HDR media. Unknown color tags stay unknown. HQ is lossy re-encoding
and guarantees neither file size nor a visual quality score. AAC can shift peaks,
which is why quality is measured again after encoding. Long encoding resumes through
validated cache/retry rather than partial frame checkpoints.

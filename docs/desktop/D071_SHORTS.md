# D071 - Shorts & Repurposing Engine

The 2026-10-18 [analysis visibility repair](D071_SHORTS_ANALYSIS_REPAIR.md) records
the current Original Excerpt analysis phases, detached worker reads, reuse,
regression evidence and operator verification steps.
The subsequent [robust selection repair](D071_SHORTS_SELECTION_REPAIR.md) resolves
known duplicate/overlapping preferences without another provider call, records
fallback provenance and retains replayable unapproved selection evidence.

## Integration and ownership

Branch `feat/d071-shorts-repurposing` is based on local D070 commit `cb69d32`.
The original checkout was clean at `c9e1890`. Fetching origin/master confirmed
`92c14326e1817991538e2da1e1744c1fa3a57595`; D067-D070 are local stacked
implementations, not merged upstream dependencies. No original checkout files,
project databases or user media were replaced.

`application/shorts.py` composes D070 source ownership/current-Timeline checks and
its verified D068/D069 composition mapping. It uses the existing configured LLM
only on explicit Analyze; its strict schema permits supplied interval IDs and
editorial assessments, never timestamps or invented scene references. Without an
LLM, chapter/section/sentence coverage produces explicitly labeled heuristic
REVIEW candidates, preferring distinct chapters and always excluding overlap. Whole chapters and
sections are preferred over shorter boundary combinations. Conclusion-only
coverage is excluded. Suggestions are not evidence of audience retention.

Source boundaries come from `ProjectTimelineMedia.boundaries`: verified measured
sentence blocks, otherwise existing scene edges. Source shot frame offsets remain
separate from exact audio sample offsets. The renderer rounds video outward by
at most one frame; it trims audio at the exact speech edges and pads only that
small frame remainder with silence. An excerpt edge coinciding with a branding
insertion must also be an exact video frame; ambiguous card edges fail closed. Excerpts cannot cross an inserted branding
card. Every actual frame-aligned output must remain 30-60 seconds.

`ProjectShorts` retains append-only selection receipts, analyses, decisions, edit revisions, media,
SRT, metadata, packages and successful selections through configured storage.
No parent head is selected or rewritten. Reanalysis retains accepted candidates
and edits. Identical edit saves reuse the revision; source SHA, Timeline, edit
revision, captions, encoder settings and executable hashes pin output identity.
Failures/cancellation retain the preceding complete selected Short. Cache and
selection verification include MP4 checksum/decode evidence and the complete
package, metadata and optional SRT. Changed source candidates become STALE.

`providers/shorts.py` is the injected renderer contract. The composition factory
constructs only trusted local FFmpeg/FFprobe; a missing encoder does not prevent
opening projects or analyzing candidates. `MediaProcess` owns execution and
cancellation. The adapter probes source streams, exports one contiguous excerpt,
checks H.264/yuv420p, AAC 48 kHz, 1080x1920, exact FPS/frame count, duration/A/V
consistency and packet timestamps, then fully decodes before immutable publication.

## Desktop and packages

The Shorts workspace restores decisions and saved edits. It offers source status,
candidate count and target duration, exact boundary selectors, audio/video source
playback, per-shot normalized focal points, actual encoded-frame crop preview,
fit/blur or fill, measured caption/SRT reuse and optional burn-in, font/color/safe
margins, and a separate fading hook zone. Render selected/batch runs through the
Qt event-loop driver, with process progress and cancellation. Save-copy uses the
existing verified atomic export helper.

Each independent ZIP contains `short-01/short.mp4`, `metadata.json`,
`description.txt` and optional `captions.srt`. Metadata pins parent/source/Timeline,
exact interval, sections, presentation, caption identity, codecs, FPS, output SHA
and renderer version. Description names the excerpt and existing documentary;
this does not duplicate D070's complete publishing workflow. MP4, metadata and
SRT can also be exported individually. No upload adapter is constructed.

## Rendering and editorial limits

- Fill is a crop/upscale of existing widescreen content; it creates no new image
  detail. Fit preserves all source content over a blurred source background.
- Existing source motion and hard cuts are preserved; this is not an NLE or
  subject-tracking model. Review every shot's focal point. Existing burned source
  overlays cannot be removed from a completed MP4.
- Speech boundaries do not prove semantic independence, natural silence or a
  compelling hook. REVIEW candidates require human audio/editorial inspection.
- Automatic captions require complete measured word alignment and exact lineage.
  There is no proportional timing, implicit model download or new transcription.
  Missing alignment produces an actionable error; captions can remain disabled.
- Caption wording is retained, clipped and rebased. Burn-in wraps to at most two
  lines and rejects overly long text rather than shrinking into unreadable type.
  Hook and subtitle zones are separated; manual claims still require review.
- Source verification and artifact staging are synchronous owning-session reads;
  expensive first-source validation may pause the UI. Encoding, decoding and LLM
  execution yield to the desktop event loop. Human audible/device playback
  acceptance is not established by offscreen/CPU tests.

## Validation

Focused validation covers 101 distinct cases across the Shorts, desktop shell,
publishing and Video Plan test files (including the final boundary and layout
checks). New Shorts cases cover strict mock LLM references/errors, exact time
mapping, duration/unsupported cut rejection, overlap/crop math, subtitle rebasing
and source identity, missing alignment, safe overlays, fingerprints, identical
save/reuse, selective crop/hook rebuilding, reopen, cancellation, corrupt or
invalid output rejection, parent preservation and responsive/offscreen controls.
Two real CPU FFmpeg smoke cases render 30-second fill (25 FPS) and fit/blur
(30 FPS) outputs, burn
captions/hooks, inspect dimensions/FPS/packet timestamps/full decode, and verify
source beeps at 6/21 seconds become output beeps at 5/20 seconds after trimming.

The existing `acceptance-standard-10min-001` project was copied to a new verified
snapshot under `.tmp/d071-acceptance/project`; original SQLite files were opened
read-only for backup/inspection. Its actual selected source is **489.44 seconds**,
1920x1080 at 25 FPS, with SHA-256
`a6c440eff6531aea46f500982234e17c2fe9298260ce6d884db6c0631323d5bc`.
The project ID does not establish a ten-minute measured duration. No duration
repair or parent regeneration occurred.

Six heuristic chapter suggestions were inspected to select three distinct
covered topics; default discovery still requests three. Fixture accept decisions
are machine acceptance evidence, not a claim of human editorial approval.

| Excerpt | Verified source interval (seconds) | Output duration |
| --- | --- | --- |
| The First Minutes | 115.08-158.56 | 43.48 s |
| Payments and Banking | 175.44-226.68 | 51.24 s |
| Hospitals and Critical Infrastructure | 328.00-386.40 | 58.40 s |

All three and a separate crop-only rebuild passed probe/packet/full decode at
1080x1920, 25 FPS, H.264/yuv420p and mono AAC 48 kHz. The first Short's per-shot
crop changed to focal point 0.36; the other two MP4 checksums and parent source
checksum remained unchanged. A fresh project session restored all decisions,
settings and selected artifacts and reused all three outputs. Muted automated
Qt playback received about 50 valid frames and advanced roughly two seconds for each
selected MP4 with no media error. Offscreen visual QA confirmed readable controls
and a full-height vertical preview without overlapping buttons.

Caption enablement was attempted and correctly blocked: **28 sections lack
complete measured word alignment**. These acceptance exports omit SRT/burned
captions. Complete measured-caption clipping, identity, burn-in and SRT/package
paths are covered with offline fixtures and the CPU smoke. No alignment model,
LLM, GPU, image or TTS provider was invoked for the documentary.

After acceptance, **both original database SHA-256 values and all 426 existing
artifact SHA-256/size values** remained unchanged. Selected output copies,
packages, previews and the detailed acceptance report are retained under
`.tmp/d071-acceptance/results/` (final selected copies in `selected-verified/`); they are ignored and are not committed.
Human audible playback, crop/editorial approval and a configured real LLM run
remain external acceptance checks.

Final `python -m pytest backend/tests`: **2,211 passed, 11 skipped** in
776.44 seconds, using the isolated Python 3.11 environment. The 11 skips are the
existing optional Windows privilege cases. The final Shorts-focused run passed
**40/40** cases in 65.48 seconds; 61 existing publishing/Video Plan/desktop cases
also passed in the focused checks. `git diff --check` passes. No network, GPU or
real model execution was required.

## Changed files

- `backend/app/application/shorts.py`: dedicated analysis/edit/render orchestration.
- `backend/app/application/publishing_package.py`: excludes independent Shorts
  artifacts from the unchanged-upstream verification cache key.
- `backend/app/domain/shorts.py`: exact intervals, captions, framing and overlays.
- `backend/app/storage/shorts.py`: immutable project histories and complete output verification.
- `backend/app/providers/shorts.py`, `ffmpeg_shorts.py`: injected port and CPU encoder.
- `backend/app/desktop/shorts_composition.py`, `shorts_panel.py`, `editor.py`: optional
  composition, review workspace and navigation/process cleanup guards.
- `backend/tests/unit/test_shorts.py`: domain/service/storage regressions.
- `backend/tests/integration/test_shorts_media.py`, `test_shorts_panel.py`: real
  media and offscreen workspace coverage.
- `backend/tests/integration/test_project_editor.py`, `test_project_shell.py`:
  include the new workspace in existing navigation contracts.
- `docs/desktop/IMPLEMENTATION_PLAN.md`, `D071_SHORTS.md`: task boundary and evidence.

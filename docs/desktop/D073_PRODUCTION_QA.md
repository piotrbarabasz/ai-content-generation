# D073 - Production QA & Acceptance Suite

## Verified repository and architecture

The original checkout was clean on `fix/standard-video-plan-structure-contract`
at `c9e189095d1fb84f66787bb29ee07d8e4054574f`. Fetch verified origin/master
`92c14326e1817991538e2da1e1744c1fa3a57595`. Implementation uses the isolated
`D:/Projects/ai-content-generation-d073` worktree, `feat/d073-production-qa`,
based on D072 `253f73a`. D067-D072 are local stacked branches, unmerged upstream;
their remaining hardware/live-provider/human gates were not assumed complete.

Existing pipeline diagnostics assess generation readiness; the legacy QA module
assesses scripts. Neither inspected completed MP4 signal/visual quality. D073 is
a separate application service with `QAContext`, `QAValidator`, `QAFinding`,
`QAReport` and `QARunConfiguration` contracts and independent validators.

The local inspection adapter reuses `MediaProcess` cancellation, independent
deadlines and Windows process containment. The only shared runtime change is an
optional bounded streaming stderr-line callback; existing stdout/stderr behavior
is preserved. Decoded loudness JSON uses the existing D069 parser. Source adapters
reuse frozen Timeline, `CompositionTiming`, caption/alignment and D071 clipping
contracts. No encoder, TTS, image provider or generation workflow is changed.

Production composition constructs only trusted local FFmpeg/FFprobe inspection.
The existing configured LLM contract is text-only; it does not accept images.
Semantic review is therefore UNAVAILABLE when explicitly requested. No provider
is invented, constructed, called or sent images/scripts by production QA.

## Scan and source contracts

Quick QA streams the source SHA-256 and verifies ownership/size/retained render
evidence, current mappings and available captions/publishing assets. FFprobe reads
container metadata without `-count_frames` or full-frame filters. One video and
one required narration audio stream must satisfy the retained production geometry,
FPS, frame count and duration; supported video is progressive 8-bit H.264
baseline/main/high with yuv420p, audio is mono/stereo AAC-LC at 48 kHz. Historical
unspecified field order/profile is accepted only as documented in the validators.
Unknown frame count needs Deep QA; low bitrate alone is not a defect.

Deep QA includes Quick and separately executes full audio/video decode, decoded
silencedetect/loudnorm, black/freezedetect/cropdetect, bounded shot midpoint dHash
sampling and scene pacing. Similarity comparisons use distinct verified shots;
exact reused source-image SHA values are checked across shots. Repeated frames
within a scene are never treated as repeated scene images. Dark borders, low
motion, repetition, pacing and mastering are editorial guidance. Fit padding,
known cards and explicit static policies are accounted for. These checks cannot
prove subject loss, missing words, visual relevance or a factual error.

Source adapters use actual retained contracts:

| Production | Verified mapping |
| --- | --- |
| Original Standard/Social | Frozen render Timeline, selected render, current section revisions, motion and plan/script binding |
| D068 branded | Exact source ID/SHA, branding settings, measured insertion boundary, card intervals and caption shifts |
| D069 delivery | Exact source/preset/settings, preserved frame count/duration and normalization targets |
| D071 excerpt | Exact source/edit/selection, original shot intervals, unpadded speech duration and verified caption clipping/rebasing |
| D072 directed | Exact parent/script/narration/storyboard/image/caption/finish identities and selected output; its new narration duration comes from the retained measured narration record |

Standard acceptance uses measured narration 540-660 seconds. Social/Short
acceptance uses 30-60 seconds. Intro/outro time is excluded; planned narration,
actual narration, original video, branded video, delivery video and selected media
duration remain separate measurements. Unverified mappings yield UNAVAILABLE,
never estimated acceptance. Actual MP4 duration/frames are checked independently
against the source's verified output contract.

Captions require exact Timeline, audio/text/alignment identities, valid UTF-8 SRT,
monotonic nonzero/nonoverlapping timings and bounds. Existing word alignment is
revalidated before reporting speech coverage. Original omission is INFO. Director
sentence-caption timing is checked against its own narration identity and SRT;
independent speech coverage remains UNAVAILABLE because QA does not open/create
the child synthesis service or use caption spans to invent speech evidence.

## Thresholds and bounded resources

All thresholds are retained in versioned configuration and QA cache identity.
They are editorial preferences, not universal YouTube requirements.

| Setting | Default / limit |
| --- | --- |
| Editorial integrated loudness | -16 LUFS, tolerance 1 LU |
| Editorial true peak | -1 dBTP |
| D069 inheritance | Enabled; verified normalization targets replace editorial defaults |
| Silence | Below -40 dB for at least 3 seconds |
| Black | At least 0.4 seconds; pixel threshold 0.1, picture ratio 0.98 |
| Freeze / low motion | -60 dB difference for at least 12 seconds; advisory |
| Shot length guidance | Below 0.8 or above 20 seconds |
| Repeated pacing | At least 5 shots, 80% with identical durations rounded to milliseconds |
| Similar-image dHash | 64 bits, distance at most 6; advisory |
| Excessive vertical Fill crop | Retained source width fraction below 0.4; advisory |
| Dark-border estimate | At least 25 detections retaining less than 85% area; Fit classified INFO |
| Caption readability | At most 42 characters per line, 2 lines; advisory |
| Aligned speech coverage | Below 90% yields REVIEW when independent verified speech exists |
| A/V tolerance | 0.1 seconds, with frame/AAC packet tolerance |
| Frames | 36 by default, configurable 1-120, distributed over verified shots |
| Evidence | 12 by default, configurable 0-24; PNG previews at most 384 pixels, 2 MiB each |
| Interval results | 300 by default, configurable 1-2000 per signal; truncation is explicit |
| Validator deadline | 900 seconds, configurable 1-7200; cancellation terminates owned processes |
| Process buffers | stdout at most 1 MiB; stderr tail/individual line at most 64 KiB |

Checksums read 1 MiB chunks. FFmpeg streams to null sinks; full video files are
not loaded in memory. Private QA work directories resolve through configured
storage. Evidence extraction is bounded and never writes into source media.
No GPU or paid service is needed. Representative retained samples are marked
as measurements, not findings. Metadata/UI freshness checks stream the source
checksum synchronously; exceptionally large/slow storage can delay refresh.

## Reports, decisions and desktop integration

Reports retain source ID/SHA, schema/engine/validator/tool identity, configuration,
mapping, outcomes, actual measurements and stable findings. Findings include
severity, rule/version, source identity, measured/expected values, explanation,
action, available timestamps/scene references and owned evidence. No fake score.

Integrity or deterministic output failures remain BLOCKED. Unresolved REVIEW
findings or important unavailable/skipped requested validators prevent READY.
Source/mapping/settings/tool changes produce STALE; cancellation retains a
CANCELED report alongside earlier completed reports. Reuse is explicit in the UI
and verifies bytes and exact inputs; incomplete requested checks are not reused.

Append-only note/confirmed/intentional/resolved decisions never rewrite raw
measurements. Only REVIEW findings can be acknowledged as intentional/resolved;
resolution requires a newer completed run against the same exact source that
executes the relevant validator and no longer contains that rule. A new source
revision receives its own report and makes older history stale.

The Quality tab provides source/history selection, Quick/Deep, configuration,
reuse, cancellation, findings/details, evidence samples, separate review controls,
report export and explicit timestamp playback. Scanning uses the existing desktop
async-loop pattern; no QA starts on project open, source refresh or tab selection.
Project changes/close wait for scan cleanup. JSON and Markdown are retained
`quality_*` artifacts; export copies use the existing exclusive-copy helper.

Publish displays current QA state and includes the report ID/SHA/state/decision
snapshot in package identity and metadata. Unresolved REVIEW requires an exact
report-bound acknowledgement. That acknowledgement never makes blocked, stale
or incomplete QA approved. Manual publishing/export remains available subject to
its existing source-integrity gates. QA records are excluded from the production
verification catalog so they do not invalidate generation identities.

## Validation and real acceptance

Focused QA tests: **54 passed in 17.19 seconds** on Windows, isolated Python 3.11.
They cover valid/invalid streams, duration boundaries, original/branded/delivery
and caption mapping, decoded signal measurements, optional unavailable semantics,
review restrictions/cache/staleness/cancellation/reopen/export, publishing identity,
desktop explicit scanning and real CPU FFmpeg three-second synthetic media.
Final broader focused QA/publishing/process/editor/shell checks: **120 passed in
90.68 seconds**. Complete `python -m pytest backend/tests`: **2300 passed,
11 skipped in 932.62 seconds**. Existing optional skips do not prove hardware or
live-provider acceptance. The initial full run exposed an outdated shell tab-list
assertion; it was updated to include Quality, then the complete final suite passed.
`git diff --check` passed.

The acceptance project was copied using read-only SQLite backup and verified
artifact copies into `.tmp/d073-acceptance/project`. QA never opened the original
workspace for writing. Its 426 artifacts total 277,879,108 bytes.

| Acceptance evidence | Measured result |
| --- | --- |
| Original selected artifact | `artifact_db6e7cbf03e94324957aef11d88ace98` |
| Original SHA-256 | `a6c440eff6531aea46f500982234e17c2fe9298260ce6d884db6c0631323d5bc` |
| Technical output | 1920x1080, 25 FPS, 12,236 decoded frames, H.264/AAC mono 48 kHz |
| Planned / actual narration | 600 / 489.44 seconds; **BLOCKED**, below 540-second Standard minimum |
| Decoded mastering | -17.90 LUFS, +0.42 dBTP; two REVIEW findings |
| Visual inspection | 49 verified shots; 36 samples; no detected black/freeze or repeated visual finding in the original scan |
| Original captions | Not requested; no alignment-derived coverage was invented |
| Retained output | Quick/Deep reports, JSON/Markdown exports, up to 12 representative PNG previews, note and publishing acknowledgement |
| Reopen | Latest exact-source Deep report remains BLOCKED, not QA approved |

The report/export copies are in `.tmp/d073-acceptance/`; original snapshots are
retained locally and excluded from Git. Copies of actual D071 and D072 output were
also inspected under `.tmp/d073-excerpt/` and `.tmp/d073-director/`. The D071
selected output `artifact_793d2df0b17346a4a17420a2415d7bd9` is REVIEW_REQUIRED
for mastering and potentially excessive Fill cropping. The directed output
`artifact_1fcda8ec66434b99b3b4731974458587` is REVIEW_REQUIRED for caption line
lengths, mastering and exact reused images across distinct shots. Director speech
coverage is explicitly UNAVAILABLE. No semantic/cloud check was executed.

A separate isolated real CPU D019/D040 production run rendered a source, scanned
it, changed Fit to Fill through normal rendering tools and published a new render.
The preceding QA became STALE; the replacement scan was retained. Its historical
fixture lacks a Video Plan, so narration acceptance stays UNAVAILABLE and the new
report is REVIEW_REQUIRED. QA preserved all preexisting fixture assets and heads.
The large original acceptance movie was not regenerated merely to prove this.

Offscreen Quality layout/evidence rendering was inspected at 1100x900 with Segoe
UI explicitly loaded for the headless fixture. Qt's existing media backend also
decoded a valid original-video frame after the explicit 4,760 ms sample seek,
with audio muted. This does not prove packaged playback or human audible/editorial
acceptance. Human review remains pending;
D073 is recorded Partial until that acceptance is supplied.

## Changed files

- Contracts: `backend/app/domain/production_quality.py`.
- Service/adapters/validators: `backend/app/application/production_quality.py`,
  `quality_sources.py`, `quality_validators.py`.
- Inspection boundary/local adapter: `backend/app/providers/production_quality.py`,
  `ffmpeg_quality.py`; optional callback in `backend/app/runtime/media_process.py`.
- Retained QA storage: `backend/app/storage/production_quality.py`.
- Desktop: `backend/app/desktop/quality_composition.py`, `quality_panel.py`,
  `editor.py`, `publish_panel.py`.
- Publishing snapshot: `backend/app/application/publishing_package.py`.
- Tests: unit/integration `test_production_quality.py`; existing editor/shell tab tests.
- Documentation: this evidence note, implementation-plan registration and
  architecture overview.

No merge or push is authorized by this task.

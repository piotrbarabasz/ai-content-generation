# D072 - AI Shorts Director

## Verified integration and ownership

The original checkout was clean on `fix/standard-video-plan-structure-contract`
at `c9e189095d1fb84f66787bb29ee07d8e4054574f`. A read-only fetch verified
origin/master `92c14326e1817991538e2da1e1744c1fa3a57595`. The isolated worktree
`D:/Projects/ai-content-generation-d072`, branch `feat/d072-ai-shorts-director`,
starts from D071 `dd943050fcf0ec5ad60fb6b3bf3c482efdeb0028`. D067-D071 are local
stacked implementations, not merged GitHub dependencies.

D071 verifies selected final media ownership, checksum, Timeline, accepted source
candidate and source section revisions. AI Director requires that accepted candidate
and adds an explicit production mode. Original Excerpt is still the default and
uses its existing extraction/rendering path. Opening projects, choosing candidates
and switching modes do not call generation providers.

The existing supervised audio contract requires a project's own active script and
section revisions. To preserve that contract without changing the parent, Director
creates a child ProjectSession in configured `work/director/<lineage hash>` storage.
Its immutable full lineage marker includes parent, candidate, source checksum,
language and derived project ID. The child owns narration sections, jobs and audio
selections; it never changes the parent active script, audio selection or Timeline.
This task boundary adds composition/ownership, not a new synthesis engine.

Parent-linked `shorts_director_*` artifacts retain scripts, proposals, approvals,
voice choices, measured narration manifests, storyboards, images, finishing settings,
MP4s, SRTs, metadata and complete selections. All revisions are append-only.
D070 already excludes `shorts_*` artifacts from upstream publishing identity.
Narrative revisions, measured visual scenes and technical TTS chunks stay separate.

## Production stages

- **Story:** The existing injected structured LLM receives exact saved selected and
  neighboring passages, their verified IDs, topic, language and source visual style.
  Strict validation rejects unknown references, incomplete parts/beats, mismatched
  narration and unsupported generated numeric claims. The default CTA is empty.
  Generated full scripts become retained proposals; replacing a working revision
  is explicit. Manual revisions take precedence. Hook-only regeneration changes
  only the hook; all facts and proposed concepts require human review.
- **Voice:** Existing Chatterbox choices appear first when installed; configured
  providers remain behind existing AudioServices contracts. Identity includes
  text, voice selection and prepared effective provider identity. TTS runs in the
  child through supervised jobs/publication. Actual PCM frames, not provider duration
  claims, determine length and deviation. Narration outside 30-60 seconds blocks
  storyboard, image generation, captions and rendering. Listen/approve is explicit.
- **Visuals:** Each new measured sentence becomes a scene with cumulative video
  frame rounding. Original selected images are verified, with motion masters traced
  back to clean stills; relevance must be justified, never inferred from timestamps.
  The visual LLM can choose only supplied image/scene IDs. Missing images use the
  existing image-provider contract, source style and single-composition prompts.
  Exact current request count/provider/model requires approval; no reliable cost is
  invented. Successful images survive cancellation. Prompt edits regenerate only
  that missing image, while crop changes preserve narration and image bytes.
- **Finish:** Captions come exclusively from new narration's verified measured
  sentence blocks, with text/audio identity and labeled sentence timing. One clean
  still gets one restrained motion pass; per-scene fill/focal or fit/blur and optional
  gentle pan are supported, with no shake. The fading 2-4 second headline occupies
  its own safe zone. Optional D068 logo reuse excludes intro/outro cards. MP4 export
  uses source FPS, 1080x1920 progressive H.264/yuv420p, AAC 48 kHz and faststart.
  Exact frame count, A/V duration, packet timestamps, checksum and full decoding are
  checked before a complete MP4/SRT/metadata selection is published.

The desktop provides Story/Voice/Visuals/Finish tabs, source playback, editable
parts/full text, factual review, voice/duration/listen, per-scene image and actual
vertical previews, missing-image approval, caption preview and local export copies.
Provider preparation/generation runs off the UI thread; the event loop continues
processing progress/cancel until cleanup completes. Unsaved script/scene changes
block replacement, project close and downstream operations until saved or explicitly
discarded. Reopening restores retained revisions and selections.

Cache identity includes source lineage, language, script/narration/board revisions,
verified image bytes, caption method/text/audio, finish/logo settings and renderer
identity. Voice changes retime matching scene IDs without dropping visual edits.
Invalidation never deletes preceding artifacts. No partial render is selected.
Cached outputs verify the matching MP4, SRT and metadata, including lineage.

## Validation and acceptance evidence

An isolated read-only SQLite backup and verified copy of all 426 acceptance-project
artifacts (277,879,108 bytes) was made under `.tmp/d072-acceptance/project`. The
original project was never opened for write. The source is 1920x1080, 25 FPS,
12,236 frames, 489.44 seconds, mono AAC 48 kHz. Its checksum is
`a6c440eff6531aea46f500982234e17c2fe9298260ce6d884db6c0631323d5bc`.
The source payments/banking candidate was accepted in the copy. Exact passages
cover terminal authorization, cash alternatives and banking-app synchronization;
49 clean original images were verified. Source metadata language is Polish while
its spoken script is English, so the derived language is explicitly English.

The default per-user installed composition found **zero voice choices**. The
original development launcher's `.env` also points to an existing Chatterbox
runtime/model/cache. Its existing injected audio-factory path was checked separately,
without importing the GUI-launching script or revealing credentials. The initial
worker-revision check failed because Git's CRLF checkout changed 21 worker files'
line endings. Every differing file matched the original checkout and installed
worker after line-ending normalization. Restoring those original bytes locally
produced no source diff; full runtime/model verification then passed, with CUDA
`cuda:0` and the English builtin voice available. No runtime/model was replaced
and no integrity check was bypassed.

The configured LLM was invoked explicitly for acceptance script generation. Its
existing transport timed out after its configured retries; no proposal or script
was saved, and no additional LLM/image requests were made. Mechanical acceptance
continued through an explicit manual script revision and a complete, domain-validated
manual storyboard fixture persisted through the existing artifact storage, labeled
as such. No application fallback to a fabricated AI result was added. The 114-word manual draft was reviewed against the three selected source
passages; five existing source images were visually inspected and assigned by
relevance, with zero new paid image requests. This does not prove live AI editorial
script/storyboard generation. The offline structured-provider tests cover that
contract. Human fact, standalone meaning, audible voice and visual relevance review
remain required. D072 stays Partial for these acceptance gaps.

Real Chatterbox CUDA narration completed as a separate child artifact: **38.08
seconds**, target 45, deviation **-6.92 seconds**, with 12 verified sentence blocks.
Its opening hook occupies 2.4 seconds. The complete manually reviewed storyboard
covers **952 contiguous frames at 25 FPS**. All imagery is reused from five clean,
visually inspected source images; zero new images were generated.

The initial MP4 and a second MP4 after changing only the first scene's fill/focal
setting both passed stream, duration, packet timestamp, checksum and complete decode
checks: **1080x1920, progressive H.264/yuv420p, AAC 48 kHz mono, 38.08 seconds**.
The second render reused the same narration and images. Both videos remain retained.
The final artifact is `artifact_1fcda8ec66434b99b3b4731974458587`, 4,537,258 bytes,
SHA-256 `d93d7a73b5c3f6a0be837fd5a74345d77f5ef8acc3243ad2f48447e0e12c6afb`.

A complete parent/child SQLite close and reopen restored the current verified
selection; unchanged rendering returned the cached artifact, with providers disabled.
Qt playback reached EndOfMedia at **38,080 ms** without errors, in muted mode.
This proves playback completion, not a human listening verdict. The separate fading
headline and new sentence caption zones were visually inspected in the actual
vertical preview. New MP4/SRT/metadata acceptance copies were written exclusively
under `.tmp/d072-acceptance/`, without replacing existing files. Both original
databases and all 426 original artifact checksums/lengths remained unchanged after
real narration, both renders and the selective rebuild.

Offline tests exercise the real child SQLite, supervised sentence TTS assembly and
result publication using fake providers. The CPU FFmpeg smoke renders 36 seconds
of new PCM narration, six measured sentence scenes, source-image reuse, fill/fit,
gentle motion, safe headline/caption overlays, actual vertical preview and full
1080x1920 H.264/AAC validation. The broader offline cases also check unchanged caching, reopen identity,
selective crop rebuilding and unchanged original source. Synthetic speech is a
mechanical timing fixture, not evidence of editorial or audible quality.

- Focused script/audio/image/desktop/Original Excerpt regression run: **38 passed
  in 166.33 seconds**.
- Initial focused pipeline including the real FFmpeg media smoke: **28 passed
  in 201.49 seconds**.
- There are **35 new D072 test cases**. Full `python -m pytest backend/tests --tb=short
  --junitxml=.tmp/d072-full.xml`: **2,246 passed, 11 skipped in 1,080.15 seconds**.
  All skips are existing optional tests; no D072 test skipped.
- Desktop layout snapshots were visually inspected with explicit Segoe UI font
  loading for the offscreen Qt environment.
- Original project database checksums and all 426 artifact checksums/lengths
  still match the pre-task read-only snapshot evidence.
- `git diff --check`, staged whitespace/UTF-8/newline checks, 72 unique backlog IDs
  and current documentation-link checks pass.

## Limits

- Source-grounded generation and numeric/reference checks are not automated fact
  checking. Human review is essential; the model may still make qualitative errors.
- Verified sentence assembly is the current caption timing method. Word-level
  alignment and karaoke captions are not claimed. Narration without measured
  boundaries fails closed; no proportional word timing is invented.
- Initial source/image verification is synchronous and can be slow for large
  artifact collections; provider preparation/generation and encoding are asynchronous.
- Long sentences may exceed the two-line caption limit and need script revision;
  holds longer than ten seconds require an explicit justification.
- Automatic tempo fitting is disabled. Processed audio must already be a valid
  current child artifact and retain measured boundaries; raw fallback is forbidden.
- Model installation, reference-voice approval transfer from the parent, paid-cost
  estimation, uploads and automatic editorial guarantees are outside this task.

## Changed files

- Application: `director_audio.py`, `shorts_director.py`.
- Domain/storage: `domain/shorts_director.py`, `storage/shorts_director.py`.
- Provider boundary/adapter: `providers/shorts.py`, `providers/ffmpeg_director.py`.
- Desktop: `director_composition.py`, `director_panel.py`, `shorts_panel.py`, `editor.py`.
- Tests: `unit/test_director_script.py`, `integration/test_shorts_director.py`,
  `integration/test_director_panel.py`, `integration/test_director_media.py`.
- Documentation: this evidence note and `IMPLEMENTATION_PLAN.md`.

No original databases, outputs, models, reference audio or secrets are committed.
No merge or push is performed.

# Desktop-first implementation plan

## Authority and implementation policy

Status: accepted plan; all tasks below are initially **Planned**, not implemented.
Accepted on 2026-09-11 against repository baseline `7d19a70`.

[ADR 0003](../decisions/0003-desktop-first-architecture.md) records the architecture
decision. This file is the **single authoritative implementation backlog** for
desktop AI Content Studio. [ROADMAP](../ROADMAP.md) is high-level navigation, not
a second task queue. [Architecture pages](../architecture/overview.md) describe
implemented behavior; [archived material](../INDEX.md#historical-context) is evidence,
not active requirements. Current code discrepancies are recorded below.

Implement one explicitly selected D### task per implementation branch. Inspect
actual dependency code and tests before starting; an ID, milestone or status is
not proof of readiness. Keep existing public contracts unless the selected task
authorizes a bounded compatibility change. Record justified scope changes in this
plan before expanding implementation. Review and test each task independently.
Update its status here with evidence when completed; do not renumber IDs or create
parallel task definitions. Split future oversized tasks by adding IDs and links.

This is documentation for people and coding assistants. It requires no Spec Kit,
agent roles, task-loop tools, receipts, generated manifests or automatic execution.
The plan itself does not authorize implementing a task without its selection.

## Product goal

```text
Topic
  -> Structured Script
  -> Narrative Sections
  -> Section Audio
  -> Scene Segmentation
  -> Visual Prompts
  -> Images
  -> Timeline
  -> FFmpeg Render
  -> MP4
```

Users can edit each meaningful stage, retain earlier variants, listen to one
section, inspect one scene and regenerate a selected output. Render settings and
timeline edits are explicit inputs; editing an MP4 itself is not the source of
project state. Source language remains separate from publishing localization.

The target is a local Windows production tool with Timeline Lite: one narration
track and one visual layer initially. It is not a full CapCut replacement or a
multi-user SaaS. Mock text/images are permitted during development, but audio and
the release-gate MP4 must be real. Manual script entry and image import are
first-class ways to make useful content before real LLM/image integrations.

## Architecture target

Conceptual layers, from user-facing operations to implementation mechanisms:

```text
Presentation
    -> Application Services
    -> Domain
    -> Infrastructure

Application
|-- Project services
|-- Editing services
|-- Generation services
|-- Job coordinator
`-- Dependency / invalidation planner

Infrastructure
|-- SQLite
|-- Artifact storage
|-- Runtime manager
|-- Workers
|-- TTS providers
|-- Image providers
`-- FFmpeg renderer
```

The layer listing is **not an import dependency from Domain to Infrastructure**.
Domain has no Qt, HTTP, SQLite, Torch or FFmpeg dependencies. Application services
use injected ports; infrastructure implements those ports, and composition wires
them together. Presentation submits commands and displays results/events.

PySide6/Qt Widgets is the primary presentation layer, with Qt Multimedia for
playback. Keep services independent of Qt. FastAPI remains an optional adapter
for automation/integration and future web use; desktop startup needs no HTTP
server. Existing `backend/app/` names need not be moved for the pivot.

Use application operations for create/open project, edit/split/merge/reorder
sections, generate section audio, plan scenes, edit/generate prompts, import or
generate images, update timeline and export. Services own transactions and input
snapshots, not provider SDK logic. The existing workflow engine remains useful
for batch module execution; it does not become the project's revision database.

## Current code, reuse and discrepancies

| Evidence | Current behavior | Planned treatment |
| --- | --- | --- |
| `backend/app/domain/narrative_segment.py`, `script.py` | Run-owned segment and versioned script text, without editorial revision aggregate | D001 adds stable project/editorial identity and immutable revisions; preserve legacy construction through explicit mapping |
| `backend/app/domain/render_scene.py` | Scene-plan reference and textual timing hint | D013/D018 add explicit scene/timing/timeline relationships |
| `backend/app/modules/script_generation.py` | LLM text is recorded as a reference; segment text comes from deterministic templates | D014 consumes a validated structural result as actual section content |
| `backend/app/api/routes/workflow_runs.py` | Start/resume updates in-memory status, without executing the engine | Desktop services first; optional HTTP integration D035/D052 |
| `backend/app/storage/local_store.py`, `manifest.py` | Unique artifact ID in keys already prevents same-name overwrites; whole-byte I/O and separate sidecar writes | D003/D004/D040/D044 add indexing, streaming, publication and containment; do not claim store currently overwrites every revision |
| `backend/app/tts/preview.py` | Validated preview cache with per-identity single-flight locking | Reuse, preserving preview/production cache separation; global model/device scheduling is additional work |
| `backend/app/tts/chunk_synthesis.py` | Resumable chunks; stale work/final files can be removed inside its runtime directory | Isolate each generation workspace; never point it at immutable project history |
| `backend/app/tts/assembly.py` | PCM readability/frame completeness and cross-chunk format compatibility; atomic WAV publication | Reuse; adapter-specific mono/16-bit constraints are not universal assembly constraints |
| `backend/app/tts/post_processing.py` | FFmpeg tempo processing separate from native synthesis | D011 preserves original audio and records a derivative |
| `backend/app/modules/voiceover.py` | Equal-duration estimated word timings | D012 measured block boundaries, D031 optional forced alignment; never label estimates as alignment |
| `backend/app/modules/video_rendering.py` | A provider reference is saved under an MP4 artifact name | D019 persists and probes actual playable media |
| `backend/app/workflow/presets.py` | Short preset lacks the script predecessor required by scenes | D052 repairs legacy preset composition; desktop execution does not depend on that broken preset |
| `pyproject.toml` | FastAPI and pytest are base dependencies; no desktop package yet | D002 evaluates packaging; D041 separates desktop/API/dev dependency groups as release work |
| ADR 0002 and curated Piper catalog | English production default; curated Piper path currently supplies Polish voices | Preserve the default and localization boundary. MVP smoke explicitly selects supported Polish Piper input; do not silently claim English voice coverage. D029 extends verified production runtime coverage |

Reuse existing provider protocols/factories, TTS catalog/selection, preview,
effective synthesis identity, chunk manifests/resume, PCM validation, tempo and
approval history. Preserve deterministic offline tests. Heavy optional runtimes
must not load through catalog discovery or the default test installation.

The cleanup report records 497 passing tests at its baseline, not desktop or GPU
acceptance. New work needs task-specific evidence, full repository tests and
separate packaged Windows/media smoke evidence where specified.

## Project and pipeline model

These names describe target responsibilities, not a requirement to create one
class/file for each row immediately. Extend existing concepts rather than keeping
duplicate competing section or provider models.

| Model | Responsibility |
| --- | --- |
| Project | Stable project identity, source settings, active script/timeline selections and workspace ownership |
| ScriptRevision | Immutable ordered references to exact section revisions |
| NarrativeSegment | Stable identity of an editorial narrative section, independent of generation attempt |
| SectionRevision | Immutable text, role/title snapshot as needed, parent revision, author/source and fingerprint |
| SectionAudio | Exact section revision plus effective synthesis identity, raw/processed artifact references and measured audio parameters |
| SpeechBoundaryMap | Source-text spans mapped to audio samples, method and confidence/quality; estimates distinguished from measurements |
| RenderScene | Stable visual scene identity and semantic text range belonging to a section revision |
| SceneTiming | Range within one exact audio artifact, separate from scene semantics |
| VisualPromptRevision | Immutable prompt, context/style revisions and manual/generated provenance |
| ImageArtifact | Imported/generated image bytes, dimensions, provenance, actual model parameters and checksum |
| TimelineRevision | Immutable ordered clips referencing exact visuals, source audio ranges, offsets and output timebase |
| RenderArtifact | Actual media for one timeline snapshot and render profile, with probe/checksum evidence |
| GenerationJob | Durable requested operation, input snapshot, attempts, progress, outcome and publication eligibility |

A **narrative section** is an independently editable part of the story. A **visual
scene** selects a semantically coherent piece of narration and its visual treatment.
A **technical TTS chunk** is a synthesis/retry/cache unit. A section has many
possible scenes/chunks; a scene can cover several chunks. Do not force a 1:1 mapping.

Keep section identity when editing B1 into B2 and when reordering. A job/run ID is
provenance, not editorial identity. Split creates new section identities with
lineage; merge creates a new section with both sources retained. No automatic
reuse of old audio is claimed after text-changing split/merge.

Use integer sample positions and explicit sample rate for audio, and an explicit
rational frame timebase for video. UI seconds are derived. Half-open source ranges
must be valid, ordered when required and free from accidental gaps/duplication.
RenderScene semantics can remain unchanged when only its SceneTiming changes.

## Project workspace and persistence

```text
project/
  project.sqlite
  project.json          # optional format/identity descriptor, not duplicate state
  artifacts/
    audio/
    images/
    prompts/
    captions/
    renders/
  exports/
  work/jobs/
  cache/previews/
  cache/thumbnails/
  backups/

application-data/       # outside individual projects
  runtimes/
  models/
  downloads/
  logs/
```

SQLite owns metadata, revisions, active pointers, dependencies and durable jobs.
Large media stay in files behind ArtifactStore; stream/import them without whole
MP4 payloads in RAM. Relative opaque keys make projects movable. Friendly export
names such as `hook.wav` are not artifact identity. Models are shared outside
projects; secrets/reference paths must not leak into exported manifests.

Publication protocol: write private job output, validate/checksum, atomically move
within the artifact volume, then transact index and conditional active selection.
SQLite and filesystem do not share a transaction. Recovery handles orphan files
and interrupted jobs; no database pointer may advertise unvalidated output.
Workers cannot overwrite existing artifacts or directly change active selections.

MVP permits one writer per project on local storage. Reject competing edit sessions;
do not support active multi-machine or network-share WAL use. Backups/portable
exports capture a consistent database snapshot and referenced media, not just a
live SQLite main file. Version the schema from D003; later migrations are D042.

Undo means restoring a prior revision/selection, not rerunning AI. D001/D003 retain
history; D033 adds a history UI. Distinguish disposable cache/work from pinned or
referenced artifacts. Garbage collection in D047 respects all retained snapshots.

## Dependency and invalidation model

```text
SectionRevision -> raw SectionAudio -> processed audio -> SpeechBoundaryMap
                                       ^
                                     tempo

SectionRevision -> semantic ScenePlan -> VisualPromptRevision -> ImageArtifact
                         |                     ^
                         |               film context + style revisions
                         v
SectionAudio + SpeechBoundaryMap + ScenePlan -> SceneTiming

ImageArtifact + SceneTiming + Audio -> TimelineRevision -> RenderArtifact
```

Edges record actual inputs. They are product artifact dependencies, not a coding
agent graph. A scene plan contains RenderScene identities/text ranges; SceneTiming
binds these to one audio revision. Measured durations may suggest regrouping, but
changing voice/tempo must not silently replace an accepted semantic scene plan.

| Change | Required invalidation | Reusable outputs |
| --- | --- | --- |
| Text in section B | B audio and dependent timing/scene content/prompts; final timeline/render | A and C unless their recorded global context actually includes changed B |
| Voice/native synthesis settings | Selected section's raw audio and audio-derived timings | Text and visuals if scene text/context remains unchanged |
| Post-processing tempo | Processed audio, timing, timeline/render | Raw TTS and unchanged scene prompts/images |
| Scene prompt | Image compatibility with prompt and derived render | Audio, text and unrelated scenes |
| Selected image variant | Preview/render | Prompt, narration and other images |
| Section/clip order | Timeline/render | Existing media; context-based suggestions can be separately marked stale |
| Export resolution/FPS | Render and needed media derivatives | AI outputs |
| Global style/context | Only prompts/results that consumed that revision | Unrelated text/audio and manually retained variants |

Immutable input/output revisions retain their historical validity. `fresh` means
matching the selected inputs; `stale` means usable historical output for older
inputs; `missing` means no required result exists. `dirty` is an unsaved UI edit.
`failed` is a job attempt outcome, not a replacement for artifact freshness: a
failed regeneration can coexist with a selected stale but playable result.

Fingerprints identify canonical requests: source hashes, effective provider/model
revision, relevant settings/device, referenced content checksums, context/style
and algorithm version. Checksums identify actual output bytes. Identical requests
do not guarantee deterministic new AI samples. A forced new variant bypasses
reuse and receives a new generation identity; cache reuse validates stored bytes.

Manual prompts/images are explicitly owned choices. Never overwrite them during
automatic invalidation. A changed context can flag them for review. Pin/version
the film brief used for visual prompts; feeding the entire changing script into
every prompt necessarily broadens invalidation and must be visible.

Jobs capture expected section/scene/timeline revisions at enqueue time. Publish
with a transactional expected-version check. If B2 is active when the B1 job
finishes, retain its result under B1 but do not select it for B2. This requirement
is implemented early in D040, before real generation; D024 composes selective
regeneration end to end. No worker or UI may bypass the publication check.

## Local execution and runtime strategy

The Qt thread handles interaction. Short I/O and communications use background
threads; model inference and FFmpeg use controlled subprocesses. The lightweight
coordinator can live inside the application initially. Closing the application
stops/checkpoints work; background execution after closing the UI is not promised.

Use a SQLite job queue without Redis/Celery. Only the coordinator commits project
metadata. IPC uses versioned JSON messages over private pipes, request/job IDs,
framing limits and structured progress/errors. Send artifact references, not media
bytes. Reserve stdout for the protocol and stderr for logs; never use pickle or
shell-interpreted user input. Spawn workers hidden on Windows.

Pending jobs cancel immediately; active jobs cooperate at a safe boundary, then
terminate after a grace period if necessary. Pause/resume applies at job/chunk
boundaries, not arbitrary GPU instructions or FFmpeg frames. Restart marks abandoned
running attempts interrupted and verifies cached chunks before reuse. Output from
an interrupted attempt cannot appear completed. Progress reports actual phases,
chunk counts or FFmpeg time; do not invent precise percentages for model loading.

Runtime manager tracks installed profiles, process health, model/device identity,
load/unload, resource leases and failures. Initially one GPU operation across the
application, including preview; a single coordinator owns the lease. P0 uses a
verified Piper CPU profile and establishes process lifecycle. D028/D029 add the
GPU lease and verified Chatterbox profile before exposing GPU generation.

OOM ends that attempt, preserves prior output and releases/restarts the worker.
CPU fallback is explicit, capability-tested and recorded in effective identity;
never silently change provider or use a remote service. Separate incompatible
Torch/TTS/image environments. XTTS remains evaluation-only. Display RAM/VRAM/device
availability when supported; do not promise accurate per-model budgets without
measurement. Model unload may require ending its process, not just emptying cache.

## Scene segmentation strategy

Plan semantic boundaries from complete sentences before TTS; finalize timing from
measured audio afterward. Target 8-12 seconds is a preference, below sentence
integrity and meaning. Longer/shorter scenes are acceptable. Never cut text every
N characters or label equal-duration word estimates as measured synchronization.

For MVP, preserve sentence-aligned synthesis block boundaries and source spans.
Assemble a section WAV while retaining measured cumulative sample positions.
Scenes combine whole blocks; technical provider limits must not force visual
cuts mid-sentence. Validate coverage and account for explicit pauses. Compare
prosody with larger blocks; small blocks can reduce naturalness.

Tempo-derived timing must correspond to the processed waveform. Where necessary,
process/map measured blocks before assembly instead of assuming ideal floating
point division exactly matches FFmpeg output. Do not resynthesize merely to change
post-processing tempo. D031 later aligns known text to larger continuous audio;
STT can detect omissions but cannot silently rewrite the user's script.

## Rendering and desktop UX

TimelineRevision is the render source: selected images, exact audio ranges,
duration/offsets and a fixed output profile. Start with static frames, explicit
fit/fill, simple cuts and one tested MP4 profile (for example 1080p/30 FPS).
Normalize sample rate/format as derived media, preserving original TTS. Probe and
decode output before publishing; a file extension or provider URI is not evidence.

Use one final FFmpeg encode initially. Scene MP4 intermediates are not required.
Re-encoding the film after an image edit is acceptable; regenerating unrelated
AI outputs is not. Concat/cached segments need consistent streams/timebase and
belong to D037 after measurements. Crossfades, captions and motion are explicit
later features; they must not shorten narration accidentally.

Desktop layout: project/settings header, section list, central script/scene editor,
selection inspector, preview, Timeline Lite and job panel. Sections expose edit,
split/merge/reorder and audio generation; scenes expose text range, prompt,
image import/variant selection and independent prompt/image regeneration.
Audio controls reuse the catalog/selection/preview services. Timeline initially
has one narration track and one visual layer, with safe boundary adjustments.
Moving a narrative clip moves its audio range with the visual; replacing just the
image is a separate action. Preview uses the same snapshot as final rendering.

Actions distinguish regenerate text, audio, scene plan, prompt and image. Display
stale output as older but still playable. A failed new attempt retains the old
variant. Users can save, reopen and explicitly rebuild missing/stale dependencies.

## Packaging and distribution

Deliver one Windows installer, not one giant self-extracting file containing every
model. D002 evaluates PySide6 deployment (starting with pyside6-deploy standalone),
WAV/MP4 playback and subprocess IPC on a machine without system Python. The spike
is not the editor or production worker implementation. A failed spike records
evidence and triggers an explicit decision review before dependent UI work.

Bundle application Python/Qt, required media plugins and pinned ffmpeg/ffprobe.
Manage optional AI profiles separately; download weights/voices on demand outside
projects. Do not copy developer venvs or depend on user PATH. Use a private
interpreter and pinned packages verified for the target device, without arbitrary
pip upgrades. Detect drivers; do not silently install/replace system GPU drivers.

Each profile/model records version, source, integrity hash, license/provenance,
supported language/device and health check. Downloads resume to temporary files;
activation follows integrity and free-space checks. Avoid an administrator-only
cache design; account for Windows symlink limitations and duplicate cache space.
Raw private reference audio and credentials stay out of exported configuration.

Qt playback libraries and FFmpeg CLI packaging are separately verified. Record
the exact redistributed build, notices/source obligations and codec/model license
constraints before distribution. Sign installer/application binaries for release;
signing does not guarantee SmartScreen reputation. Test Unicode/spaces, restricted
write permissions, locked files and clean Windows without dev tools.

App, runtime and model versions are independent. P0 can update by a controlled
installer without deleting projects; automatic updates and runtime rollback are
D034/D043. Never mutate a runtime leased by a running job. Database upgrades use
version checks, backup and fail-safe migration (D042); old apps refuse unsupported
schemas instead of writing them. Application size, download volume, RAM/VRAM and
render time are measured release evidence, not assumed budgets.

## Risks and release evidence

| Risk | Required treatment / task evidence |
| --- | --- |
| UI hangs or frozen executable cannot spawn | D002/D007/D021: packaged IPC, nonblocking actions, process failure handling |
| Runtime/DLL/CUDA mismatch or OOM | D045/D008/D028/D029: pinned profile, health check, exclusive GPU lease, recoverable failure |
| Long/unsafe Windows paths and media locks | D044/D041: resolved containment, Unicode/spaces, no command injection, controlled publication |
| Partial files, stale results, crash | D004/D006/D040/D024: verified immutable output, recovery, conditional active selection |
| Timing drift/prosody | D012/D013/D019: measured coverage, sample/timebase checks and explicit listening smoke |
| Download corruption, low disk, oversized installer | D009/D041/D025: resume/hash/free-space checks and measured package sizes |
| Unsupported playback/codecs | D002/D019/D041: pinned binaries/plugins and decoding on clean Windows |
| Storage growth and concurrent/synchronized copies | D003/D042/D047: one writer, consistent backups, schema versions, reference-aware GC |
| Inconsistent generated characters | Keep style/context and manual choice; model-level character consistency is outside MVP |
| Supply/license/signing constraints | D045/D009/D041: provenance, redistributable pinned components, notices and release signing evidence |

## MVP definition

MVP is reached only when a user can:

1. Launch the installed application on Windows without installing Python.
2. Create a project.
3. Enter a topic or their own text.
4. Obtain structured narrative sections (mock generation is clearly labeled).
5. Edit sections, including split, merge and order.
6. Generate real local audio for sections with a supported installed profile.
7. Obtain scenes based on text and measured audio boundaries.
8. Edit independently persisted visual prompts.
9. Import images or generate them through the available image contract.
10. Inspect Timeline Lite and scene/film preview.
11. Generate a real, decodable MP4 with narration and visuals.
12. Close the application.
13. Reopen the project with its media and selections intact.
14. Edit only one section.
15. Recompute only affected dependencies, retaining all other generated media.
16. Export a new version without regenerating the entire project.

D025 is the release acceptance task and transitively requires every P0 task.
Topic-to-mock output proves plumbing, not production-quality content. A useful
MVP permits manual script and image import with real Piper CPU speech/rendering;
D026/D027 add real AI automation. The acceptance profile explicitly selects its
supported language; it does not change existing production defaults by implication.

Out of MVP: GPU-required deployment, XTTS production, forced-aligned word captions,
multitrack editing, arbitrary keyframes, generated video clips, consistent character
models, live collaboration/sync, automatic publishing and background service mode.
Static images/simple cuts suffice. Optional features must not require providers
when disabled. A final full encode may repeat while AI artifacts are reused.

## Milestones

Milestones group deliverables, not rigid sequential gates. Cross-milestone task
dependencies below determine readiness. In particular D002 is the second early
task, and D040 precedes generation even though it belongs to M8.

| Milestone | Goal / exit evidence | Tasks |
| --- | --- | --- |
| M1 — Editable project foundation | Stable editable identities, reopenable project and safe immutable artifacts | D001, D003, D004, D005, D038, D039, D044 |
| M2 — Local execution foundation | Recoverable jobs and a verified managed CPU worker | D006, D007, D008, D045 |
| M3 — Audio pipeline | Downloaded Piper voice, section WAV, tempo and measured boundaries | D009, D010, D011, D012 |
| M4 — Script and scene pipeline | Structured sections, semantic scene plan and editable prompts | D013, D014, D015 |
| M5 — Visual pipeline | Imported/generated image artifacts and explicit selection | D016, D017 |
| M6 — Timeline and rendering | Versioned timeline, real MP4 and coherent proxy preview | D018, D019, D046 |
| M7 — Desktop editor | Project/section/audio/scene panels and Timeline Lite | D020, D021, D022, D023 |
| M8 — Regeneration and recovery | Conditional publication and end-to-end selective regeneration | D024, D040 |
| M9 — Installable MVP | Early packaging proof, distributable installer and all 16 acceptance steps | D002, D025, D041 |
| M10 — Production AI integrations | Real providers, managed GPU, references, alignment and captions | D026, D027, D028, D029, D030, D031, D032, D057 |
| M11 — Product durability and optimization | History, safe updates, schema migration and measured rendering improvements | D033, D034, D036, D037, D042, D043, D047 |
| M12 — Optional integrations and output expansion | API, grounded sources, approvals, publishing and additional media | D035, D048, D049, D050, D051, D052, D053, D054, D055, D056 |

Recommended P0 implementation order (each step still requires code evidence):

```text
D001 D002 D003 D004 D005 D006 D040 D045 D007 D008 D009
D010 D011 D012 D013 D014 D015 D044 D016 D017 D018 D019
D038 D039 D020 D021 D022 D023 D046 D024 D041 D025
```

Priorities: P0 = installable MVP dependency; P1 = production quality/automation;
P2 = durability, optional integration and optimization; P3 = output expansion.
No higher-numbered task is implicitly done because a lower-numbered task needs it.

## Task backlog

All entries start Planned. Code areas are bounded planning targets, not claims
that proposed paths exist. Paths are relative to the repository; `app/` below
means `backend/app/`, and tests live under `backend/tests/` unless stated otherwise.
Every task uses the validation policy at the end in addition to its focused tests.

### D001 — Stable narrative sections and immutable revisions

- **Status:** Completed — PASS (2026-09-12)
- **Milestone:** M1
- **Priority:** P0
- **Goal:** Make editorial identity independent of a generation run.
- **Scope:** Stable section IDs, immutable SectionRevision and ScriptRevision values, explicit active revision selection and pure reorder semantics; preserve existing NarrativeSegment/Script entrypoints through an explicit compatibility boundary.
- **Out of scope:** Qt, HTTP, SQLite, TTS, providers, filesystem persistence and a second competing section model.
- **Dependencies:** None.
- **Main code areas:** app/domain/narrative_segment.py, app/domain/script.py, narrowly scoped revision models; backend/tests/unit/.
- **Acceptance criteria:** For A-B-C, editing B1 to B2 preserves A, B and C section IDs; B1 remains accessible; B2 is selected in the new snapshot; the old snapshot is unchanged; reorder preserves identity.
- **Test strategy:** Pure domain tests for edit/reorder, immutable previous snapshots, invalid references and legacy construction. No infrastructure imports or connections.
- **Evidence:** `SectionRevision` and `ScriptRevision` in `backend/app/domain/narrative_segment.py` and `backend/app/domain/script.py`; behavioral coverage in `backend/tests/unit/test_editorial_revisions.py`. A/B/C identities, retained B1, selected B2, pure reorder, frozen snapshots, invalid/foreign references and explicit legacy import pass. Existing `NarrativeSegment`/`Script` classes remain unchanged; isolated domain-import test blocks infrastructure dependencies.
- **Validation:** `python -m pytest backend/tests/unit/test_editorial_revisions.py backend/tests/unit/test_t006.py backend/tests/integration/test_long_form_workflow.py` — 64 passed; `python -m pytest backend/tests` — 549 passed; `git diff --check` — PASS. Snapshots are in memory only; durable history remains D003. No D002 implementation.

### D002 — Desktop packaging spike

- **Status:** Partial — clean Windows and manual playback acceptance pending (2026-09-12).
- **Evidence:** [D002 packaging spike](D002_PACKAGING_SPIKE.md): source and relocated standalone WAV/MP4/child smoke PASS; focused 14 tests and full backend 563 tests PASS; diff check PASS. Clean-machine launch and human picture/sound observation remain unverified; dependent UI work remains gated.
- **Milestone:** M9
- **Priority:** P0
- **Goal:** Validate PySide6 playback and process packaging before editor investment.
- **Scope:** Minimal Qt window, fixture WAV/MP4 playback, simple subprocess request/reply, standalone packaging recipe and recorded Windows evidence.
- **Out of scope:** Real editor, production job protocol, models, provider downloads and full installer/updater.
- **Dependencies:** None. The recommended execution sequence places this spike second.
- **Main code areas:** New app/desktop/ spike entrypoint, packaging configuration, redistributable test media and docs/desktop/ spike evidence.
- **Acceptance criteria:** Packaged app starts on clean Windows with no system Python, plays both fixtures and exchanges a message with its child. Record OS/build/tool versions and failures; failure blocks dependent UI work, not silently changes the ADR.
- **Test strategy:** Developer and packaged smoke; clean-machine manual playback observation and automated worker handshake/exit assertions. Generate or include only redistributable synthetic fixtures.

### D003 — Durable editable project

- **Status:** Completed — PASS (2026-09-12).
- **Milestone:** M1
- **Priority:** P0
- **Goal:** Reopen a project without losing identities and active revisions.
- **Scope:** Small SQLite repository for project/section/script state, schema version 1, atomic selection transactions, relative workspace references and a single-writer guard.
- **Out of scope:** Persisting every legacy API dictionary, cloud sync, migration framework and full undo UI.
- **Dependencies:** D001.
- **Main code areas:** New app/storage/ project repository, app/application/projects.py; backend/tests/unit/ and integration/.
- **Acceptance criteria:** Create/edit/close/reopen preserves all revisions, ordering and selected B2; failed transaction preserves B1 selection; competing writer and unsupported schema are rejected.
- **Test strategy:** Temporary SQLite tests for round trips, rollback, writer contention and unknown schema; no Qt or network.
- **Evidence:** `app/storage/project_repository.py` persists schema v1, project metadata, stable section/script identities, immutable histories, ordered selections and relative workspace references. `app/application/projects.py` uses injected repository ports. A/B/C edit to B2, reopen and workspace relocation preserve history and selection; injected transaction failure and abrupt pre-commit process exit retain B1; a post-commit exit retains B2. Competing sessions/processes and unsupported formats are rejected; unknown-format bytes remain unchanged. See [implemented storage behavior](../architecture/api-and-storage.md#editable-project-persistence-d003).
- **Validation:** `python -m pytest backend/tests/unit/test_project_repository.py backend/tests/integration/test_durable_project.py backend/tests/unit/test_editorial_revisions.py backend/tests/unit/test_t046_project_config_models.py` — 83 passed (24 D003 cases plus 59 regressions); `python -m pytest backend/tests` — 587 passed; `git diff --check` and new-file whitespace checks — PASS. Validated on Windows with Python 3.11; local single-session storage only.

### D004 — Streaming immutable artifact publication

- **Status:** Completed — PASS
- **Milestone:** M1
- **Priority:** P0
- **Goal:** Store real large outputs without whole-file memory copies or partial active records.
- **Scope:** Extend ArtifactStore with controlled file/stream import, incremental hash, unique immutable destination and staged publication/index transaction; recover orphaned incomplete publications.
- **Out of scope:** Global deduplication, garbage collection and revision-aware job selection (D040).
- **Dependencies:** D003.
- **Main code areas:** app/storage/artifact_store.py, local_store.py, manifest.py and artifact index; storage tests.
- **Acceptance criteria:** Repeated friendly names retain distinct artifacts; recorded checksum matches bytes; injected failure cannot advertise partial media; large-file path uses bounded reads.
- **Test strategy:** Temporary files with guarded stream reads and failure injection before/after file move and metadata commit; retain existing store tests.
- **Evidence:** [D004 publication protocol and self-review](D004_ARTIFACT_PUBLICATION.md): optional file/stream contract, 1 MiB bounded reads, incremental SHA-256, staged no-replacement publication and project-bound SQLite artifact index. D003 schema/data stay unchanged. Repeated names, collisions, transfer/commit failure, actual process crashes, deterministic journal recovery and legacy compatibility pass. Orphan finals remain unadvertised and are reported without automatic adoption/deletion.
- **Validation:** Focused storage/project/workflow tests — 66 passed, including 25 new D004 cases; `python -m pytest backend/tests` — 612 passed; `git diff --check` and new-file whitespace checks — PASS (2026-09-12, Windows/Python 3.11). No D005 or later implementation.

### D005 — Artifact dependencies and freshness

- **Status:** Completed — PASS
- **Milestone:** M1
- **Priority:** P0
- **Goal:** Describe precisely which selected outputs no longer match their inputs.
- **Scope:** Canonical request fingerprints, explicit input edges, fresh/stale/missing derivation, manual provenance and separate failed-attempt semantics.
- **Out of scope:** Job execution, UI, deleting old variants or a generic workflow framework.
- **Dependencies:** D001, D003, D004.
- **Main code areas:** New app/domain/ dependency values, app/application/invalidation.py and index mappings; unit tests.
- **Acceptance criteria:** Changing B affects only actual dependents; prompt edit preserves audio; tempo preserves raw TTS; recorded global-context edges invalidate when their context changes.
- **Test strategy:** Table-driven behavior tests for the invalidation matrix, manual variants, relevant settings and unchanged content; no provider calls.
- **Evidence:** [D005 dependency semantics and self-review](D005_DEPENDENCIES.md): immutable canonical requests and explicit consumed-input edges in existing D004 manifest metadata, read-only project index mapping, pure fresh/stale/missing derivation, manual review flags and independent failed-attempt evidence. A/B/C invalidation matrix, effective generation identity, unchanged narration across revisions, project reopen and invalid metadata checks pass. No database migration or automatic selection changes.
- **Validation:** Focused dependency/revision/storage tests — 135 passed, including 34 D005 cases; `python -m pytest backend/tests` — 646 passed; `git diff --check` and new-file whitespace checks — PASS (2026-09-12, Windows/Python 3.11). No D006 or later implementation.

### D006 — Durable local job queue

- **Status:** Completed — PASS
- **Milestone:** M2
- **Priority:** P0
- **Goal:** Retain queued work and truthful attempts across application restart.
- **Scope:** Persist input snapshots, queued/running/completed/failed/canceled/interrupted attempts, claim ownership, progress and restart recovery; pause prevents new claims.
- **Out of scope:** Redis/Celery, workers, GPU scheduling and arbitrary mid-inference resume.
- **Dependencies:** D003, D005.
- **Main code areas:** app/domain/generation_job.py, new app/jobs/ coordinator/repository; integration tests.
- **Acceptance criteria:** Restart recovers pending work and marks abandoned active attempts interrupted; two claims cannot execute the same attempt; failed attempts retain prior artifacts.
- **Test strategy:** SQLite claim contention, state-transition, pause/cancel and simulated restart tests with a fake clock/executor.
- **Evidence:** [D006 queue semantics and self-review](D006_DURABLE_JOBS.md): project-owned versioned SQLite queue, frozen D005 request/input snapshots, separately retained attempts, transactional claims with tokens, reported phase/count progress, persistent pause, cooperative cancellation and explicit retries. New D003 sessions atomically interrupt abandoned running attempts; same-session adapter reconstruction preserves active claims. Failed attempts retain D004 artifacts and map to independent D005 failure evidence. D003 schema and legacy GenerationJob behavior remain unchanged.
- **Validation:** Focused queue/domain/project/dependency/storage tests — 103 passed, including 26 D006 cases; four actual subprocess crash boundaries and SQLite write contention pass; `python -m pytest backend/tests` — 672 passed; `git diff --check` and new-file whitespace checks — PASS (2026-09-12, Windows/Python 3.11). No D007 or later implementation.

### D007 — Versioned worker protocol and lifecycle

- **Status:** Completed — PASS
- **Milestone:** M2
- **Priority:** P0
- **Goal:** Run isolated work without blocking the application or trusting unframed output.
- **Scope:** Private-pipe JSON commands/events with job IDs and version handshake, bounded messages, stderr logs, progress, cooperative cancel, timeout/termination and hidden Windows launch.
- **Out of scope:** Real models, HTTP server, arbitrary command execution and UI redesign.
- **Dependencies:** D002, D006.
- **Main code areas:** New app/runtime/protocol.py and worker supervision; process integration fixtures.
- **Acceptance criteria:** Fake worker success/failure/hang/cancel yields truthful queue state; malformed/version-mismatched replies fail safely; child cleanup releases the process.
- **Test strategy:** Real lightweight subprocess tests for handshake, malformed output, timeout and cancellation; repeat packaged handshake from D002.
- **Evidence:** [D007 protocol, lifecycle and self-review](D007_WORKER_LIFECYCLE.md): private length-prefixed JSON with strict version/job/attempt binding and 256 KiB limit, async D006 supervision, bounded stderr, cooperative/forced cancellation, exit verification and joined pipe cleanup. Hidden Windows launch and kill-on-close process containment pass descendant/parent-crash tests. Relocated standalone diagnostic worker completes through D006; repeated D002 packaged ping exits zero. D002 clean-machine/manual playback evidence remains outstanding and its status is unchanged.
- **Validation:** Focused protocol/lifecycle/queue/spike tests — 91 passed, including 51 D007 cases, with resource/unraisable warnings treated as errors; `python -m pytest backend/tests` — 723 passed; standalone build and relocated packaged handshakes — PASS; `git diff --check` and new-file whitespace checks — PASS (2026-09-12, Windows/Python 3.11). No D008 or later implementation.

### D008 — Managed Piper CPU runtime provisioning

- **Status:** Partial — implementation and automated provisioning PASS; clean-Windows acceptance pending (2026-09-13).
- **Milestone:** M2
- **Priority:** P0
- **Goal:** Make the first local TTS runtime independent of system Python.
- **Scope:** Install one pinned private Python/Piper CPU profile from the approved manifest, isolate environment paths, run health check and activate only a complete environment.
- **Out of scope:** CUDA, weights/voice downloads, arbitrary pip latest and general runtime updater.
- **Dependencies:** D007, D045.
- **Main code areas:** New app/runtime/ provisioning and Piper profile composition; packaging fixtures.
- **Acceptance criteria:** Provisioned profile can run its worker without system Python/Piper; failed install remains inactive; restart recognizes the same installed profile.
- **Test strategy:** Offline fixture package source and interrupted-install tests; explicit clean-Windows provisioning smoke with approved artifacts.
- **Evidence:** [D008 private runtime, activation and smoke](D008_MANAGED_PIPER_RUNTIME.md): approved offline artifacts are hash/size verified, embedded Python and pinned wheels are isolated with `._pth`, and pinned native DLLs are extracted app-locally without a system installer. A bounded private worker probe gates atomic activation; restart verifies profile, worker revision and immutable file inventory. Failed/interrupted installs remain inactive, including real process death. Relocated standalone provisioning/restart/worker smoke and D006/D007 queue composition PASS on the developer host. Windows Sandbox is unavailable and Hyper-V access is denied; the explicit clean-Windows gate remains unverified.
- **Validation:** Focused tests — 167 passed, including 42 D008 cases; `python -m pytest backend/tests` — 813 passed; final standalone build and relocated provisioning smoke — PASS; `git diff --check` and new-file whitespace checks — PASS (2026-09-13, Windows/Python 3.11). No D009 or later implementation.

### D009 — On-demand Piper voice download

- **Status:** Completed — PASS
- **Milestone:** M3
- **Priority:** P0
- **Goal:** Install a supported voice with integrity and recoverable download behavior.
- **Scope:** Reuse curated catalog provenance; download model and required config to temporary files, resume, hash-check, check free space and atomically activate a version.
- **Out of scope:** Arbitrary model repositories, changing source-language defaults and model updates.
- **Dependencies:** D008.
- **Main code areas:** app/providers/piper_catalog.py, new app/runtime/ downloader and model index; tests.
- **Acceptance criteria:** Interrupted download resumes; invalid hash, insufficient space or missing companion config prevents activation; selected language matches the installed voice.
- **Test strategy:** Fake HTTP/range transport and disk-space tests; explicit real download smoke outside Git and default CI.
- **Evidence:** [D009 voice download and real smoke](D009_PIPER_VOICE_DOWNLOAD.md), merged in PR #67 (`7c1a685`): curated model/config/card downloads resume from persisted bytes, validate catalog hashes, free space and voice language, and atomically activate a verified immutable version. Real Gosia download resumed after 1 MiB using HTTP 206; all three files passed integrity checks and restart recognized the same version. The existing provider language `pl` resolves to catalog locale `pl_PL`. D002/D008 clean-Windows gates remain unchanged.
- **Validation:** Focused tests — 122 passed, including 62 D009 cases; `python -m pytest backend/tests` — 875 passed; real HTTP resume/download smoke — PASS; `git diff --check`, new-file whitespace and documentation checks — PASS (2026-09-13, Windows/Python 3.11). Status/evidence synchronized after verifying merged code; no D009 functionality reimplemented.

### D010 — Generate audio for one section

- **Status:** Completed — PASS
- **Milestone:** M3
- **Priority:** P0
- **Goal:** Produce a real WAV for exactly one immutable section revision.
- **Scope:** Application service and worker adapter reusing TTS factory/selection, effective identity and ResumableChunkSynthesizer; isolate generation workspaces and publish SectionAudio through the common gate.
- **Out of scope:** New TTS provider, whole-project generation, preview cache reuse as production and changing other sections.
- **Dependencies:** D004, D009, D040.
- **Main code areas:** New app/application/section_audio.py, app/runtime/ TTS dispatch; app/tts/ integration and tests.
- **Acceptance criteria:** A section gets validated audio with truthful duration; interrupted work reuses valid chunks; old revision media survives retry; a stale result cannot become active.
- **Test strategy:** Fake-provider resume/checksum/identity tests plus an explicit managed Piper smoke; no real models in default suite.
- **Implementation boundary:** Reuse D007 completion hooks and D040 publication, adding optional non-reserved artifact metadata for validated SectionAudio measurements. Store resumable generation files in configured per-job workspaces, separate from immutable published artifacts. Extend the D008 private worker source bundle with the existing TTS factory/selection/chunk synthesis dependency closure and a pinned Piper backend bridge; no new provider or runtime dependencies. Runtime/model identities are verified before work and publication. Application services use injected voice/output ports; workers never open project or queue databases.
- **Evidence:** [D010 section audio and managed smoke](D010_SECTION_AUDIO.md): one immutable section produces measured mono PCM WAV; validated chunks survive cancellation/process death and are reused on retry, while old revision audio and unrelated sections remain intact. Coordinator revalidation and D040 prevent corrupt or stale media from becoming selected. Final managed Gosia smoke produced 328704 frames at 22050 Hz (14.907210884 s), reused 2 chunks and generated 6 after cancellation; worker exit zero and project reopen — PASS.
- **Validation:** Focused tests — 145 passed, including 27 new D010 cases; `python -m pytest backend/tests` — 941 passed; final real managed Piper smoke, `git diff --check`, new-file whitespace, documentation links and task-scope checks — PASS (2026-09-13, Windows/isolated Python 3.11.9). Real media/runtime/model files remain ignored. D002/D008 remain Partial; standalone installer/clean-Windows acceptance was not reclassified. D011 was not started; branch remains unmerged for review.

### D011 — Tempo as an audio derivative

- **Status:** Completed — PASS
- **Milestone:** M3
- **Priority:** P0
- **Goal:** Change playback tempo without repeating native synthesis.
- **Scope:** Reuse post_processing to persist a processed audio variant keyed by raw checksum, tempo and processor version; expose original versus processed selection and measured parameters.
- **Out of scope:** Provider-native speaking-rate redesign, overwriting raw audio and alignment.
- **Dependencies:** D010.
- **Main code areas:** app/tts/post_processing.py, section audio service and derivative metadata; unit/integration tests.
- **Acceptance criteria:** Changing tempo creates a validated derivative and leaves raw bytes and other sections intact; no TTS call occurs; bad FFmpeg output stays unpublished.
- **Test strategy:** Provider call-count assertions, existing fake-process validation cases and a small real FFmpeg PCM fixture smoke.
- **Implementation boundary:** Add a provider-free tempo application service and an artifact adapter over D010 SectionAudio, D005 consumed-artifact edges and D040 jobs/publication. Keep separate persisted raw/processed output selections and expose an explicit original/processed read choice; no playback-preference schema or cache scheduler. Record a deterministic derivative key from raw checksum, normalized tempo and processor contract version in the request and immutable metadata. Reuse the existing FFmpeg processor in configured temporary storage with bounded, hidden child execution; do not change the raw synthesis pipeline or add another worker runtime.
- **Evidence:** [D011 derivative behavior and real FFmpeg smoke](D011_TEMPO_DERIVATIVE.md): changing tempo publishes measured immutable derivatives while raw audio, previous variants and unrelated sections remain intact; original/processed choices survive reopen. Actual D010 provider call-count tests show no repeated TTS. Invalid FFmpeg output, failure, timeout and cancellation stay unpublished; D040 preserves prior selections or retains stale results historically. Real FFmpeg 8.1.2 transformed a 4-second PCM fixture into 4.990929705 s at 0.8x and 3.204081633 s at 1.25x, with unchanged A/B/C raw checksums and successful reopen.
- **Validation:** Focused tests — 110 passed, including 30 new D011 cases; `python -m pytest backend/tests` — 971 passed; real FFmpeg PCM smoke, `git diff --check`, new-file whitespace, documentation links and task-scope checks — PASS (2026-09-13, Windows/isolated Python 3.11.9). Only D011 status changed; D002/D008 remain Partial. D012 was not started; branch remains unmerged for review.

### D012 — Measured speech boundary map

- **Status:** Partial — automated acceptance PASS; manual prosody comparison pending
- **Milestone:** M3
- **Priority:** P0
- **Goal:** Map complete textual blocks to actual source audio positions.
- **Scope:** Retain sentence/source spans through synthesis and assembly, measured cumulative sample boundaries and processed-tempo mapping; record method/quality and full coverage.
- **Out of scope:** Word-level forced alignment, equal word-duration claims and changing scenes solely to meet chunk limits.
- **Dependencies:** D010, D011.
- **Implementation boundary:** Desktop synthesis request version 2 retains sentence identity and original section character spans, never packs different sentences into one technical chunk, and measures cumulative PCM frames including silence. Version 1 jobs and legacy chunking remain supported without invented sentence timing. Persist the map with existing audio metadata, validate it at publication and carry it to tempo derivatives by the measured input/output frame ratio (explicitly approximate internal positions, exact full coverage). No alignment, scene changes or database migration. Manual listening remains separate evidence from automated timing checks.
- **Main code areas:** app/tts/chunking.py, chunk_synthesis.py, manifest.py, assembly mapping and new speech boundary values; tests.
- **Acceptance criteria:** Map covers the full selected WAV and section text without gaps/overlap; changing tempo maps to processed audio; sentence identity survives technical subchunks.
- **Test strategy:** Synthetic variable-length chunk and silence fixtures, incompatible parameters, punctuation/long-sentence cases and processed-duration checks; manual prosody comparison.
- **Evidence:** [D012 speech boundary contracts and real-media evidence](D012_SPEECH_BOUNDARIES.md): desktop synthesis v2 retains original character spans and sentence identity through technical subchunks/retry, then publishes independently validated cumulative PCM boundaries with the WAV. Original/processed maps survive reopen; tempo uses measured frame ratios with explicit approximate internal positions. Legacy v1 jobs and unmapped historical media remain supported. Real managed Piper/FFmpeg smoke covers 282 characters, four sentences and five chunks: raw 391936 frames at 22050 Hz (17.774875283 s), 0.8x 489861 frames, 1.25x 313599 frames; raw/history unchanged. Listening fixtures and comparison instructions are ready; manual prosody result has not been supplied.
- **Validation:** Focused tests — 114 passed, including 36 new D012 cases; `python -m pytest backend/tests` — 1007 passed in 69.77 s; real managed-worker/FFmpeg smoke — PASS (2026-09-14, Windows/isolated Python 3.11.9). Automated acceptance criteria pass; final diff/documentation checks recorded in the evidence document. Only D012 task status changed; D002/D008 remain Partial, D013 not started, branch unmerged.

### D013 — Semantic scene planning

- **Status:** Planned
- **Milestone:** M4
- **Priority:** P0
- **Goal:** Suggest visual scenes using meaning and measured speech boundaries.
- **Scope:** Project-owned RenderScene values, source ranges, separate SceneTiming and grouping of whole sentence blocks toward 8-12 seconds; retain explicit accepted plan identity.
- **Out of scope:** Image generation, forced 10-second cuts and silent replanning after voice changes.
- **Dependencies:** D001, D012.
- **Main code areas:** app/domain/render_scene.py, new segmentation application service; app/modules/scene_planning.py compatibility; tests.
- **Acceptance criteria:** All narration is covered once; long/short complete sentences remain valid; voice/tempo retimes an accepted plan without replacing its visuals.
- **Test strategy:** Variable-duration boundary fixtures, very long sentences, semantic grouping and accepted-plan retiming tests.

### D014 — Structured script generation

- **Status:** Planned
- **Milestone:** M4
- **Priority:** P0
- **Goal:** Use provider output as the actual editable script.
- **Scope:** Consume LLMProvider.generate_structured through strict schema validation, map ordered roles/text into revisions, support user-text section input and deterministic mock output.
- **Out of scope:** Real provider API, fact retrieval and rewriting unrelated script/research modules.
- **Dependencies:** D001, D003.
- **Main code areas:** app/modules/script_generation.py, app/providers/mock_llm.py, script application service; tests.
- **Acceptance criteria:** Generated sections match the structured response rather than fixed templates; repeated roles and optional CTA work; invalid structure cannot replace an active script.
- **Test strategy:** Offline provider fixtures for valid/invalid output, manual text and unchanged previous script; preserve legacy module compatibility tests.

### D015 — Independent visual prompt revisions

- **Status:** Planned
- **Milestone:** M4
- **Priority:** P0
- **Goal:** Make scene prompts editable and regenerable independently from images.
- **Scope:** Prompt artifact/service using scene text, section context, pinned film brief and style; manual edit, generated revision and explicit active selection.
- **Out of scope:** Image generation, automatic manual-edit overwrite and feeding unversioned global context.
- **Dependencies:** D005, D013, D014.
- **Main code areas:** New visual prompt values and application service; existing LLM boundary, artifact store; tests.
- **Acceptance criteria:** Each scene has a separate versioned prompt; manual edit retains old prompt/audio; context changes affect exactly recorded dependencies.
- **Test strategy:** Mock structured-output tests and invalidation/manual-ownership scenarios; no model/network connection.

### D016 — Controlled image import

- **Status:** Planned
- **Milestone:** M5
- **Priority:** P0
- **Goal:** Use real user-supplied visuals before a generative provider exists.
- **Scope:** Import PNG/JPEG, validate decoded dimensions/format, retain source/provenance, create immutable image artifact and select it for a scene.
- **Out of scope:** Video import, remote search, image generation and public absolute paths.
- **Dependencies:** D004, D013, D044.
- **Main code areas:** New app/application/ image intake, image metadata, ArtifactStore; image fixture tests.
- **Acceptance criteria:** Image remains available after moving/reopening the project; invalid/oversized decode is rejected; prior selected image remains intact on failure.
- **Test strategy:** Synthetic image imports, corrupt input, limits, path containment and project relocation tests.

### D017 — Image generation contract and mock

- **Status:** Planned
- **Milestone:** M5
- **Priority:** P0
- **Goal:** Support image generation and variants without binding core to one model.
- **Scope:** Small ImageGenerationProvider request/result contract, explicit capabilities, prompt/settings fingerprint, deterministic valid-image mock and variant selection.
- **Out of scope:** Real model/API, img2img, character consistency and a universal media-provider union.
- **Dependencies:** D015, D016, D040.
- **Main code areas:** app/providers/interfaces.py, new mock image adapter and image application service; tests.
- **Acceptance criteria:** Mock returns decodable image bytes; forced regeneration creates a new generation record; selecting an old variant changes no audio.
- **Test strategy:** Offline contract, format-validation, cache-versus-new-variant and stale-publication tests.

### D018 — Immutable timeline description

- **Status:** Planned
- **Milestone:** M6
- **Priority:** P0
- **Goal:** Compile selected media into a deterministic render snapshot.
- **Scope:** TimelineRevision/clips with image IDs, exact audio source ranges, durations, output timebase, fit/fill policy and validated ordering/offsets.
- **Out of scope:** GUI, FFmpeg execution, multiple tracks and keyframes.
- **Dependencies:** D011, D013, D016.
- **Main code areas:** New app/domain/ timeline values and application compiler; unit tests.
- **Acceptance criteria:** Every clip resolves exact selected inputs; invalid/out-of-range spans fail; reorder changes timeline identity without regenerating media.
- **Test strategy:** Pure timeline fixtures for timing sums, frame rounding, reordering, gaps, missing artifacts and mixed source sample rates.

### D019 — Real static-image MP4 renderer

- **Status:** Planned
- **Milestone:** M6
- **Priority:** P0
- **Goal:** Export actual playable video for one timeline snapshot.
- **Scope:** FFmpeg process adapter with fixed initial profile, derived audio normalization, static frames/simple cuts, progress/cancel, ffprobe/decode validation and immutable publication.
- **Out of scope:** Transitions, captions, GPU encoding requirement and scene-MP4 caching.
- **Dependencies:** D007, D018, D040, D044.
- **Main code areas:** New app/providers/ FFmpeg adapter, app/modules/video_rendering.py result contract and render service; tests.
- **Acceptance criteria:** Fixture produces MP4 containing decodable audio/video with expected dimensions and duration tolerance; nonzero exit/truncation never publishes success.
- **Test strategy:** Fake process failure/cancel tests and mandatory synthetic real FFmpeg integration smoke with no AI/network.

### D020 — Project and section editor UI

- **Status:** Planned
- **Milestone:** M7
- **Priority:** P0
- **Goal:** Create/open a project and edit its script through application services.
- **Scope:** PySide6 project screen, section list/text edit, structured generation action, save and bindings to existing split/merge/reorder commands.
- **Out of scope:** Implementing editing rules in widgets, audio/scene panels and full undo browser.
- **Dependencies:** D002, D003, D014, D038, D039.
- **Main code areas:** New app/desktop/ project/section widgets and presentation adapters; UI tests.
- **Acceptance criteria:** User can create/open/edit/split/merge/reorder and reopen with stable revisions; errors preserve unsaved text and valid saved state.
- **Test strategy:** Qt tests with fake application ports plus SQLite-backed user-flow smoke; no provider imports from widgets.

### D021 — Audio controls and playback UI

- **Status:** Planned
- **Milestone:** M7
- **Priority:** P0
- **Goal:** Select a compatible voice and operate section audio without blocking Qt.
- **Scope:** Catalog/selection controls, short preview, section generation, original/tempo playback and progress/cancel state through services.
- **Out of scope:** New provider registry/cache, reference-audio intake and silent CPU/provider fallback.
- **Dependencies:** D010, D011, D020.
- **Main code areas:** New app/desktop/ audio panel; adapters for app/tts/catalog.py, selection.py and preview.py; UI tests.
- **Acceptance criteria:** Preview and production use the same effective selection; stale audio is labeled and playable; generation leaves editing responsive.
- **Test strategy:** Qt tests with fake audio/jobs, existing selection identity tests and manual playback in the packaged spike environment.

### D022 — Scene and prompt editor UI

- **Status:** Planned
- **Milestone:** M7
- **Priority:** P0
- **Goal:** Inspect and change each scene's visual independently.
- **Scope:** Scene cards/text/time display, prompt editing/regeneration, image import/generation and active variant selection wired to services.
- **Out of scope:** Freeform video editing, local model installation and business rules in UI.
- **Dependencies:** D015, D016, D017, D020.
- **Main code areas:** New app/desktop/ scene panel and presentation adapters; UI tests.
- **Acceptance criteria:** Changing one prompt or image does not call TTS or modify another scene; failed generation preserves prior selection and manual text.
- **Test strategy:** Fake service call assertions, manual prompt preservation and persisted selection round-trip UI smoke.

### D023 — Timeline Lite editor

- **Status:** Planned
- **Milestone:** M7
- **Priority:** P0
- **Goal:** Expose a bounded timeline without a multitrack editor.
- **Scope:** One visual layer/narration track, clip selection/order, source ranges and safe boundary/offset controls through the timeline service.
- **Out of scope:** Whole-film proxy rendering (D046), arbitrary cut mid-word, keyframes and multitrack mixing.
- **Dependencies:** D018, D021, D022.
- **Main code areas:** New app/desktop/ timeline widgets and application editing adapters; UI tests.
- **Acceptance criteria:** Displayed durations/order match the persisted timeline; invalid bounds fail without corrupting it; clip moves keep their intended audio reference.
- **Test strategy:** Qt command tests and timeline round trips using known sample boundaries and changed selection/order.

### D024 — Selective regeneration composition

- **Status:** Planned
- **Milestone:** M8
- **Priority:** P0
- **Goal:** Turn dependency planning into one bounded user operation across existing stages.
- **Scope:** Compose already implemented services/jobs for rebuild-missing/stale or one selected output, preserve user-owned variants and reuse fresh dependencies; expose truthful outcomes.
- **Out of scope:** New generation stages, CAS implementation (D040), distributed scheduling and render-fragment cache.
- **Dependencies:** D005, D006, D010, D013, D015, D017, D019, D023, D040, D046.
- **Main code areas:** New app/application/ regeneration composition and desktop action binding; integration tests.
- **Acceptance criteria:** After editing B in A-B-C, only B's actual dependent generations run; A/C checksums remain; canceled/crashed work resumes safely; late B1 output never selects over B2.
- **Test strategy:** Offline end-to-end call-count, checksum, concurrent-edit and crash/restart scenarios over real SQLite/files with fake providers.

### D025 — Installable MVP acceptance

- **Status:** Planned
- **Milestone:** M9
- **Priority:** P0
- **Goal:** Prove the complete 16-step MVP boundary on the delivered package.
- **Scope:** Assemble release evidence for the already implemented application, supported Piper profile, project reopen, selective edit, actual MP4 and recovery on clean Windows.
- **Out of scope:** Adding missing product behavior as incidental fixes, new runtimes or calling mock output production-ready.
- **Dependencies:** D024, D041.
- **Main code areas:** docs/desktop/ release evidence and packaged acceptance harness/fixtures; existing integration tests.
- **Acceptance criteria:** All 16 MVP steps pass without system Python/FFmpeg; explicitly selected voice language is correct; real output plays; A/C generation is reused after B edit. Record installer size, downloads, machine/profile and duration evidence.
- **Test strategy:** Clean Windows install-to-export manual smoke with real Piper and FFmpeg; automated offline flow and interruption tests. Failures return to their owning task rather than broadening this gate.

### D026 — One real structured LLM adapter

- **Status:** Planned
- **Milestone:** M10
- **Priority:** P1
- **Goal:** Replace mock-only script/prompt generation with one configured provider.
- **Scope:** Select one vendor at task kickoff, implement its existing LLM protocol adapter, strict output validation, settings/usage, timeout/throttling and credential redaction; wire service selection.
- **Out of scope:** Multiple vendors, source retrieval, model training and secrets in project DTOs.
- **Dependencies:** D014, D015, D025.
- **Main code areas:** app/providers/ adapter/settings/factory, script/prompt composition and usage boundary; transport tests.
- **Acceptance criteria:** Actual structured output becomes editable sections/prompts; malformed or failed responses preserve current revisions; serialized errors/config contain no credentials.
- **Test strategy:** Offline injected transport for success/schema/error/rate-limit cases; explicit credentialed smoke outside default CI after provider choice.

### D027 — One real image API adapter

- **Status:** Planned
- **Milestone:** M10
- **Priority:** P1
- **Goal:** Generate useful visuals through the established image contract.
- **Scope:** Select one API provider, map prompt/size/settings, fetch verified image output, record actual parameters/provenance and integrate cancellation/failure reporting.
- **Out of scope:** Local image runtime (future separately scoped task), multiple vendors, img2img and character consistency.
- **Dependencies:** D017, D025.
- **Main code areas:** app/providers/ image adapter/factory, image application composition; transport tests.
- **Acceptance criteria:** Real image is selected/exportable; regeneration retains older variants; timeout/corrupt output cannot replace a valid image.
- **Test strategy:** Offline transport and decode fixtures; explicit remote smoke after provider selection. No network in default tests.

### D028 — Application-wide GPU resource lease

- **Status:** Planned
- **Milestone:** M10
- **Priority:** P1
- **Goal:** Prevent concurrent model operations from exhausting one device.
- **Scope:** One active GPU lease across projects/preview, lightweight availability reporting, worker ownership, unload/restart, bounded OOM recovery and explicit capability-tested fallback decision.
- **Out of scope:** Concurrent multi-model scheduling, automatic provider substitution and pretending empty_cache unloads live tensors.
- **Dependencies:** D007, D025.
- **Main code areas:** app/runtime/ resource manager, coordinator lease integration; tests.
- **Acceptance criteria:** Two jobs never own the same GPU simultaneously; crash/OOM releases ownership; device changes are recorded in the request/result identity.
- **Test strategy:** Deterministic fake-device/process contention, OOM and restart tests; measured hardware smoke is attached when D029 provides a real profile.

### D029 — Managed Chatterbox profile

- **Status:** Planned
- **Milestone:** M10
- **Priority:** P1
- **Goal:** Expose the existing Chatterbox adapter through a tested isolated runtime.
- **Scope:** Pinned compatible Python/Torch/TTS profile, on-demand assets, language/device health check, model lifecycle and production selection using the GPU manager.
- **Out of scope:** Rewriting Chatterbox, changing XTTS policy and bundling every model in base installer.
- **Dependencies:** D009, D010, D028, D045.
- **Main code areas:** app/providers/chatterbox_v3.py integration, runtime manifests/provisioning and selection wiring; tests.
- **Acceptance criteria:** Configured Windows/device profile generates and resumes section audio through the app; missing GPU/runtime fails honestly; supported source languages are evidenced.
- **Test strategy:** Offline profile/dispatch tests plus explicit real Windows/GPU synthesize-resume/unload smoke with versions, peak memory and retained artifact evidence.

### D030 — Approved reference-audio intake

- **Status:** Planned
- **Milestone:** M10
- **Priority:** P1
- **Goal:** Use controlled reference audio consistently in preview and production.
- **Scope:** Import/checksum/validate audio, persist approval metadata, expose opaque-ID resolver and minimal intake/selection UI with existing capabilities.
- **Out of scope:** General voice marketplace, changing XTTS production restrictions and leaking local paths.
- **Dependencies:** D004, D021, D029, D044.
- **Main code areas:** app/tts/selection.py, preview.py, storage/reference service and small desktop intake control; tests.
- **Acceptance criteria:** Same approved ID works in preview and narration; changed bytes/unapproved ID fail or change identity; earlier source/approval history survives.
- **Test strategy:** Offline resolver, tampering, approval and identity tests plus explicitly supplied reference smoke where authorized.

### D031 — Measured text-audio alignment adapter

- **Status:** Planned
- **Milestone:** M10
- **Priority:** P1
- **Goal:** Obtain accurate boundaries for continuous section speech.
- **Scope:** One alignment protocol/adapter, versioned boundary artifact, monotonicity/coverage checks and explicit low-confidence/omission outcomes; preserve user text.
- **Out of scope:** Automatic script rewriting from STT, caption UI and universal aligner support.
- **Dependencies:** D012, D025.
- **Main code areas:** app/providers/ alignment boundary and adapter, timing service and artifact metadata; tests.
- **Acceptance criteria:** Known-text fixture maps to measured audio; omissions and low confidence are reported; failed alignment retains previous timing.
- **Test strategy:** Offline timed-audio fixtures and fake runtime failure cases; explicit real aligner smoke evaluates sentence/word synchronization.

### D032 — Synchronized optional captions

- **Status:** Planned
- **Milestone:** M10
- **Priority:** P1
- **Goal:** Export readable captions from measured timing.
- **Scope:** Use alignment output for ordered caption segments, SRT/ASS artifact and optional renderer burn-in/export toggle.
- **Out of scope:** Animated karaoke styles and estimated timings labeled as aligned.
- **Dependencies:** D019, D031.
- **Main code areas:** app/modules/captions.py, domain/caption_track.py, renderer and small caption option; tests.
- **Acceptance criteria:** Caption intervals fit the selected audio; disabled captions require no provider; optional output is playable/exportable and text order is preserved.
- **Test strategy:** Serialization/bounds tests and a real FFmpeg synthetic caption smoke; no AI/network dependencies.

### D033 — History and variant restoration UI

- **Status:** Planned
- **Milestone:** M11
- **Priority:** P2
- **Goal:** Restore prior edits and chosen media without regenerating them.
- **Scope:** History view for retained project/section snapshots and image/audio variants, transactional selection restore and resulting freshness display.
- **Out of scope:** Garbage collection, event-sourcing rewrite and unlimited storage promises.
- **Dependencies:** D024, D025.
- **Main code areas:** app/application/ history service, SQLite selections and app/desktop/ history view; tests.
- **Acceptance criteria:** Restoring B1 leaves B2 available and updates only actual dependencies; selecting an older image triggers no provider call.
- **Test strategy:** Persistence/UI round trips, expected-version conflict tests and provider call-count assertions.

### D034 — Application update and rollback

- **Status:** Planned
- **Milestone:** M11
- **Priority:** P2
- **Goal:** Update the installed application while preserving projects and a working fallback.
- **Scope:** Versioned application package check/install, signature/integrity verification, restart boundary and rollback on failed activation.
- **Out of scope:** Updating a running model environment (D043), schema design/migrations (D042) and new product behavior.
- **Dependencies:** D025, D042.
- **Main code areas:** Installer/updater configuration and app lifecycle boundary; Windows update tests.
- **Acceptance criteria:** Failed application update leaves the previous app launchable; projects are retained; unsupported project schemas are not written.
- **Test strategy:** Offline update packages with tampering/interruption cases and clean-Windows install-upgrade-rollback smoke.

### D035 — Optional FastAPI project and job adapter

- **Status:** Planned
- **Milestone:** M12
- **Priority:** P2
- **Goal:** Expose shared services without duplicating desktop state or rules.
- **Scope:** Bind create/open/read project and enqueue/read/cancel job operations to application ports and durable state; make serving opt-in with controlled access.
- **Out of scope:** Whole legacy preset migration, artifact download (D053), desktop HTTP requirement and a second persistence model.
- **Dependencies:** D024, D025.
- **Main code areas:** app/api/ routes/dependencies/schemas and composition; ASGI integration tests.
- **Acceptance criteria:** API and desktop service calls observe identical project/job state; requests enqueue actual work; disabled API is not required for desktop startup.
- **Test strategy:** Offline ASGI tests sharing temporary repositories with application calls, including invalid ownership and stale revision requests.

### D036 — One still-image motion effect

- **Status:** Planned
- **Milestone:** M11
- **Priority:** P3
- **Goal:** Add bounded visual motion without changing generated imagery.
- **Scope:** One Ken Burns effect represented in clip settings, validated transform bounds and shared proxy/final render behavior.
- **Out of scope:** General animation graph, multiple effects and image/video generation.
- **Dependencies:** D019, D023, D046.
- **Main code areas:** Timeline effect values, FFmpeg compiler and small UI control; media tests.
- **Acceptance criteria:** Same timeline/settings produce matching preview/final motion; source image checksum is unchanged; disabled effect preserves existing output behavior.
- **Test strategy:** Pure transform/compiler tests and small frame-sampled FFmpeg fixture smoke.

### D037 — Measured render-fragment cache

- **Status:** Planned
- **Milestone:** M11
- **Priority:** P3
- **Goal:** Reduce repeat rendering only where profiling demonstrates value.
- **Scope:** One explicit cache unit/profile with input fingerprints, stream/timebase compatibility, invalidation and final assembly checks.
- **Out of scope:** General distributed cache, changing timeline semantics and adding cache without measured benefit.
- **Dependencies:** D019, D024; recorded render bottleneck measurement.
- **Main code areas:** Renderer/cache storage and benchmark evidence; media tests.
- **Acceptance criteria:** A changed fragment reuses compatible unaffected segments, rejects mismatched profiles and produces the correct final timeline; report measured benefit.
- **Test strategy:** Cache-hit/miss and corruption tests plus decoded media/timing comparison against an uncached fixture render.

### D038 — Transactional section split and merge

- **Status:** Planned
- **Milestone:** M1
- **Priority:** P0
- **Goal:** Provide small pure/service editing operations without hiding lineage changes.
- **Scope:** Split at explicit text boundary and merge selected adjacent sections, new identities with retained source lineage, new script snapshot and invalidation metadata.
- **Out of scope:** Audio splicing, implicit LLM edits and UI widgets.
- **Dependencies:** D001, D003, D005.
- **Main code areas:** app/application/ section editing and narrow domain helpers; unit/integration tests.
- **Acceptance criteria:** Split/merge preserves original revisions and unaffected section IDs; invalid selection fails atomically; changed sections require appropriate downstream work.
- **Test strategy:** Text-boundary and lineage unit tests; SQLite rollback/reopen tests for both commands.

### D039 — Persist section ordering changes

- **Status:** Planned
- **Milestone:** M1
- **Priority:** P0
- **Goal:** Reorder sections atomically while retaining identities and media.
- **Scope:** Application reorder command with expected revision, complete unique section list, new script order and dependency impact.
- **Out of scope:** Timeline widgets, rewriting section text and TTS regeneration.
- **Dependencies:** D001, D003, D005.
- **Main code areas:** app/application/ section order command and repository transaction; tests.
- **Acceptance criteria:** Reorder A-B-C to C-A-B retains all section/revision identities and existing media; stale/missing/duplicate IDs are rejected.
- **Test strategy:** Pure order validation and repository expected-version/round-trip tests.

### D040 — Revision-aware publication gate

- **Status:** Completed — PASS
- **Milestone:** M8
- **Priority:** P0
- **Goal:** Prevent a completed old job from replacing a newer edit.
- **Scope:** Transactional comparison of expected input revisions/generation selection at publication; retain valid obsolete results as historical variants; handle duplicate completion idempotently.
- **Out of scope:** Whole regeneration planner, artifact byte storage rewrite and UI.
- **Dependencies:** D004, D005, D006.
- **Main code areas:** app/application/ result publication, job/artifact repository transactions; integration tests.
- **Acceptance criteria:** B1 result completing after B2 is saved remains attached to B1, never active for B2; two completions cannot overwrite each other; failed publication preserves prior selection.
- **Test strategy:** Concurrent-edit, duplicate-completion and crash-between-file/index/selection tests using temporary SQLite/files.
- **Implementation boundary:** Extend the existing artifact index with a separately versioned D040 table extension and reuse D004 streaming/journal recovery. On the coordinator thread, use an attached D006 queue in the artifact-index SQLite transaction so artifact registration, conditional selection and job completion commit together; project revisions are read under the existing D003 exclusive session guard. Enqueue records exact section/script inputs and a generation token, atomically reserving that token with the queued job. A small trusted completion hook in D007 runs after worker cleanup and before terminal queue success; D040 jobs cannot bypass publication through ordinary D006 completion. Existing project, queue and base artifact schemas and legacy job behavior remain unchanged; no general migration framework or second byte store is introduced.
- **Evidence:** [D040 publication contracts and recovery](D040_RESULT_PUBLICATION.md): exact enqueue snapshots and D005 dependencies survive history/reopen; B1 completion after B2 is retained without selection; the latest reserved generation wins in both completion orders; duplicate completion returns the original decision without importing or reselecting. Publication failures retain prior selection. Six actual process-crash cases verify file/index/selection/queue boundaries, with D004 orphan/committed-stage recovery and D006 interruption recovery. D007's trusted completion hook runs after worker cleanup; worker references cannot bypass the gate.
- **Validation:** Focused tests — 139 passed, including 39 D040 cases; `python -m pytest backend/tests` — 914 passed; `git diff --check`, new-file whitespace, documentation links and task-status scope checks — PASS (2026-09-13, Windows/isolated Python 3.11.9). All tests offline; no real TTS required. D010 and later tasks were not started; branch remains unmerged for review.

### D041 — Windows MVP installer

- **Status:** Planned
- **Milestone:** M9
- **Priority:** P0
- **Goal:** Package the already working desktop and its managed CPU entry path.
- **Scope:** Installer over standalone bundle, pinned Qt media/FFmpeg binaries, app/API/dev dependency separation, user data locations, uninstall preservation, component notices and release-signing procedure/evidence.
- **Out of scope:** Auto-update engine, new editor features, all-model bundle and changing system Python/drivers.
- **Dependencies:** D002, D008, D009, D019, D023, D044, D046.
- **Main code areas:** Packaging/installer configuration, pyproject.toml dependency groups and desktop composition; Windows smoke assets.
- **Acceptance criteria:** Installed app locates bundled media and private runtime without PATH; launch/uninstall does not delete projects; release records provenance/licenses and signing outcome.
- **Test strategy:** Clean Windows install/launch/media/worker/uninstall smoke, Unicode and non-admin paths; inspect package for tests, secrets, caches and unnecessary AI weights.

### D042 — Project schema migration and backup

- **Status:** Planned
- **Milestone:** M11
- **Priority:** P2
- **Goal:** Upgrade project formats without corrupting existing work.
- **Scope:** One explicit version-to-version migration with consistent SQLite/media backup, unsupported-version refusal and recovery from interrupted upgrade.
- **Out of scope:** Generic migration framework, cloud synchronization and silent downgrade.
- **Dependencies:** D003, D004, D025.
- **Main code areas:** app/storage/ schema/version handling and backup service; integration tests.
- **Acceptance criteria:** Old fixture upgrades preserving IDs/media selections; newer unknown schema stays untouched; injected failure retains a restorable original.
- **Test strategy:** Versioned database fixtures, rollback/interruption tests and backup restore checks including WAL-consistent snapshots.

### D043 — Runtime profile update and rollback

- **Status:** Planned
- **Milestone:** M11
- **Priority:** P2
- **Goal:** Replace AI environments without mutating an active job's dependencies.
- **Scope:** Side-by-side profile installation, lease-aware activation, health check, retained previous version and explicit rollback.
- **Out of scope:** Application updater, automatic weight changes and deleting leased environments.
- **Dependencies:** D008, D025, D028, D029.
- **Main code areas:** app/runtime/ profile version/activation manager and installer manifests; tests.
- **Acceptance criteria:** Active job retains its original environment; failed health check leaves prior profile selected; new identity records activated version.
- **Test strategy:** Fake provisioner/lease tests and explicit Windows runtime upgrade/rollback smoke using isolated profiles.

### D044 — Workspace path containment

- **Status:** Planned
- **Milestone:** M1
- **Priority:** P0
- **Goal:** Prevent project/media access from escaping configured storage roots.
- **Scope:** Resolved-path validation for imports/reads/publication, Windows junction/symlink/traversal/drive/ADS cases, opaque ownership-aware keys and safe subprocess path arguments.
- **Out of scope:** Arbitrary filesystem browser, encryption and claims of a full hostile-process sandbox.
- **Dependencies:** D004.
- **Main code areas:** app/storage/ resolver and artifact access, process path construction; unit/platform tests.
- **Acceptance criteria:** Traversal or redirected destination cannot access outside workspace; legitimate Unicode/space paths work; unknown artifact IDs do not probe arbitrary files.
- **Test strategy:** Temporary-root traversal/ownership tests and Windows junction/ADS cases where platform-supported; record unavailable OS capabilities explicitly.

### D045 — Runtime profile manifest contract

- **Status:** Completed — PASS
- **Milestone:** M2
- **Priority:** P0
- **Goal:** Define a small reproducible installation and health-check boundary.
- **Scope:** Versioned profile descriptor for interpreter/architecture/device, pinned packages/hashes, model requirements, provenance/license references and typed health-check outcome.
- **Out of scope:** Installing packages, executing untrusted manifest commands and discovering arbitrary plugins.
- **Dependencies:** D006.
- **Main code areas:** New app/runtime/ profile values/schema and approved Piper manifest; unit tests.
- **Acceptance criteria:** Invalid or incompatible profiles are rejected before installation; catalog/discovery does not import Torch or load a model; all required package identities are explicit.
- **Test strategy:** Offline schema/compatibility/provenance fixtures and lazy-import checks against default environment.
- **Evidence:** [D045 profile contract and self-review](D045_RUNTIME_PROFILES.md): strict immutable v1 descriptor, fixed health-check outcome, host/wheel compatibility, dependency closure and curated content allowlist. Approved Piper CPU manifest pins embedded CPython 3.11.9, Piper 1.6.0 and six transitive wheels, plus the MSVC 14.44.35211.0 native runtime identified by static PE inspection; every artifact has exact source/version/hash/size/license references. Public wheel hashes and active metadata constraints were verified without installation. Discovery is offline and imports no optional runtime; model requirements reference the existing catalog. Manifest loads directly from the application wheel.
- **Validation:** Focused profile/queue/protocol/lifecycle tests — 125 passed, including 48 D045 cases; `python -m pytest backend/tests` — 771 passed; offline wheel resource smoke, `git diff --check` and new-file whitespace checks — PASS (2026-09-12, Windows/Python 3.11). D008 provisioning/clean-Windows acceptance remains separate; no runtime packages installed.

### D046 — Scene and whole-film preview

- **Status:** Planned
- **Milestone:** M6
- **Priority:** P0
- **Goal:** Inspect the same selected media and timeline that will be exported.
- **Scope:** Image plus selected audio-range preview and lower-resolution film proxy using the renderer/snapshot, cache key, cancellation and stale indication; wire preview controls.
- **Out of scope:** A second timeline interpretation, scene MP4 intermediates for every edit and realtime effects engine.
- **Dependencies:** D002, D019, D023.
- **Main code areas:** app/application/ preview service, renderer profile and app/desktop/ playback adapter; tests.
- **Acceptance criteria:** Proxy references the exact timeline snapshot; changing one image invalidates the proxy without TTS calls; obsolete preview never presents itself as current.
- **Test strategy:** Snapshot/cache/call-count tests, audio-range checks and packaged Qt playback smoke for the generated proxy.

### D047 — Reference-aware storage cleanup

- **Status:** Planned
- **Milestone:** M11
- **Priority:** P2
- **Goal:** Recover disk space without erasing retained project history.
- **Scope:** Storage usage report, disposable work/cache cleanup and explicit collection of unreferenced/unpinned artifacts with grace period and dry-run summary.
- **Out of scope:** Deleting active variants, shared model cache owned by another component and unlimited automatic history retention.
- **Dependencies:** D004, D025, D033, D042.
- **Main code areas:** app/storage/ reachability/cleanup service and small storage UI; tests.
- **Acceptance criteria:** Active and retained snapshot references survive cleanup; interrupted jobs are protected; only eligible files are removed and accounting matches.
- **Test strategy:** Temporary graph/filesystem fixtures covering pinned, historical, active-job, orphan and interrupted-cleanup cases.

### D048 — Ground a script in supplied sources

- **Status:** Planned
- **Milestone:** M12
- **Priority:** P2
- **Goal:** Preserve useful research/dossier work as optional source-grounded production.
- **Scope:** One controlled supplied-text intake path, immutable source provenance, fact-to-source references through existing research/dossier output and QA for unsupported references.
- **Out of scope:** General web crawler, automatic fact-truth guarantees and blocking manual-text MVP.
- **Dependencies:** D004, D026, D044.
- **Main code areas:** app/modules/research.py, dossier.py, qa.py and small source intake adapter; tests.
- **Acceptance criteria:** Fixture sources remain attributable in generated findings; unreadable sources and ungrounded claims/references are reported rather than fabricated.
- **Test strategy:** Offline supplied-source and fake-LLM fixtures for valid attribution, missing material and invalid reference IDs.

### D049 — Durable approval and revised-artifact continuation

- **Status:** Planned
- **Milestone:** M12
- **Priority:** P2
- **Goal:** Retain review history and resume only permitted downstream operations.
- **Scope:** Persist existing approval decisions and revised artifact selection, validate ownership/version, connect approved continuation to application execution and expose through optional API.
- **Out of scope:** Approval on every desktop edit, new review framework and overwriting rejected artifacts.
- **Dependencies:** D024, D035, D052.
- **Main code areas:** app/domain/approval.py, application review service, storage and app/api/routes/approvals.py; tests.
- **Acceptance criteria:** Reject/request-changes retains original bytes/history across restart; approved revision resumes allowed downstream work while unrelated completed output is reused.
- **Test strategy:** Offline service/ASGI-to-fake-engine tests for reject, revised approval, restart and stale/foreign artifact decisions.

### D050 — Durable publishing execution

- **Status:** Planned
- **Milestone:** M12
- **Priority:** P2
- **Goal:** Publish an approved export without duplicate submission during normal retries.
- **Scope:** Use existing publishing adapter with durable attempt/publication identity, explicit user action, known-success reuse and honest uncertain-result recovery; map recorded localization decisions.
- **Out of scope:** Automatic publishing schedules, invented auto-dubbing API and promising exactly-once delivery without provider evidence.
- **Dependencies:** D049, D055, D056.
- **Main code areas:** app/modules/publishing.py, app/providers/youtube_publishing.py integration, job storage and optional publishing routes; tests.
- **Acceptance criteria:** Retry after recorded success reuses publication ID; lost response yields uncertain/reconcile state rather than blind upload; decisions survive restart.
- **Test strategy:** Offline fake transport for success, timeout-after-submit, retry and restart; real publishing only under a separate explicit authorized smoke.

### D051 — Optional thumbnail artifact

- **Status:** Planned
- **Milestone:** M12
- **Priority:** P3
- **Goal:** Export a reviewable thumbnail from selected visuals.
- **Scope:** One thumbnail derivation from an imported/generated image, immutable storage and inclusion in export/handoff metadata.
- **Out of scope:** New generative model, automatic publication and blocking normal MP4 export.
- **Dependencies:** D016, D055.
- **Main code areas:** Small thumbnail module/service, artifact store and export mapping; tests.
- **Acceptance criteria:** Enabled thumbnail is inspectable and checksummed; disabled thumbnail requires no provider and does not block export.
- **Test strategy:** Synthetic image derivation and enabled/disabled export fixture tests.

### D052 — Canonical preset compatibility

- **Status:** Planned
- **Milestone:** M12
- **Priority:** P2
- **Goal:** Preserve both existing batch workflows alongside desktop editing.
- **Scope:** Repair short preset's missing script dependency, compose actual module definitions and connect selected legacy run start/resume to durable application execution; keep source language/localization policies.
- **Out of scope:** Making the desktop depend on legacy presets, fake definitions to bypass dependency checks and new content formats.
- **Dependencies:** D024, D035.
- **Main code areas:** app/workflow/presets.py, registry.py as needed, application batch composition and app/api/routes/workflow_runs.py; tests.
- **Acceptance criteria:** Both canonical presets validate and execute through real module definitions with fakes; impossible dependency combinations fail; HTTP start represents actual queued execution.
- **Test strategy:** Offline canonical short/long composition tests and ASGI-to-job tests with persisted results, no real providers.

### D053 — Controlled API artifact delivery

- **Status:** Planned
- **Milestone:** M12
- **Priority:** P2
- **Goal:** Retrieve real stored bytes and export bundles through the optional API.
- **Scope:** Ownership-aware opaque artifact/bundle downloads, streaming/range handling where needed, integrity/missing-artifact behavior and access control.
- **Out of scope:** Arbitrary local file endpoints and a second export format.
- **Dependencies:** D035, D044, D055.
- **Main code areas:** app/api/routes/artifacts.py, workflow_runs.py download binding and storage reader; ASGI tests.
- **Acceptance criteria:** Delivered bytes match registered artifact/bundle checksum; unknown, foreign or traversal identifiers fail without leaking paths.
- **Test strategy:** Offline ASGI streaming/range/integrity/access tests against temporary real artifacts.

### D054 — Controlled video asset intake

- **Status:** Planned
- **Milestone:** M12
- **Priority:** P3
- **Goal:** Retain the future user-supplied B-roll path without overloading image MVP.
- **Scope:** Import/probe one supported video profile, preserve provenance/checksum and expose a prepared asset through AssetProvider-compatible application intake.
- **Out of scope:** Generated clips, multitrack editing and claiming timeline video rendering before a separately scoped renderer extension.
- **Dependencies:** D016, D019, D044.
- **Main code areas:** app/providers/ asset adapter, media intake and artifact metadata; media tests.
- **Acceptance criteria:** Valid fixture video is stored and probe metadata survives project move; invalid media is rejected; image intake remains unchanged.
- **Test strategy:** Synthetic FFmpeg video/probe fixtures, failure and relocation tests; no external media download.

### D055 — Durable local export bundle

- **Status:** Planned
- **Milestone:** M12
- **Priority:** P2
- **Goal:** Preserve existing export/manifest functionality around actual desktop outputs.
- **Scope:** Connect ExportModule to selected timeline/render and decisions, materialize manifest/snapshots and requested available media with truthful missing-optional reporting.
- **Out of scope:** HTTP transport, publishing and regenerating content during packaging.
- **Dependencies:** D004, D019, D025.
- **Main code areas:** app/modules/export.py, export_manifest.py, new application export composition and storage; tests.
- **Acceptance criteria:** Bundle references the exact real MP4 and selected artifacts with matching checksums; missing optional captions/thumbnail are explicit; reopen preserves export identity.
- **Test strategy:** Offline bundle-content/checksum/snapshot tests and failure injection without any provider invocation.

### D056 — Durable publishing and localization handoff

- **Status:** Planned
- **Milestone:** M12
- **Priority:** P2
- **Goal:** Separate persisted publication intent/review from actual network submission.
- **Scope:** Persist existing platform/localization handoff models and decisions for a selected export, maintain source-language separation and register explicit approval state.
- **Out of scope:** Uploading, automatic dubbing and changing source narration language from target settings.
- **Dependencies:** D003, D055.
- **Main code areas:** app/domain/ platform/localization handoff mapping, storage and application handoff service; tests.
- **Acceptance criteria:** Approved export/handoff identity and per-language history survive restart; localization changes leave source artifacts untouched.
- **Test strategy:** Offline repository/application tests for decision history, ownership, source/target separation and no-network construction.

### D057 — One optional local image runtime

- **Status:** Planned
- **Milestone:** M10
- **Priority:** P1
- **Goal:** Offer local image generation without adding heavy dependencies to every install.
- **Scope:** Select one supported model at task kickoff, add pinned runtime/model profile using existing provisioning and GPU lease, adapt ImageGenerationProvider and record measured device requirements.
- **Out of scope:** Multiple diffusion stacks, ComfyUI management framework, img2img, character consistency and generated video.
- **Dependencies:** D009, D017, D025, D028, D045.
- **Main code areas:** app/providers/ local image adapter, runtime profile and image composition; contract/device tests.
- **Acceptance criteria:** Optional install creates a real image through the shared contract; absent runtime leaves import/API paths usable; OOM preserves prior image and releases GPU.
- **Test strategy:** Offline fake-runtime contract tests and explicit Windows/GPU download-generate-unload smoke with sizes, versions and memory evidence.

## Backlog provenance and deferred scope

There are **57 implementation tasks**: D001-D037 preserve the 37 subjects from the
accepted desktop analysis, with large subjects narrowed through D038-D047. D048-D057
retain valuable optional work and an explicit local-image extension. None is
completed merely by publishing this plan.

| Original analysis item | Backlog treatment |
| --- | --- |
| 01-19 | D001-D019, with bounded supporting work split as listed below |
| 20: section editor plus editing operations | D020 UI; D038 split/merge; D039 persistent reorder |
| 21-22 | D021-D022 |
| 23: Timeline Lite and preview | D023 timeline UI; D046 coherent preview |
| 24: regeneration and stale-job protection | D024 composition; D040 early publication gate |
| 25: installable MVP | D025 acceptance only; D041 distributable installer |
| 26-32 | D026-D032; D027 is one API adapter, D057 separately covers a local image runtime |
| 33: history and disk usage | D033 history/selection; D047 accounting/cleanup |
| 34: updates | D034 application update; D042 schema migration; D043 runtime update |
| 35-37 | D035-D037 |
| 04/16: storage/import safety | D004 publication and D016 image intake; D044 resolved-path containment |
| 08: managed CPU profile | D045 profile contract before D008 provisioning |

The previous API-first ROADMAP is replaced, not kept as a competing queue. Its
valuable subjects remain represented here:

| Previous product work | Destination |
| --- | --- |
| Connect workflow execution to API | D035 optional service adapter and D052 canonical presets, after desktop MVP |
| Persist projects/runs/jobs/decisions | D003/D006 foundation; D049/D056 optional review/handoff state |
| Review revised artifacts and resume | D040/D024 editing safety; D049 optional review continuation |
| Real export bundles/downloads | D019 real MP4; D055 bundle; D053 optional HTTP bytes |
| Real LLM | D026 |
| Supplied-source research/dossier/QA | D048 |
| Approved reference voice | D030 |
| Alignment and captions | D031/D032 |
| Visual assets | D016/D017/D027/D057; optional supplied video D054 |
| Real rendering | D019 |
| Publishing/localization | D056 durable handoff and D050 explicit execution |
| Project/review/voice UI | D020-D023/D046, with optional review D049 |
| Background jobs | D006/D007/D024 before MVP, not after HTTP integration |
| Thumbnail | D051 |

Later concepts not yet sized into implementation tasks include arbitrary video
timeline clips, img2img/character references, music/SFX, general transitions,
multi-user workspaces, billing, live sync, scheduling, marketplaces and advanced
analytics. They are explicitly outside the current task contracts. Add a bounded
task and dependency evidence before implementing one; do not hide it in D054 or
another convenient extension. Audio-only/script-only behavior can reuse existing
modules; a new dedicated product path needs a separately selected task.

## Validation and completion policy

Before implementation, inspect the selected task's current code dependencies and
baseline changes. Use exact focused behavioral tests named or introduced by that
task, followed by the repository's required full suite and diff check from the
repository root with an isolated Python 3.11+ environment:

```sh
python -m pytest backend/tests
git diff --check
```

Documentation edits additionally check Markdown links/anchors, task IDs, required
fields, dependency existence/acyclicity, milestone membership and P0 release-gate
coverage. Do not introduce permanent task-validation modules to perform these
checks. They are ordinary review/validation, not a revived orchestration system.

Default tests remain deterministic and offline, using temporary projects, fake
providers, fake transports and no GPU/model downloads. Focused media tests use
synthetic redistributable fixtures and a pinned local FFmpeg. Hardware, model
downloads, external provider calls and packaged Windows playback are explicit
separate smoke evidence; neither a mock nor a skipped hardware test proves them.

For each completed task, record changed areas, passing commands and any required
smoke/profile evidence with its status in this backlog or a linked evidence note.
If required acceptance remains unverified, keep it uncompleted and state the gap.
New tests should exercise behavior and failure modes, not mirror implementation.
Preserve existing API/provider compatibility until a specific task changes it.

MVP completion is D025 plus all its P0 dependencies, not a count of task headings.
No commit, merge, deployment or next-task execution is implied by task completion;
follow the current user's requested scope for each branch.

## Technical references

These explain deployment constraints behind the accepted analysis. They do not
pin future package versions; each runtime/installer task must verify its selected
build and record compatibility evidence.

- [PySide6 deployment](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html)
- [Qt Multimedia deployment and backend constraints](https://doc.qt.io/qt-6/qtmultimedia-index.html)
- [Python virtual-environment portability](https://docs.python.org/3/library/venv.html)
- [Python embeddable distribution](https://docs.python.org/3/using/windows.html#the-embeddable-package)
- [SQLite WAL concurrency and filesystem constraints](https://sqlite.org/wal.html)
- [FFmpeg licensing and build obligations](https://ffmpeg.org/legal.html)
- [NVIDIA CUDA compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/why-cuda-compatibility.html)
- [Windows SmartScreen reputation](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)

## Documentation-branch validation record

Recorded on 2026-09-11 for this documentation-only plan, not implementation evidence
for any D### task:

- Focused repository static/documentation tests plus `test_t074.py`: 22 passed.
- Full `python -m pytest backend/tests` using the existing isolated Python 3.11
  environment: 497 passed, with no real model/provider execution.
- Backlog review: 57 unique tasks with all required fields, 12 consistent milestone
  memberships, acyclic dependencies, a valid P0 sequence and all 32 P0 tasks covered
  by the D025 release dependency closure.
- Documentation link scan: 38 Markdown files, 870 local link/anchor occurrences;
  518 resolve. The 352 unresolved occurrences are pre-existing relative source-file
  references in the four `docs/archive/source-repo-insights/` analyses, all verified
  against baseline HEAD. They describe other source repositories, not new desktop
  plan paths. No new or current-product documentation link/anchor failure was found.
- External link reachability: all 34 distinct Markdown HTTP(S) targets responded
  successfully. This verifies reachability, not the continuing truth of every
  historical source statement.
- No product source, product tests, runtime configuration or implementation task
  status was changed. `git diff --check` and new-document whitespace checks pass.

Historical source-relative links are retained as archival evidence; no speculative
upstream URLs or placeholder files were introduced to make them appear resolved.
They do not block D001, whose acceptance is pure domain behavior.

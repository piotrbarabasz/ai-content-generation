# Domain model

This page describes implemented models. The accepted project-owned section,
immutable revision and timeline model is specified in the
[desktop plan](../desktop/IMPLEMENTATION_PLAN.md#project-and-pipeline-model).
SectionRevision and ScriptRevision provide the D001 foundation described below;
[D003 storage](api-and-storage.md#editable-project-persistence-d003) retains them.
D005 adds the dependency values below. D018 adds the immutable timeline snapshot
described below. Other target models remain governed by the desktop plan.

Entities are Python dataclasses with explicit validation in `backend/app/domain`;
HTTP schemas are separate Pydantic models in `backend/app/api/schemas.py`.
`DomainEntity` supplies IDs and timestamps; `DomainValidationError` reports domain
validation failures. Internal snake_case fields map to camelCase API payloads.

| Model | Role and relationships |
| --- | --- |
| `Project` | Workspace reference, name, content/genre/platform/language/tone and lists of config/run IDs |
| `ContentBrief` | Project topic, objective, audience, constraints, duration and success criteria |
| `WorkflowConfig` | Project's preset, content type, genre, duration, platform, source language, module toggles, provider settings, voice/render/caption/asset settings, approval and export policies |
| `ProviderConfig` | Provider type/name, enabled state and settings; resolution is through `ProviderRegistry` |
| `WorkflowRun` | Config reference, stage/status, timestamps, errors, artifact IDs and checkpoint IDs |
| `GenerationJob` | Module attempt, retry count, status, timestamps, output IDs and optional usage metadata |
| `JobRequest` / `JobAttempt` / `JobProgress` | Immutable desktop operation inputs, separately retained durable attempts, claim ownership and reported phase/count progress (D006) |
| `Artifact` | Run, producing module, type, relative storage key, metadata and creation time |
| `Script` | Versioned text, language, word count and optional approval timestamp |
| `NarrativeSegment` | Ordered narrative text, title, role and duration estimate |
| `SectionRevision` | Frozen editorial text/title/role with stable section/project IDs and a parent revision ID |
| `ScriptRevision` | Frozen ordered selection of exact section revisions, stable script/project IDs and parent snapshot ID |
| `RenderScene` | Ordered render unit, scene-plan reference, timing hint and visual intensity |
| `TimelineRevision` / `TimelineClip` | Frozen ordered selected media, exact audio sample spans and rational offsets, separately rounded video frame boundaries, output timebase and fit/fill policy (D018) |
| `Voiceover` | Text reference, provider, audio storage key, duration and optional approval time |
| `CaptionTrack` / `CaptionSegment` | Legacy caption metadata plus validated ordered subtitle segments; D032's separate immutable `SynchronizedCaptionTrack` binds measured segments to one exact timeline and exports SRT/ASS |
| `VideoRender` | Render storage key, duration, format and optional approval time |
| `ExportBundle` | Manifest reference, required/conditional artifacts, missing optional artifacts and approval/provider summaries |
| `ApprovalCheckpoint` / `ApprovalDecision` | Artifact-specific review state and preserved reviewer decision history |
| `ExportConfig` | Localization targets/strategy and downstream export settings, separate from source language |
| `PlatformHandoff` / `ArtifactReference` / `YouTubeMetadata` | Approved export identity, checksummed references and validated platform metadata |
| `LocalizationHandoff` / `LocalizationTarget` / `LocalizationDecision` | Per-language manual platform status, acceptance history and custom-audio fallback metadata |

## Editorial revision values (D001)

`SectionRevision` lives in `domain/narrative_segment.py`; `ScriptRevision` lives
in `domain/script.py`. They are frozen values, not subclasses of the mutable
`DomainEntity`. Existing `NarrativeSegment.create` and `Script.create` remain
unchanged for legacy modules. There is no second editorial section identity:
`SectionRevision.section_id` identifies the narrative segment across edits.

`ScriptRevision.sections` is an immutable tuple selecting one exact revision per
section. `edit_section` produces a new section revision and a new script snapshot;
`select_section_revision` can select a retained earlier revision; `reorder` accepts
only a complete permutation of existing section IDs. Old snapshots remain intact.
An empty selection is a draft. A project repository must retain snapshots for
history across restarts; these values perform no persistence, scheduling or lookup
of parent IDs in a global history store.

`SectionRevision.from_segment` copies legacy content with its existing segment ID.
`ScriptRevision.from_legacy` requires explicit segments from the script's run,
orders them by unique positive ordinals and preserves script ID/language. Segment
text is authoritative at this explicit boundary: flat script text is not parsed
into guessed sections. Mutable legacy objects are never retained in snapshots.
Run metadata, approval state and duration estimates remain on the legacy objects;
importing a snapshot does not constitute approval or measured audio timing.

### Split and merge topology (D038)

Split/merge create new section identities and one immutable child script snapshot;
unaffected section revisions remain identical. Exact cross-identity source slices
and downstream impact are represented by the frozen values in
`domain/section_edit.py`. The lineage is reconstructed durably from D003's exact
parent/child snapshots, keeping `SectionRevision.parent_revision_id` reserved for
edits of one stable identity. See [D038 semantics and evidence](../desktop/D038_SECTION_SPLIT_MERGE.md).

### Section ordering impact (D039)

`domain/section_order.py` applies one complete permutation to the selected
section IDs. Its child `ScriptRevision` retains the exact existing
`SectionRevision` values and only changes their order. `SectionOrderImpact`
records the prior/current order, the D005 fingerprint of the current ordered ID
list and every reusable section ID. A changed order requires a new timeline and
video render; it does not require new audio, scene, prompt or image outputs. An
identical requested order is a no-write result with no downstream work. See
[D039 semantics and evidence](../desktop/D039_SECTION_ORDERING.md).

## Immutable timeline snapshot (D018)

`domain/timeline.py` defines frozen `AudioSpan`, `TimelineMedia`, `TimelineClip`,
`OutputTimebase` and `TimelineRevision` values. `application.timeline.TimelineCompiler`
consumes an explicit ordered selection of timing/scene IDs and audio variants
through an injected resolver. `storage.timeline.ProjectTimelineMedia` verifies
current project selections and retained bytes through D011, D013 and D016.

Audio keeps half-open sample ranges and rational durations/offsets. Video uses
cumulative nearest-frame boundaries, with ties rounded up and zero-frame clips
rejected. Versioned JSON and a content-derived ID pin inputs, ordering, timing
quality, timebase and fit/fill. Reordering moves audio with the image and changes
only the timeline snapshot. There is no renderer, timeline database table or
automatic active-timeline selection. See [D018 rules and evidence](../desktop/D018_IMMUTABLE_TIMELINE.md).

D023 adds explicit editing through `application.timeline_editing` and immutable
parent-linked snapshots in `storage.timeline_edits`. The final validated event
selects the current timeline; earlier revisions remain retained. The Qt Timeline
Lite panel edits pinned paired visual/audio clips with contiguous offsets and
measured sentence-boundary controls. See [D023 behavior and limits](../desktop/D023_TIMELINE_LITE.md).

## Consumed inputs and freshness (D005)

`domain/dependencies.py` supplies immutable `InputEdge`, `RequestFingerprint` and
`DependencyDeclaration` values. A declaration records the actual consumed source
fingerprints, selected artifact identities/checksums, relevant settings, effective
generation identity and algorithm version. `ArtifactDependency` references an
existing published artifact; it is not a replacement artifact model.

`application/invalidation.evaluate_freshness` compares these records with explicit
current request/source/selection snapshots. It returns `fresh`, `stale` or
`missing`, with independent `FailedAttempt` information. It performs no storage,
provider calls, scheduling or selection changes. Manual provenance flags changed
context for review while preserving the selected variant as a reusable input.
See [D005 semantics and evidence](../desktop/D005_DEPENDENCIES.md).

## Durable desktop jobs (D006)

`domain/generation_job.py` retains the existing mutable `GenerationJob` workflow
DTO unchanged. The desktop queue uses frozen `JobRequest`, `JobAttempt` and
`JobProgress` values from the same module: the requested operation and its input
snapshot remain immutable while each explicit retry creates a separate attempt.
It does not persist or reinterpret legacy workflow-run records.

Attempts use `queued`, `running`, `completed`, `failed`, `canceled` and
`interrupted`. Progress is a reported phase with optional integer counts, not an
invented inference percentage. `JobAttempt.failure(job)` maps a recorded failure
to D005 `FailedAttempt` evidence without changing selected artifact freshness.
`jobs.coordinator.JobCoordinator` depends on an injected repository port and clock,
with no SQLite, UI, provider or worker imports. See [D006 queue semantics and
evidence](../desktop/D006_DURABLE_JOBS.md).

`domain/publication.py` adds D040 `PublicationSnapshot` and `PublicationResult`.
The snapshot binds project-owned immutable section values, optional whole-script
revision and an enqueue generation token to the existing D005 request. The result
records the original publication decision; it is not a mutable artifact or a
permanent freshness flag. The injected `ResultPublicationService` orchestrates
publication without importing storage or worker/provider infrastructure. See
[D040 selection and replay semantics](../desktop/D040_RESULT_PUBLICATION.md).

`domain/section_audio.py` exposes the retained D010 raw `SectionAudio` value from
existing artifact metadata: section/revision identity, artifact checksum, sample
rate and PCM frame count. Duration is measured as frames divided by sample rate.
The value does not turn technical TTS chunks into narrative sections or scenes.
See [D010 section-audio behavior](../desktop/D010_SECTION_AUDIO.md).

D011 reuses that measured `SectionAudio` representation for processed WAVs.
Immutable `audio_derivative` metadata adds the consumed raw artifact/checksum,
tempo, processor contract version, deterministic derivative key and measured
input/output durations. D005 dependencies and separate D040 raw/processed
selections describe currentness; no parallel audio entity or selection schema is
introduced. See [D011 derivative semantics](../desktop/D011_TEMPO_DERIVATIVE.md).

D012 adds immutable `SourceSpan`, `SpeechChunkBoundary`, `SpeechBlock` and
`SpeechBoundaryMap` values. Original text ranges and cumulative measured PCM
frames cover the complete section/WAV; sentence blocks group technical chunks
without creating editorial sections or render scenes. `SectionAudio` exposes an
optional map from retained metadata. Tempo maps use measured duration ratios and
explicitly approximate internal positions; neither map is word alignment.
Legacy audio remains readable without invented boundaries. See [D012 contracts
and validation](../desktop/D012_SPEECH_BOUNDARIES.md).

D031 adds a separate immutable `SpeechAlignment` rather than relabeling D012's
chunk measurements as word timing. Each entry retains an exact lexical source
range and sentence identity plus an aligned, low-confidence or omitted outcome.
Timed entries use monotonic PCM frame intervals bound to the exact selected WAV;
coverage and provider insertions are explicit. The original section text remains
authoritative. See [D031 alignment](../desktop/D031_SPEECH_ALIGNMENT.md).

D032 maps only complete D031 word measurements contained by the exact D018 audio
sample spans. `SynchronizedCaptionTrack` keeps a content-derived identity,
timeline/alignment IDs, language, selected-audio duration and ordered millisecond
segments. Static SRT and ASS serializers do not add display padding or estimated
timing; low-confidence, omitted, cut-boundary or mismatched audio inputs fail
closed. See [D032 synchronized captions](../desktop/D032_SYNCHRONIZED_CAPTIONS.md).

D013 adds `ProjectRenderScene` beside the unchanged legacy `RenderScene`, with
project/section ownership, whole-sentence source ranges and stable visual scene
identity. `ScenePlan` retains ordered immutable scene semantics; an explicit
`AcceptedScenePlan` retains the reviewed plan and reviewer attribution.
`SceneTimingSet` binds separate `SceneTiming` sample intervals to exact audio and
the acceptance ID. Voice/tempo retiming never replaces accepted scenes or their
visual descriptions. See [D013 semantic grouping, persistence and evidence](../desktop/D013_SCENE_PLANNING.md).

D014's `ScriptSectionInput` validates and freezes ordered title/role/text values
from the versioned desktop structured script response. The application maps them
directly into existing `SectionRevision` / `ScriptRevision` values, preserving
repeated roles and optional CTA. Manual append/edit uses the same revision model;
no new script entity or role-based identity inference is introduced. See
[D014 structured contract and evidence](../desktop/D014_STRUCTURED_SCRIPT.md).

D015 adds project-owned `PromptContextRevision` families for pinned film briefs
and visual styles, immutable `VisualPromptRevision` values with consumed
`PromptInputs` and D005 provenance, and explicit `PromptSelection` events.
Generation/manual edits retain variants; selection is separate and protected by
the expected previous event identity. Context changes derive freshness/manual
review without rewriting prompts, scenes or audio. See [D015 contracts and
evidence](../desktop/D015_VISUAL_PROMPTS.md).

## Existing workflow and service boundaries

Canonical presets are `short_video` and `long_form_script_voiceover`. Content type
enums include short video, long-form video, audio-only and script-only; enum
availability does not imply a dedicated preset exists. Config validation rejects
invalid enum values and overlapping enabled/disabled modules.

`WorkflowConfig.language` is the generated source language. Localization target
languages live in `ExportConfig`; they must not replace that field. English is the
current long-form production default, with Polish provider fixtures and support
retained. See [the localization decision](../decisions/0002-english-first-localization-boundary.md).

Approval states are `not_required`, `pending`, `approved`, `rejected`,
`changes_requested` and `skipped`. A rejected or changes-requested artifact remains
available. Only resumable checkpoints permit continuation under the applicable
policy. See [workflow execution](workflow-engine.md) for the API integration limit.

The TTS service adds immutable catalog descriptors for **provider**, **model** and
**voice**, plus preview results and chunk/synthesis manifests. These are service
contracts, not a second persisted workflow selection model. Technical narration
chunks preserve source text and must not become semantic scenes.

Old drafts also named `User`, `Workspace`, `SceneAsset`, `PromptTemplate`,
`BrandProfile` and a standalone `SpeechTimeline` class. Those classes are not
implemented. `workspace_id` is a reference, and `speech_timeline.json` is currently
a module-produced payload with estimated word timings. Do not import nonexistent
models or infer database persistence from the domain dataclasses.

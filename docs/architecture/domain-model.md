# Domain model

This page describes implemented models. The accepted project-owned section,
immutable revision and timeline model is specified in the
[desktop plan](../desktop/IMPLEMENTATION_PLAN.md#project-and-pipeline-model).
SectionRevision and ScriptRevision now provide the in-memory D001 foundation
described below. Other target models and database persistence remain planned.

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
| `Artifact` | Run, producing module, type, relative storage key, metadata and creation time |
| `Script` | Versioned text, language, word count and optional approval timestamp |
| `NarrativeSegment` | Ordered narrative text, title, role and duration estimate |
| `SectionRevision` | Frozen editorial text/title/role with stable section/project IDs and a parent revision ID |
| `ScriptRevision` | Frozen ordered selection of exact section revisions, stable script/project IDs and parent snapshot ID |
| `RenderScene` | Ordered render unit, scene-plan reference, timing hint and visual intensity |
| `Voiceover` | Text reference, provider, audio storage key, duration and optional approval time |
| `CaptionTrack` / `CaptionSegment` | Caption metadata and validated ordered subtitle segments; `serialize_srt` emits UTF-8-compatible text |
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

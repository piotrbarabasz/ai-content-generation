# Shorts Repository Migration Plan

## Purpose

This document maps the legacy shorts repository into the unified AI Content Studio architecture.
It focuses on the source capabilities that already exist in the shorts pipeline and shows how they
become first-class modules, providers, workflow presets and artifact records in the new system.

## Legacy Repository Snapshot

The shorts repository is strongest in the production layer:
- transcript intake
- scene segmentation
- speech timing and preparation
- asset preparation and projection
- video assembly and rendering
- caption planning and delivery
- export of video and subtitle artifacts

The repository is useful as a source of implementation heuristics, but it is not the target
architecture. The unified application should preserve behavior, not file layout.

## Target Architecture Mapping

| Legacy component | What it does today | Unified AI Content Studio target | Migration action |
| --- | --- | --- | --- |
| `scene_segmentation/planner.py` | Splits transcript into narrative scenes | `ScenePlanningModule` | Reuse logic after wrapping it behind module contracts and explicit input/output schemas |
| `scene_segmentation/feature_layer.py` and `decision_layer.py` | Heuristic scene scoring and ordering | `ScenePlanningModule` support code | Refactor into deterministic internal helpers instead of CLI-oriented scripts |
| `preparation_engine/application/preparation_pipeline.py` | Builds narrative, scene and speech timelines | `VoiceoverModule` and future timing helpers | Split timing concerns from intake and make speech timing an explicit artifact-producing step |
| `preparation_engine/domain/speech/transcription_service.py` | Transcribes audio and aligns words to scenes | `VoiceoverModule` plus timing-adjacent provider usage | Keep the timing heuristics, but move audio provider access behind `TranscriptionProvider` or `TTSProvider` abstractions |
| `image_prep/cli.py` | Crops and prepares images for scenes | `AssetSelectionModule` or asset preprocessing support | Refactor the image handling into reusable asset preparation logic and remove hardcoded filesystem assumptions |
| `video_base_engine/projection.py` | Maps scenes to visual assets and render specs | `AssetSelectionModule` and `VideoRenderingModule` | Lift projection logic into module-level planning so renderers receive structured scene specs |
| `video_base_engine/assembler.py`, `effects.py`, `motion.py` | Builds the final MP4 | `VideoRenderingModule` | Reuse render composition logic behind a renderer provider interface |
| `subtitle_engine/core/orchestrator.py` and delivery components | Generates semantic plans and ASS captions | `CaptionsModule` | Preserve caption planning and rendering behavior as a dedicated optional module |
| `voiceover` and audio prep helpers | Prepare and assemble audio artifacts | `VoiceoverModule` | Keep chunking and audio assembly behavior, but move provider selection and artifact storage into the new workflow model |

## Reuse, Refactor, Out of Scope

### Reuse

- Scene segmentation heuristics and pacing signals
- Speech timing and alignment heuristics
- Caption planning and ASS delivery behavior
- Video assembly and effect composition patterns
- Local artifact examples for scene, timeline and export files

### Refactor

- Hardcoded file paths and direct CLI entrypoints
- Tight coupling between input discovery, processing and output writes
- Implicit artifact naming and storage conventions
- Provider access embedded in scripts rather than interfaces

### Out of scope for MVP

- Publishing automation
- Multi-user collaboration
- Advanced analytics
- Thumbnail generation
- Marketplace asset search
- Any requirement that depends on a full UI editor

## Workflow Preset Impact

The shorts repository maps most directly to the `short_video` preset.

### Short Video preset

Recommended module path:
`brief` or `transcript` -> `scenePlanning` -> optional `voiceover` -> optional `captions` -> `videoRendering` -> `export`

What the legacy repository contributes:
- `scene_segmentation` becomes scene planning
- `preparation_engine` becomes timing and speech alignment support
- `subtitle_engine` becomes optional captions
- `video_base_engine` becomes video rendering
- the existing export outputs become the template for export packaging

### Legacy behavior to keep

- deterministic scene ordering
- explicit timeline outputs
- caption and render artifacts that can be inspected independently
- support for runs that continue when optional voiceover or captions are disabled

## Artifact Storage Migration

The legacy repo stores output directly in project folders. The new architecture must move to an
artifact store abstraction.

### New storage rules

- Every output becomes an artifact with type, owner workflow run, module source and storage reference
- Paths are resolved through the storage layer, not hardcoded in modules
- Scene plans, speech timelines, render plans, ASS files and MP4 outputs are stored as artifacts
- Export bundles should reference stored artifacts rather than duplicating raw file locations

### Suggested artifact mapping

- `scene_segmentation.json` -> scene plan artifact
- `narrative_plan.json` -> scene planning or narrative support artifact
- `speech_timeline.json` and `scene_timeline.json` -> timing artifacts
- `subtitle_semantic_plan.json` and `subtitle_render_plan.json` -> caption planning artifacts
- `subtitles.ass` -> caption delivery artifact
- `base_short.mp4` and final rendered MP4 -> video render artifacts

## Provider Migration

The shorts repo shows where provider abstractions are needed most:
- transcription and speech alignment should move behind `TranscriptionProvider`
- any future voice synthesis should move behind `TTSProvider`
- image handling should move behind `AssetProvider`
- video composition should move behind `VideoRendererProvider`
- storage must move behind `StorageProvider`

Deterministic mock providers should be the default for MVP so the short workflow can run without
real vendor credentials.

## Migration Phases

### Phase 1: Wrap existing behavior

- Convert the strongest existing pipeline pieces into module-shaped services
- Preserve deterministic output formats
- Make storage references explicit

### Phase 2: Introduce workflow orchestration

- Register short-video modules in the module registry
- Execute them through the core workflow engine
- Capture module status, artifact references and approval checkpoints

### Phase 3: Replace direct CLI assumptions

- Remove hardcoded paths from module code
- Move configuration to workflow config and provider config
- Keep the command-line scripts only as thin adapters, if retained at all

## Implementation Notes

- The shorts repository should not be copied wholesale into the new app.
- The goal is to preserve the proven production behaviors while separating them into module,
  provider and artifact boundaries.
- The short-video path should remain the fastest path to a completed export bundle because it is the
  clearest validation of the unified architecture.

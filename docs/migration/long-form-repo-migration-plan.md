# Long-form Repository Migration Plan

## Purpose

This document maps the legacy long-form repository into the unified AI Content Studio architecture.
It describes how research, dossier, planning, script generation, QA and voiceover behavior become
modules and artifacts in the new workflow engine.

## Legacy Repository Snapshot

The long-form repository is strongest in the narrative and research layer:
- source ingestion and validation
- research and retrieval
- dossier construction
- outline and narrative planning
- segment writing and script generation
- editing and QA
- voiceover preparation and synthesis
- artifact export

This repository is the clearest source for the long-form workflow path, but it must be decomposed
into reusable modules rather than retained as a single pipeline.

## Target Architecture Mapping

| Legacy component | What it does today | Unified AI Content Studio target | Migration action |
| --- | --- | --- | --- |
| `transcript/pipeline/ingestion.py` | Validates source manifests and builds a corpus | `ResearchModule` | Reuse the source-validation behavior, but move it behind storage and provider abstractions |
| `transcript/pipeline/rag.py` | Produces research notes and fact extraction | `ResearchModule` | Refactor into deterministic research retrieval with explicit artifact outputs |
| `transcript/pipeline/dossier.py` | Builds a structured story dossier | `DossierModule` | Keep dossier shaping logic as a dedicated module output linked to the workflow run |
| `transcript/pipeline/planner.py` | Creates narrative plans and scene structure | `OutlineModule` and `ScenePlanningModule` support | Split outline-level planning from any later scene planning concerns |
| `transcript/pipeline/segment_writer.py` | Generates segment-level script drafts | `ScriptGenerationModule` | Reuse the writing logic behind a module contract and provider abstraction |
| `transcript/pipeline/editor.py` | Merges and normalizes the final transcript | `PostProcessingModule` | Preserve cleanup behavior as a deterministic post-processing step |
| `transcript/pipeline/qa.py` | Runs quality checks and flags issues | `QAModule` | Keep the QA rules and make the report a formal artifact |
| `voiceover/application/generate_voiceover.py` and `voiceover/models/kokoro_tts.py` | Produces voiceover audio | `VoiceoverModule` | Reuse the audio flow, but route synthesis through `TTSProvider` and store outputs through the artifact layer |
| `transcript/utils/io.py` and voiceover artifact writers | Persist local outputs | `ArtifactStore` and `ExportModule` support | Replace direct writes with artifact-store-backed persistence and export packaging |

## Reuse, Refactor, Out of Scope

### Reuse

- Research ingestion and validation patterns
- Dossier structure and narrative fact modeling
- Outline and segment planning heuristics
- Script generation and editing flow
- QA reporting and gating behavior
- Voiceover chunking and assembly ideas

### Refactor

- Coupling to topic-specific logic and local output folders
- Direct LLM provider access embedded in pipeline code
- Input discovery and file writing tied to a single repository layout
- Implicit assumptions about export structure and artifact locations

### Out of scope for MVP

- Full publishing automation
- Collaborative editing
- Advanced analytics
- Marketplace asset search
- A dedicated UI editor for every intermediate artifact

## Workflow Preset Impact

The long-form repository maps most directly to the `long_form_script_voiceover` preset.

### Long-form Script + Voiceover preset

Recommended module path:
`sources` or `topic` -> `research` -> `dossier` -> `outline` -> `scriptGeneration` -> `postProcessing` -> `qa` -> optional `voiceover` -> `export`

What the legacy repository contributes:
- `ingestion` and `rag` become research
- `dossier` becomes the structured research summary
- `planner` becomes outline generation and long-form structure
- `segment_writer` becomes script generation
- `editor` becomes post-processing
- `qa` becomes the quality gate
- voiceover tooling becomes the optional narration path

### Legacy behavior to keep

- the workflow can start from either topic or sources
- research can be enabled or disabled depending on the run
- QA remains a first-class gate before export
- voiceover remains optional, not a hard requirement for completing the script path

## Artifact Storage Migration

The legacy repo uses local output folders for intermediate and final files. The new architecture must
make those outputs explicit artifacts.

### New storage rules

- Research notes, curated source sets, dossier files, outlines, scripts, QA reports and voiceover
  outputs are persisted through the artifact store
- Artifact metadata must capture workflow run ownership and module source
- Export bundles should reference the stored artifacts and include a manifest
- No module should write directly to hardcoded repo-relative output directories

### Suggested artifact mapping

- `rag_output.json` -> research artifact
- `story_dossier.json` -> dossier artifact
- `narrative_plan.json` -> outline or planning artifact
- `subsegments.json` and `segments.json` -> script generation intermediates
- `transcript_v1.txt` -> script artifact
- `qa_report.json` -> QA artifact
- `cleaned_transcript.txt` -> post-processing artifact
- `chunks.json` and `final_voiceover.wav` -> voiceover artifacts

## Provider Migration

The long-form repo shows the parts that should be provider-backed in the new app:
- LLM calls for research, planning, writing and QA become `LLMProvider` usage
- TTS synthesis becomes `TTSProvider` usage
- artifact writes become `StorageProvider` or `ArtifactStore` usage
- any future web or source fetch behavior should stay isolated from module logic

Deterministic mocks should be used first so the long-form workflow can run without external
credentials or network dependencies.

## Migration Phases

### Phase 1: Decompose the pipeline

- Separate ingestion, research, planning, writing, QA and voiceover responsibilities
- Define module inputs and outputs explicitly
- Ensure every stage emits artifact references rather than raw file assumptions

### Phase 2: Add workflow orchestration

- Register long-form modules in the module registry
- Connect the modules through the core workflow engine
- Add approval checkpoints where the spec requires them

### Phase 3: Align export with the unified bundle

- Package final artifacts through the export module
- Generate a manifest that describes included and missing optional outputs
- Keep the long-form path consistent with the short-video path for run tracking and artifact traceability

## Implementation Notes

- The long-form repository should be treated as a source of domain behavior, not a source of file
  structure.
- The most valuable migration outcome is a clean split between research, narrative writing, QA and
  voiceover generation.
- The long-form workflow is the best place to prove that the new architecture can handle a
  non-visual content path without depending on the short-video pipeline.

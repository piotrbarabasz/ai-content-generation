# Implementation Plan: AI Content Studio MVP

**Branch**: `001-ai-content-studio` | **Date**: 2026-07-06 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from /specs/001-ai-content-studio/spec.md

## Summary

Build a Python-first modular workflow engine that supports two MVP workflows: short video and long-form script plus voiceover. The first slice will use explicit domain models, local filesystem artifact storage behind interfaces, deterministic mock providers, provider registry validation, approval checkpoints, export bundle manifests and minimal API endpoints for project, workflow configuration, workflow runs, approvals and artifacts.

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: FastAPI, Pydantic, pytest, SQLAlchemy or a lightweight repository abstraction, structlog or logging

**Storage**: Local filesystem artifact store behind a StorageProvider interface; optional JSON or SQLite-backed metadata store for workflow state

**Testing**: pytest with unit, integration and contract tests

**Target Platform**: Linux/macOS/Windows server backend

**Project Type**: web-service / backend API with modular domain engine

**Performance Goals**: Support single-user local runs and small batch workflow execution without queueing

**Constraints**: Strict MVP scope, deterministic mock providers, no publishing, billing dashboard, advanced analytics or marketplace features, no hardcoded local paths

**Scale/Scope**: Single-user MVP with two presets and local artifact storage

## Constitution Check

- Modular workflow first: Pass. The plan centers on a CoreWorkflowEngine, module registry and workflow presets for short video and long-form script/voiceover.
- Module contracts: Pass. The plan defines explicit input/output/config contracts for each included module.
- Provider abstraction: Pass. The plan includes provider interfaces for LLM, TTS, transcription, captions, asset, rendering and storage.
- Artifact traceability: Pass. The plan includes artifact persistence, metadata and export bundle generation.
- Review and approval: Pass. The plan includes approval checkpoints for script, scene plan and export.
- MVP scope discipline: Pass. The plan explicitly excludes publishing, analytics, billing, collaboration and asset marketplace features.
- Narrative/render separation: Pass. The data model preserves NarrativeSegment and RenderScene as distinct concepts.
- Testability: Pass. The plan includes deterministic tests and mock providers.
- No hardcoded local paths: Pass. The plan uses configuration and an artifact store abstraction.
- Security and secrets: Pass. The plan uses environment-based configuration and excludes secrets from the repository.

## Project Structure

```text
backend/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── projects.py
│   │       ├── workflow_configs.py
│   │       ├── workflow_runs.py
│   │       └── artifacts.py
│   ├── domain/
│   │   ├── models/
│   │   ├── services/
│   │   └── value_objects/
│   ├── workflow/
│   │   ├── engine.py
│   │   ├── registry.py
│   │   ├── planning.py
│   │   └── execution.py
│   ├── modules/
│   │   ├── brief.py
│   │   ├── research.py
│   │   ├── dossier.py
│   │   ├── outline.py
│   │   ├── script.py
│   │   ├── post_processing.py
│   │   ├── qa.py
│   │   ├── scene_planning.py
│   │   ├── voiceover.py
│   │   ├── captions.py
│   │   ├── rendering.py
│   │   └── export.py
│   ├── providers/
│   │   ├── interfaces.py
│   │   ├── mocks/
│   │   └── adapters/
│   └── infrastructure/
│       ├── storage/
│       ├── config/
│       ├── logging/
│       └── tests/
└── tests/
    ├── unit/
    ├── integration/
    └── contract/
```

**Structure Decision**: A backend-first Python service with explicit domain, workflow, module, provider and infrastructure layers is the smallest structure that satisfies the spec and the constitution without introducing UI scope.

## Architecture

### Layered Architecture
1. Domain layer
   - Explicit models for project, workflow config, workflow run, generation job, artifact, script, narrative segments, render scenes, voiceover, captions, render output and export bundle.
   - Value objects for provider config, prompt templates and brand profile.
2. Workflow layer
   - CoreWorkflowEngine orchestrates module execution, provider validation, retry behavior, usage metadata capture and approval checkpoints.
   - ModuleRegistry exposes available modules and their capabilities.
   - WorkflowExecutionPlan and ModuleExecutionContext are used to compute execution order and module state.
3. Module layer
   - The MVP implements the required modules for brief, research, dossier, outline, script generation, post-processing, QA, scene planning, voiceover, captions, rendering and export.
   - Thumbnail, publishing and advanced asset selection are defined as stubs or future modules without implementation.
4. Provider layer
   - Interfaces isolate LLM, TTS, transcription, captions, asset, renderer, storage and publishing providers.
   - ProviderRegistry registers provider implementations by type and name, resolves providers from ProviderConfig and validates required providers for enabled modules before a workflow starts.
   - Mock implementations are used first.
5. Infrastructure layer
   - Minimal API endpoints, local artifact storage, logging, retry policy, configuration and tests.

## Data Model

The detailed entity definitions are captured in [data-model.md](data-model.md).

## Module Contracts

Each module follows the contract structure documented in [contracts/module-contracts.md](contracts/module-contracts.md).

## Provider Contracts

Provider interfaces and expected methods are documented in [contracts/provider-contracts.md](contracts/provider-contracts.md).

## Workflow Presets

### Short Video
- Canonical workflowPreset: short_video
- Default contentType: short_video
- Path: brief or transcript -> scene planning -> optional voiceover -> optional captions -> video render -> export
- Required modules: brief, scenePlanning, videoRendering, export
- Optional modules: voiceover, captions
- Expected artifacts: brief.json, scene_plan.json, optional voiceover reference, optional captions, render reference and export bundle.

### Long-form Script + Voiceover
- Canonical workflowPreset: long_form_script_voiceover
- Default contentType: long_form_video with videoRendering disabled by default
- Path: sources or topic -> research -> dossier -> outline -> script -> post-processing -> QA -> optional voiceover -> export
- Required modules: brief, outline, scriptGeneration, postProcessing, qa, export
- Optional modules: research, dossier, voiceover
- Expected artifacts: research.json when enabled, dossier.json when enabled, outline.json, script.txt, post_processed_script.txt, qa_report.json, optional voiceover reference and export bundle.

## Storage Strategy

- Use a StorageProvider interface with a local filesystem implementation for MVP.
- Persist artifacts under a configured root directory such as data/artifacts/<workflow_run_id>.
- Store metadata in JSON sidecar files with artifact type, module source, workflow run id and storage reference.
- Keep storage paths configurable and avoid hardcoded absolute paths.

## Export Bundle Contract

- Every export bundle must include manifest.json, workflow_config.json and workflow_run.json.
- manifest.json must include schemaVersion, exportId, projectId, workflowRunId, workflowPreset, contentType, contentGenre, durationProfile, createdAt, includedArtifacts, missingOptionalArtifacts, moduleResults, approvalSummary, providerSummary and artifactReferences.
- Conditional contents include script.txt, script.json, narrative_segments.json, render_scenes.json, captions.srt or captions.json, voiceover.wav or voiceover artifact reference, video.mp4 or video artifact reference, qa_report.json, research.json and dossier.json when those artifacts exist.
- Missing optional artifacts must be listed explicitly rather than treated as failures.

## Job Execution Strategy

- WorkflowRun owns the overall lifecycle.
- GenerationJob tracks each module execution attempt and retry count.
- ModuleResult may include optional usage metadata: providerName, inputTokens, outputTokens, estimatedCost and durationMs.
- A UsageTracker or NoopCostTracker records optional usage metadata without implementing billing or analytics.
- The first slice will run modules synchronously in-process.
- A future async queue can reuse the same WorkflowRun and GenerationJob models without changing the domain contract.
- Approval checkpoints pause execution until a reviewer approves, rejects, requests changes or skips the checkpoint according to approvalPolicy.

## Approval State Machine

- MVP approval checkpoints: script approval, scene plan approval and final export approval.
- Approval states: not_required, pending, approved, rejected, changes_requested and skipped.
- Pending checkpoints pause before downstream modules execute.
- Approved checkpoints allow workflow continuation.
- Rejected checkpoints keep the workflow paused and preserve the rejected artifact.
- Changes-requested checkpoints keep the workflow paused until a revised artifact exists and is approved or explicitly skipped by policy.
- Resume is allowed only when all blocking checkpoints are approved or skipped by approvalPolicy.

## API Boundaries

### Endpoints
- POST /api/v1/projects
- GET /api/v1/projects/{id}
- POST /api/v1/projects/{id}/workflow-configs
- POST /api/v1/workflow-runs
- GET /api/v1/workflow-runs/{id}
- GET /api/v1/workflow-runs/{id}/artifacts
- GET /api/v1/workflow-runs/{id}/approvals
- POST /api/v1/workflow-runs/{id}/approvals/{checkpoint_id}/approve
- POST /api/v1/workflow-runs/{id}/approvals/{checkpoint_id}/reject
- POST /api/v1/workflow-runs/{id}/approvals/{checkpoint_id}/request-changes
- POST /api/v1/workflow-runs/{id}/resume

### API Responsibilities
- Create and inspect projects and workflow configs.
- Start workflow runs.
- Return workflow status and artifact references.
- Support review actions for approval checkpoints and blocked resume attempts.

## Testing Strategy

- Unit tests for each domain model and module contract.
- Integration tests for workflow execution using mock providers.
- Contract tests for provider interfaces and API payloads.
- Deterministic tests for provider registry validation, approval gating, retries, optional module skipping, long-form workflow execution and export bundle contents.

## Migration Strategy from Existing Repos

- Reuse the existing research, dossier, outline, script-writing and export concepts from the long-form repo as module responsibilities.
- Reuse the short-form scene segmentation, speech-timing and render concepts from the shorts repo as the basis for scene planning, voiceover timing and render planning.
- Introduce explicit domain models and workflow orchestration rather than keeping logic embedded in scripts.
- Preserve existing artifact formats where possible while wrapping them in the new ArtifactStore and metadata conventions.

## Risks

- The existing repo artifacts are embedded in script and CLI code, which may require lifting into explicit modules.
- Some repository concepts such as rendering and captions are only partially implemented and need careful scoping for MVP.
- Export bundle requirements could expand if the workflow accumulates too many artifact types; the MVP should constrain them to the required manifest and artifact references.
- Any remaining ambiguity around default provider selection, review-policy edge cases or export bundle content variants is intentionally deferred to the corresponding implementation follow-up tasks rather than expanding MVP scope.

## Phased Implementation

### Phase 0: Foundation
- Create domain models and value objects.
- Create storage abstraction and local filesystem implementation.
- Define provider interfaces, provider registry, provider validation and mock implementations.
- Create workflow state models and API skeleton.
- Add security and secret-hygiene conventions for env-based provider configuration.

### Phase 1: First Vertical Slice
- Implement brief, scene planning, voiceover, export and status handling for short video.
- Support project creation, workflow config and workflow run execution.
- Persist artifacts and produce export bundle.

### Phase 2: Second Vertical Slice
- Implement research, dossier, outline, script generation, post-processing, QA and optional voiceover for long-form workflows.
- Add approval checkpoints for script, scene plan and final export.
- Expand export bundle contents and workflow status handling.

### Phase 3: Hardening
- Add retry policy, logging, contract tests and deterministic mock provider coverage.
- Refine API responses and artifact metadata.
- Keep publishing, analytics, billing and collaboration explicitly out of scope.

<!-- M004 REAL TTS PLAN EXTENSION START -->

## Phase 4: Real TTS Voiceover Vertical Slice (M004)

### Scope

M004 converts the existing mock voiceover reference into a real provider-neutral audio contract and adds Chatterbox Multilingual V3 as the first optional real adapter. The milestone stops after stable long-narration WAV generation. Semantic transcript-to-scene segmentation belongs to a later milestone.

### Architecture Decisions

1. **Preserve the existing provider system.** `ProviderConfig` remains the configuration input and `ProviderRegistry` remains the generic provider lookup mechanism. A TTS composition helper may instantiate `mock` or `chatterbox_v3` providers from `ProviderConfig`, but it must not create a second registry or a competing workflow configuration model.
2. **Keep workflow code provider-neutral.** `VoiceoverModule` and `CoreWorkflowEngine` depend on `TTSProvider` and a provider-neutral synthesis result only.
3. **Persist real media bytes.** `voiceover.wav` contains readable WAV bytes. Paths, URIs and temporary filenames are metadata or implementation details, not audio payloads.
4. **Keep heavy runtime dependencies optional.** Default installation and tests do not require PyTorch, Chatterbox, a GPU, network access or model downloads. Exact optional versions are documented in `docs/tts/CHATTERBOX_MANUAL_SPIKE.md`.
5. **Introduce one provider-neutral TTS service package.** `backend/app/tts/` owns technical chunking, resumable chunk orchestration, WAV assembly and benchmark/manifests. It must not contain concrete provider imports or semantic scene logic.
6. **Use technical chunking, not scene segmentation.** Paragraph/sentence splitting exists only to keep model requests bounded and recoverable.
7. **Resume by input identity.** Reuse requires matching normalized text, relevant provider/voice configuration identity and a valid WAV checksum/format.

### Planned Structure

```text
backend/app/providers/
├── interfaces.py
├── mock_tts.py
├── tts_result.py
├── tts_settings.py
├── tts_factory.py
└── chatterbox_v3.py

backend/app/tts/
├── __init__.py
├── chunking.py
├── assembly.py
├── chunk_synthesis.py
├── manifest.py
└── benchmark.py

backend/app/tooling/
└── tts_smoke.py

backend/tests/fixtures/narrations/
├── story_01_1min.txt
├── story_02_5min.txt
├── story_03_8min.txt
├── story_04_15min.txt
└── metadata.json
```

### Epic E007 - TTS Contract and Fixtures

- Validate fixed Polish narration fixtures.
- Add an explicit provider-neutral TTS result without breaking current behavior.
- Migrate mock TTS and `VoiceoverModule` to valid deterministic WAV bytes.

**Exit condition**: mock voiceover output is a readable WAV and the complete test suite passes.

### Epic E008 - Chatterbox Multilingual V3 Provider

- Record the successful human-run Chatterbox environment spike and deterministic optional dependency constraints.
- Keep Chatterbox/PyTorch/CUDA optional.
- Add a lazy Chatterbox Multilingual V3 adapter behind an injectable backend boundary.
- Compose providers from existing `ProviderConfig` and register them in existing `ProviderRegistry`.
- Add offline contract tests and a manual one-minute smoke runner.

**Exit condition**: CI proves the adapter contract with fakes and a human can generate or diagnose one real Polish Chatterbox Multilingual V3 WAV.

### Epic E009 - Long Narration Reliability

- Add deterministic technical chunking.
- Add per-chunk validation, retry and resume.
- Assemble compatible PCM WAV chunks without re-encoding.
- Add synthesis and benchmark manifests.
- Integrate long narration with `VoiceoverModule` without concrete-provider coupling.

**Exit condition**: the fifteen-minute fixture completes with a fake backend, resumes after simulated interruption and produces a valid final WAV plus manifests.

### Testing Boundaries

Default tests use only deterministic fixtures, mock/fake provider backends, temporary directories and small generated PCM WAV payloads. They must fail if they unexpectedly import or initialize the real Chatterbox runtime. Real-model execution is a manual smoke test and is never part of standard pytest or CI.

### Runtime Hygiene

The implementation must ignore private and generated paths such as:

```text
.runtime/tts/
.runtime/voices/
.runtime/model-cache/
data/tts-smoke/
*.speaker.wav
```

### Out of Scope

- semantic scene splitting,
- visual scene descriptions or image prompts,
- image generation,
- captions,
- video rendering,
- API redesign,
- database work,
- deployment,
- automatic model downloads in tests.

<!-- M004 REAL TTS PLAN EXTENSION END -->

## M005 TTS Runtime Hardening

M005 contains only E010, Long Narration Cache and Resume Integrity. It hardens the provider-neutral effective synthesis identity so cache reuse is correct across provider configuration and voice identity changes, prunes stale chunk records and artifacts, and makes manifest recovery and WAV finalization crash-safe and atomic. Benchmark and smoke evidence must report the actual synthesis configuration and distinguish generated, reused and failed chunks. The milestone adds an offline 15-minute interruption/resume acceptance path using deterministic fakes; CI must not execute a real model or access the network.

<!-- M006 MULTI-PROVIDER POLISH TTS PLAN EXTENSION START -->

## M006 Multi-Provider Polish TTS

M006 expands the existing provider-neutral TTS path rather than redesigning it. E011 first makes the real Chatterbox Multilingual V3 runtime reproducible and introduces a lazy provider-capability and usage-policy contract. E012 adds Piper as a fast local Polish provider with a curated, checksum-pinned voice catalog and native speaking-rate controls. E013 adds XTTS-v2 only as an evaluation provider, exposes provider selection through the existing ProviderConfig and TTS factory, and adds a manual same-text comparison runner.

The milestone uses separate optional runtime environments for Chatterbox, Piper and XTTS. The default test and agent environment remains lightweight. Real-model execution, network downloads and private reference audio are limited to explicit human-operated setup and smoke commands.

### Architecture constraints

- `VoiceoverModule`, chunking, cache, assembly and benchmark services remain provider-neutral.
- Provider selection uses the existing `ProviderConfig`, `TTSSettings`, `build_tts_provider` and `ProviderRegistry`.
- Heavy imports and model loading remain lazy.
- Effective synthesis identity includes the resolved provider, model/voice asset identity, language, device, generation settings and reference-audio checksum where applicable.
- Provider capability and usage-policy metadata can be inspected without loading models.
- XTTS-v2 is rejected outside explicit evaluation mode until a separate licensing decision authorizes another use.
- No test downloads models or executes a real provider.

### Delivery order

1. E011: repair and lock the Chatterbox real-runtime baseline; add runtime profiles and provider capabilities.
2. E012: add Piper, Polish voice asset management and Piper smoke/benchmark support.
3. E013: add evaluation-only XTTS-v2, complete provider selection and produce cross-provider comparison evidence.

### Out of scope

- UI provider selection,
- training or fine-tuning,
- cloud TTS services,
- scraping or committing voice references,
- automatic model downloads in tests,
- concurrent loading of several GPU models,
- captions, image generation, rendering and deployment.

<!-- M006 MULTI-PROVIDER POLISH TTS PLAN EXTENSION END -->

<!-- M007 ENGLISH-FIRST YOUTUBE PRODUCTION PLAN EXTENSION START -->

## M007 English-First YouTube Production

M007 moves the product from provider investigation to an English-first production and platform handoff path. English is the generated source-content and narration language. Localization is a later export/publishing concern: platform automatic dubbing is preferred when available, while custom localized audio remains an explicit fallback.

The architecture decision is recorded in `docs/decisions/0002-english-first-localization-boundary.md`. `WorkflowConfig.language` continues to describe generated source content. Validated localization targets and handoff policy are carried inside export/publishing configuration and must not mutate or replace that source-language field.

### Delivery order

1. E014 defines and validates the English source-language and localization boundary without adding provider or platform branches to orchestration.
2. E015 establishes a reproducible English Chatterbox production baseline and proves long-form resumable narration with provider-neutral artifact parity.
3. E016 produces a deterministic YouTube-ready export with metadata and UTF-8 English captions.
4. E017 composes publishing through the existing provider registry and records platform-localization handoff, human acceptance and custom-dub fallback state.

### Architecture constraints

- `WorkflowConfig.language` is the source language used by content generation and narration; it is not a localization target list.
- Localization configuration is validated at the export/publishing boundary and records target languages, preferred localization mode, acceptance requirements and custom-audio fallback metadata.
- TTS selection continues through `ProviderConfig`, `TTSSettings`, `build_tts_provider` and `ProviderRegistry`.
- Publishing selection uses `ProviderConfig`, `PublishingProvider` and `ProviderRegistry`; `CoreWorkflowEngine` does not select YouTube directly.
- `VoiceoverModule`, technical chunking, cache identity, WAV assembly and narration artifacts remain provider-neutral.
- YouTube-specific mapping stays behind export/publishing contracts and does not leak into narrative generation or rendering concepts.
- Automatic-dubbing handoff is represented truthfully as internal metadata and manual state until an authoritative platform API supports a stronger integration.
- Tests remain deterministic and offline; real TTS smoke and publishing calls are explicit human actions using isolated optional runtimes.

### Planned handoff artifacts

When the corresponding source artifacts exist, the YouTube-ready bundle contains the rendered video, English narration, deterministic English captions, upload metadata, source-language identity, artifact checksums and localization-handoff metadata. Missing optional inputs are reported rather than fabricated. Final publishing and localization acceptance remain reviewable steps after generation.

### Out of scope

- adding MOSS-TTS or another experimental provider to the production TTS factory,
- changing the provider-neutral narration architecture,
- invoking real models or platform APIs in default tests,
- claiming an unsupported YouTube automatic-dubbing API,
- hiding credentials or private localized audio in manifests,
- placing Chatterbox or YouTube conditionals in `CoreWorkflowEngine` or `VoiceoverModule`,
- automatic publish, merge or deployment without human approval.

<!-- M007 ENGLISH-FIRST YOUTUBE PRODUCTION PLAN EXTENSION END -->

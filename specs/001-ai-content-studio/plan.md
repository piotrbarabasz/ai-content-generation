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

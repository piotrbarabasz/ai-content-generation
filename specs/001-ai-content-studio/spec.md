# Feature Specification: AI Content Studio

**Feature Branch**: `001-ai-content-studio`

**Created**: 2026-07-06

**Status**: Draft

**Input**: User description: "Create the baseline specification for AI Content Studio."

## User Scenarios & Testing

### User Story 1 - Create a content project and configure workflow (Priority: P1)

A user wants to create a new content project for a specific output type, such as a short video or a long-form script. The system must let them choose the content type, genre, duration profile, platform, language, tone, enabled and disabled modules, and provider configuration before starting the workflow. For MVP, this experience may be API-first and backend-oriented with minimal or deferred UI.

**Why this priority**: This is the entry point for the product and defines the workflow engine behavior for all downstream modules.

**Independent Test**: A user can create a project, configure workflow settings and start a run without any implementation details leaking into the experience.

**Acceptance Scenarios**:

1. **Given** a new user has opened the product, **When** they create a project and set content type, genre, duration profile, platform, language, tone and provider preferences, **Then** the system stores a complete workflow configuration for that project.
2. **Given** a workflow configuration exists, **When** the user enables or disables specific modules, **Then** the workflow engine uses only the enabled modules for the run.

---

### User Story 2 - Run a short video workflow (Priority: P1)

A user wants to generate a short video from a brief or transcript. The system must execute the short video workflow path, including scene planning, optional voiceover, optional captions and rendering, and then produce an export bundle.

**Why this priority**: This proves the core MVP workflow and demonstrates the modular engine end to end.

**Independent Test**: A user can submit a brief or transcript and receive a workflow run with artifacts and an export bundle.

**Acceptance Scenarios**:

1. **Given** a valid short video workflow configuration, **When** the user starts the run with a brief or transcript, **Then** the system executes scene planning and produces an output package with an export bundle.
2. **Given** the workflow includes optional voiceover or captions, **When** those modules are enabled, **Then** the system generates the corresponding artifacts and includes them in the export bundle.

---

### User Story 3 - Run a long-form script and voiceover workflow (Priority: P1)

A user wants to create a long-form script and optional voiceover from sources or a topic. The system must run research when enabled, dossier creation when enabled, outline generation, script generation, post-processing, QA, optional voiceover and export.

**Why this priority**: This proves the second MVP workflow and shows that the engine can support both short and long-form content production.

**Independent Test**: A user can provide sources or a topic and receive a workflow run with script, QA report, voiceover and export artifacts.

**Acceptance Scenarios**:

1. **Given** a long-form workflow configuration exists, **When** the user provides sources or a topic, **Then** the system runs the enabled long-form modules and completes without voiceover when VoiceoverModule is disabled.
2. **Given** a workflow run is in progress, **When** a module produces an artifact, **Then** the system stores the artifact with metadata linked to the workflow run and module source.
3. **Given** a user starts the long-form preset from a topic, **When** the workflow runs with mock providers, **Then** the system produces outline, script, QA report and export bundle.
4. **Given** research is enabled, **When** the long-form workflow runs, **Then** research and dossier artifacts are persisted.
5. **Given** voiceover is disabled, **When** the long-form workflow runs, **Then** export still completes without a voiceover artifact; when voiceover is generated, the export bundle includes the voiceover artifact reference.

---

### User Story 4 - Review and approve workflow artifacts (Priority: P2)

A user or reviewer wants to inspect generated artifacts such as script, scene plan, QA, voiceover or captions and approve or reject them before the workflow continues.

**Why this priority**: Review checkpoints are essential to product quality and align with the constitution and MVP workflow requirements.

**Independent Test**: A reviewer can approve or reject an artifact and the workflow progresses or pauses accordingly.

**Acceptance Scenarios**:

1. **Given** a workflow artifact reaches a review stage, **When** a reviewer approves it, **Then** the workflow continues to the next module.
2. **Given** a workflow artifact reaches a review stage, **When** a reviewer rejects it, **Then** the workflow pauses and records the review outcome.
3. **Given** a workflow artifact reaches a review stage, **When** a reviewer requests changes, **Then** the workflow remains paused until a revised artifact is provided and approved or the checkpoint is explicitly skipped by policy.

---

### Edge Cases

- What happens when a module is disabled and the workflow still needs its output?
- How does the system behave when a provider fails or returns an error during a run?
- How does the system handle missing input or incomplete workflow configuration?

## Requirements

### Functional Requirements

#### Project & Workspace
- **FR-001**: The system MUST allow a user to create a workspace for one or more content projects.
- **FR-002**: The system MUST allow a user to create a project within a workspace and assign it a content type, genre, duration profile, target platform, language and tone.
- **FR-003**: The system MUST persist project state so that users can inspect prior workflow runs and output artifacts.
- **FR-004**: The system MUST allow projects to be associated with a workflow configuration and one or more workflow runs.

#### Workflow Configuration
- **FR-005**: The system MUST support the content types short_video, long_form_video, audio_only and script_only.
- **FR-006**: The system MUST support the content genres news, story, documentary, educational, tutorial, marketing, commentary and listicle.
- **FR-007**: The system MUST support the duration profiles 15_30s, 60s, 3_5min, 8_15min and custom.
- **FR-008**: The system MUST support the target platforms tiktok, youtube_shorts, youtube, instagram, linkedin and generic_export.
- **FR-009**: The system MUST support configuration of enabledModules and disabledModules for a workflow run.
- **FR-010**: The system MUST support providerConfig for LLM, TTS, transcription, captions, rendering, asset, storage and publishing providers.
- **FR-011**: The system MUST support the mandatory MVP workflow presets Short Video and Long-form Script + Voiceover.
- **FR-011a**: The system MUST use canonical workflowPreset values short_video and long_form_script_voiceover.
- **FR-011b**: WorkflowConfig MUST include id, projectId, workflowPreset, contentType, contentGenre, durationProfile, targetPlatform, language, tone, enabledModules, disabledModules, providerConfig, renderConfig, captionConfig, voiceConfig, assetConfig, approvalPolicy and exportConfig.
- **FR-011c**: WorkflowConfig validation MUST reject invalid enum values and any module present in both enabledModules and disabledModules.

#### Module Execution
- **FR-012**: The system MUST provide a CoreWorkflowEngine that orchestrates module execution for a workflow run.
- **FR-013**: The system MUST provide a ModuleRegistry that registers available modules and their capabilities.
- **FR-014**: The system MUST provide a ProviderRegistry that registers provider implementations and their capabilities.
- **FR-014a**: ProviderRegistry MUST register provider implementations by provider type and name.
- **FR-014b**: ProviderRegistry MUST resolve a provider by provider type and ProviderConfig.
- **FR-014c**: ProviderRegistry MUST validate required providers for enabled modules and fail fast before workflow execution when a required provider is missing.
- **FR-015**: The system MUST allow modules to be enabled or disabled per workflow run.
- **FR-016**: The system MUST support module retries for transient failures according to module-specific retry policy.
- **FR-017**: The system MUST record the status of each generation job as pending, running, completed, failed, skipped or waiting_for_approval.
- **FR-018**: The system MUST prevent a workflow from proceeding when a required module fails and no fallback path is available.
- **FR-019**: The system MUST allow optional modules to be disabled when downstream modules can use fallback input or the workflow can skip the optional stage.

#### Provider Settings
- **FR-020**: The system MUST allow provider selection and configuration without embedding provider-specific logic in the workflow engine.
- **FR-021**: The system MUST support mock providers first for LLM, TTS, captions and rendering when real providers are not available.
- **FR-022**: The system MUST allow provider settings to be validated before a workflow run begins.
- **FR-022a**: Provider validation MUST report invalid provider type, unknown provider name and missing provider errors before the workflow run starts.
- **FR-022b**: Disabled modules MUST NOT require provider validation for providers only needed by those disabled modules.

#### Research
- **FR-023**: The system MUST support research-based workflows that ingest topic or source information.
- **FR-024**: The system MUST allow research findings to be stored as structured artifacts and linked to a workflow run.
- **FR-025**: The system MUST support dossier creation from research outputs.

#### Script Generation
- **FR-026**: The system MUST support script generation from a brief, outline or research context.
- **FR-027**: The system MUST support post-processing for script cleanup and normalization.
- **FR-028**: The system MUST support QA evaluation for long-form script output.
- **FR-029**: The system MUST preserve a clear distinction between NarrativeSegment and RenderScene.

#### Scene Planning
- **FR-030**: The system MUST support scene planning for short video workflows.
- **FR-031**: The system MUST allow scene plans to be reviewed and approved before rendering.
- **FR-032**: The system MUST support asset planning and asset selection for visual workflows.

#### Voiceover
- **FR-033**: The system MUST support optional voiceover generation or ingestion for supported workflows.
- **FR-034**: The system MUST support speech timing alignment for voiceover and scene planning.
- **FR-035**: The system MUST allow voiceover output to be reviewed before rendering or export.

#### Captions
- **FR-036**: The system MUST support optional captions for video workflows.
- **FR-037**: The system MUST allow captions to be reviewed and approved before export.

#### Rendering
- **FR-038**: The system MUST support video rendering for workflows where a video output is requested.
- **FR-039**: The system MUST support thumbnail generation as an optional output.
- **FR-040**: The system MUST allow preview generation for review purposes.

#### Export
- **FR-041**: The system MUST generate an export bundle containing the relevant artifacts for a completed or partially completed workflow.
- **FR-042**: The system MUST persist export metadata and manifest information with the export bundle.
- **FR-043**: The system MUST include manifest.json, workflow_config.json, workflow_run.json, module artifact references, the script text when generated, the scene plan when generated, captions when generated, the voiceover reference when generated and the video render reference when generated in the export bundle.
- **FR-043a**: ExportBundle manifest.json MUST include schemaVersion, exportId, projectId, workflowRunId, workflowPreset, contentType, contentGenre, durationProfile, createdAt, includedArtifacts, missingOptionalArtifacts, moduleResults, approvalSummary, providerSummary and artifactReferences.
- **FR-043b**: ExportBundle MUST include script.txt when script text exists, script.json when structured script exists, narrative_segments.json when narrative segments exist, render_scenes.json when render scenes exist, captions.srt or captions.json when captions exist, voiceover.wav or a voiceover artifact reference when voiceover exists, video.mp4 or a video artifact reference when video render exists, qa_report.json when QA exists, research.json when research exists and dossier.json when dossier exists.
- **FR-044**: The system MUST allow export without full publishing automation.

#### Approval
- **FR-045**: The system MUST provide an ApprovalService for script, scene plan, QA, voiceover, captions and render review states.
- **FR-046**: The system MUST allow a workflow run to pause until approval is granted for a review-required stage.
- **FR-047**: The MVP MUST require approval checkpoints for script, scene plan and final export.
- **FR-047a**: Approval checkpoints MUST support the states not_required, pending, approved, rejected, changes_requested and skipped.
- **FR-047b**: If an approval checkpoint is pending, the workflow MUST pause before downstream modules execute.
- **FR-047c**: If an approval checkpoint is approved, the workflow MAY continue.
- **FR-047d**: If an approval checkpoint is rejected, the workflow MUST remain paused and downstream modules MUST NOT execute.
- **FR-047e**: If an approval checkpoint is changes_requested, a user or module MUST provide a revised artifact before resume.
- **FR-047f**: Resume MUST be allowed only when the checkpoint is approved or explicitly skipped according to approvalPolicy.
- **FR-047g**: Rejection MUST NOT delete artifacts; it MUST create an approval decision record and preserve the rejected artifact.

#### Jobs & Artifacts
- **FR-048**: The system MUST provide a WorkflowRun object that tracks the execution state of a workflow.
- **FR-049**: The system MUST provide a GenerationJob object for each module execution attempt.
- **FR-050**: The system MUST provide an ArtifactStore abstraction for persisting artifacts and metadata without coupling modules to filesystem paths.
- **FR-051**: The system MUST record each artifact with type, owner workflow run, module source and storage reference.
- **FR-052**: The system MUST support cost tracking for module execution and provider usage through a minimal MVP UsageTracker or NoopCostTracker interface.
- **FR-052a**: ModuleResult MAY contain optional usage metadata with providerName, inputTokens, outputTokens, estimatedCost and durationMs.
- **FR-052b**: Full billing dashboards, cost analytics and advanced usage reporting are out of scope for MVP.
- **FR-053**: The system MUST support local filesystem artifact storage behind StorageProvider or ArtifactStore interfaces for MVP.
- **FR-054**: The system MUST model WorkflowRun and GenerationJob from the start, while allowing later asynchronous queue execution.

### Non-Functional Requirements
- **NFR-001**: The system MUST be extensible so new modules and providers can be added without replacing the core engine.
- **NFR-002**: The system MUST use provider abstraction so workflow logic is decoupled from vendor-specific implementations.
- **NFR-003**: The system MUST be maintainable through clear module contracts and explicit interfaces.
- **NFR-004**: The system MUST be observable through workflow run status, job status, logs and artifact history.
- **NFR-005**: The system MUST support retry for transient provider and execution failures.
- **NFR-006**: The system MUST support asynchronous job execution for long-running modules, while allowing the first slice to run synchronously and locally.
- **NFR-007**: The system MUST persist intermediate and final artifacts with metadata for traceability and replay.
- **NFR-008**: The system MUST support cost control through configurable limits and cost tracking.
- **NFR-009**: The system MUST protect secrets and private runtime data through configuration and repository exclusions.
- **NFR-010**: The system MUST be testable with deterministic mock providers and isolated module tests.
- **NFR-011**: The system MUST be designed to scale beyond the MVP workflow by adding modules and providers without altering the core workflow model.

### Key Entities

- **Workspace**: A top-level container for one or more projects and shared settings.
- **Project**: A content production effort with a workflow configuration and one or more runs.
- **WorkflowConfig**: The user-selected workflow settings, modules and providers for a project.
- **WorkflowRun**: A single execution of a workflow configuration.
- **GenerationJob**: A discrete execution unit for one module or retry attempt.
- **Artifact**: A persisted output or intermediate object linked to a workflow run and module.
- **NarrativeSegment**: A logical story or script unit distinct from visual rendering.
- **RenderScene**: A timeline/rendering unit for visualization and assembly.
- **Approval**: A review or approval state for content-sensitive stages.
- **ProviderRegistry**: A registry of provider implementations keyed by provider type and provider name.
- **UsageTracker**: A minimal interface for recording optional usage metadata without implementing billing.
- **ExportBundle**: A package containing required workflow files, conditional artifact files or references and a manifest.

## Success Criteria

### Measurable Outcomes
- **SC-001**: Users can create a project and configure a valid workflow in under 5 minutes.
- **SC-002**: At least 90% of workflow runs complete with a visible status transition from pending to completed, failed, skipped or waiting_for_approval.
- **SC-003**: The system stores intermediate and final artifacts for every workflow run that reaches the artifact persistence stage.
- **SC-004**: MVP workflows support both short video and long-form script plus voiceover generation without requiring full publishing automation.
- **SC-005**: Review checkpoints can pause a workflow, resume it after approval, remain paused after rejection and record changes_requested decisions.
- **SC-006**: The MVP uses mock providers by default and can run the core workflows without requiring real external provider credentials.

## Assumptions

- Users will interact with the product through a guided project creation flow rather than raw configuration files.
- The MVP will focus on proving the modular workflow engine and will not attempt full publishing, advanced analytics, billing, multi-user collaboration or marketplace asset search.
- Cost tracking in MVP is limited to optional usage metadata and a UsageTracker or NoopCostTracker interface; full cost dashboards, billing and advanced analytics are excluded.
- External providers may be unavailable, so mock providers are acceptable for initial validation.
- The system will persist artifacts in a configurable store rather than relying on hardcoded local paths.
- The first implementation can be backend/API-first with minimal or deferred UI.

<!-- M004 REAL TTS EXTENSION START -->

## Extension: Real TTS Voiceover (M004)

### User Story 5 - Generate provider-swappable Polish voiceover (Priority: P1)

A developer wants to provide a fixed Polish narration and receive a playable WAV artifact through either the deterministic mock provider or Chatterbox Multilingual V3 without changing workflow orchestration.

**Independent Test**: The one-minute narration fixture produces a valid RIFF/WAVE artifact through the provider-neutral voiceover path, while default tests remain offline and GPU-free.

**Acceptance Scenarios**:

1. **Given** a fixed narration and the mock TTS provider, **When** voiceover synthesis runs, **Then** the artifact store receives valid deterministic WAV bytes rather than a path or URI saved as a `.wav` file.
2. **Given** a Chatterbox Multilingual V3 `ProviderConfig`, **When** provider composition runs, **Then** a Chatterbox Multilingual V3 implementation is registered behind the existing `TTSProvider` and `ProviderRegistry` contracts without importing the concrete provider from `VoiceoverModule` or `CoreWorkflowEngine`.
3. **Given** the optional Chatterbox runtime, **When** the manual smoke runner is executed with its built-in voice or an optional approved speaker reference, **Then** it creates a playable Polish WAV and a JSON evidence report or exits non-zero with an actionable error.
4. **Given** a long narration, **When** chunked synthesis is interrupted and resumed, **Then** valid matching chunks are reused and only missing, changed or corrupt chunks are regenerated.

### Additional Edge Cases

- Chatterbox optional dependencies are not installed.
- `device=cuda` is requested but CUDA is unavailable.
- A private speaker reference is missing, invalid or accidentally placed under a tracked path.
- A provider returns empty bytes, non-WAV bytes or incompatible WAV parameters.
- One sentence is longer than the preferred chunk limit.
- A resumed chunk was generated from different text or voice settings.
- Two WAV chunks have different sample rates, channel counts, sample widths or compression types.

### Additional Functional Requirements

#### Narration Fixtures

- **FR-055**: The repository MUST contain fixed Polish narration fixtures targeting approximately 1, 5, 8 and 15 minutes.
- **FR-056**: Fixture metadata MUST record filename, title, language, target duration, actual word count, expected word-count range and feature tags.
- **FR-057**: Fixture validation MUST be deterministic, offline and independent of any LLM.

#### Provider-neutral Audio Contract

- **FR-058**: TTS synthesis MUST return an explicit provider-neutral result carrying actual audio bytes, sample rate, duration, audio format, provider name and metadata.
- **FR-059**: `VoiceoverModule` MUST persist actual WAV bytes and MUST reject a string path or URI masquerading as completed audio.
- **FR-060**: The mock TTS provider MUST generate deterministic, readable PCM WAV data using lightweight local code.
- **FR-061**: Existing artifact naming and export compatibility for `voiceover.wav` MUST be preserved.

#### Chatterbox Multilingual V3 Composition

- **FR-062**: Chatterbox Multilingual V3 MUST implement the existing `TTSProvider` abstraction.
- **FR-063**: Concrete TTS providers MUST be created from the existing `ProviderConfig` settings and registered in the existing `ProviderRegistry`; the implementation MUST NOT introduce a second generic provider registry.
- **FR-064**: Chatterbox Multilingual V3 loading MUST be lazy and occur at most once per provider instance.
- **FR-065**: Default tests MUST NOT import the real Chatterbox runtime, download weights, access the network or require a GPU.
- **FR-066**: Heavy Chatterbox/PyTorch dependencies MUST remain optional and MUST use versions confirmed by the manual environment spike.
- **FR-067**: Missing dependencies, unsupported devices, invalid speaker references and invalid audio output MUST produce actionable errors.

#### Long Narration Reliability

- **FR-068**: Technical TTS chunking MUST preserve normalized narration text in order and MUST remain separate from semantic scene segmentation.
- **FR-069**: Chunking MUST prefer paragraph and sentence boundaries and MUST emit stable non-empty chunk records.
- **FR-070**: Per-chunk manifests MUST record text/config identity, status, checksum, duration and WAV parameters.
- **FR-071**: Resume MUST reuse only valid chunks whose text and relevant configuration identity match the current run.
- **FR-072**: WAV assembly MUST validate format compatibility before concatenation and MUST NOT publish a completed final artifact after partial failure.
- **FR-073**: Benchmark evidence MUST include provider, model, device, language, word count, chunk count, generation duration, audio duration, real-time factor, sample rate, checksum and failed chunk identifiers.

### Additional Non-Functional Requirements

- **NFR-012**: The real-TTS extension MUST remain compatible with Python 3.11.
- **NFR-013**: Provider-specific code MUST remain isolated from workflow orchestration and reusable provider-neutral TTS services.
- **NFR-014**: Runtime voice samples, model caches, intermediate chunks, reports and generated audio MUST be excluded from version control.
- **NFR-015**: The first long-narration implementation MUST use deterministic local PCM WAV assembly unless a documented requirement proves it insufficient.

### Additional Success Criteria

- **SC-007**: The deterministic mock path stores a valid readable WAV and the complete existing test suite remains green.
- **SC-008**: A human can run one documented command to generate a one-minute Polish Chatterbox Multilingual V3 WAV using its built-in voice or an optional approved speaker reference.
- **SC-009**: The fifteen-minute fixture completes with a fake backend, supports simulated interruption/resume and produces a final WAV plus synthesis and benchmark manifests.
- **SC-010**: No task in M004 implements semantic scene splitting, image generation, captions, rendering, API redesign or deployment.

<!-- M004 REAL TTS EXTENSION END -->

<!-- M005 TTS RUNTIME HARDENING EXTENSION START -->

## TTS Runtime Integrity

### Additional Functional Requirements

- **FR-074**: The system MUST derive a deterministic, provider-neutral effective synthesis identity that covers the provider, model, execution device, effective language, generation settings and voice identity, without persisting private absolute paths.
- **FR-075**: The system MUST reuse a cached narration chunk only when its normalized text and complete effective synthesis identity match the current synthesis request.
- **FR-076**: Before a narration run begins, the system MUST persist a running lifecycle state that cannot present a prior completed final WAV as the result of the new or interrupted run.
- **FR-077**: The system MUST validate and atomically finalize chunk and final PCM WAV artifacts before recording successful completion evidence.
- **FR-078**: The system MUST remove stale chunk records and safely remove orphaned runtime chunk artifacts that are not part of the current narration, without touching files outside the configured runtime root.
- **FR-079**: The system MUST report benchmark evidence from the effective synthesis identity and distinguish chunks generated, reused and failed in the current run.
- **FR-080**: Smoke validation and workflow voiceover validation MUST apply a compatible PCM WAV inspection contract and report the observed audio parameters.
- **FR-081**: The system MUST prove offline, using a deterministic fake provider and the fifteen-minute narration fixture, that an interrupted run resumes valid chunks and produces one consistent final WAV and artifact set.

### Additional Non-Functional Requirements

- **NFR-016**: Default TTS runtime-integrity tests MUST require no network access, model download, GPU, PyTorch, Chatterbox runtime or private speaker-reference file.

### Additional Success Criteria

- **SC-011**: An interrupted fifteen-minute offline narration run cannot expose stale success, resumes only valid compatible chunks, and publishes a validated final PCM WAV with truthful benchmark evidence.

<!-- M005 TTS RUNTIME HARDENING EXTENSION END -->

<!-- M006 MULTI-PROVIDER POLISH TTS EXTENSION START -->

## Multi-Provider Polish TTS

### Additional Functional Requirements

- **FR-082**: The system MUST select a supported TTS provider through the existing ProviderConfig and TTS factory without provider-specific branches in VoiceoverModule or workflow orchestration.
- **FR-083**: Every TTS provider MUST expose deterministic JSON-compatible capability metadata without importing or initializing its heavy optional runtime.
- **FR-084**: Capability metadata MUST describe supported languages, voice modes, reference-audio requirements, speaking-rate support and usage policy.
- **FR-085**: The Chatterbox Multilingual V3 runtime MUST be reproducibly pinned to an implementation that accepts the configured V3 model variant and MUST emit validated mono 16-bit PCM WAV output.
- **FR-086**: Piper MUST be available as an optional local TTS provider with a curated Polish voice catalog and validated controls for voice selection and speaking rate.
- **FR-087**: Every externally stored voice or model asset MUST have a deterministic identity containing provider, model key, revision or version, content checksum and recorded license identifier without persisting private local paths.
- **FR-088**: XTTS-v2 MUST be exposed only as an evaluation provider, MUST require approved reference audio and MUST be rejected by production-mode configuration validation unless a separate policy decision changes its permitted use.
- **FR-089**: Provider-specific settings MUST be validated strictly and unknown or unsupported settings MUST fail before model loading.
- **FR-090**: Effective synthesis identity and cache invalidation MUST distinguish Chatterbox, Piper and XTTS requests, resolved model or voice assets, language, device, generation controls and reference-audio content.
- **FR-091**: All providers MUST return a WAV payload compatible with the shared PCM inspection and assembly contract, while preserving the provider's truthful sample rate in result and benchmark metadata.
- **FR-092**: A human-operated comparison runner MUST generate the same normalized Polish text across selected provider profiles and write per-profile WAV and JSON evidence plus a summary and playlist.
- **FR-093**: Runtime setup and health checks MUST use explicit isolated interpreter paths and MUST NOT modify the interpreter pinned for hooks, agents or CI.
- **FR-094**: Model downloads, private reference audio and real provider execution MUST occur only in explicit manual setup, smoke or comparison commands and never during default tests.

### Additional Non-Functional Requirements

- **NFR-017**: Default M006 tests MUST require no network, model download, GPU, PyTorch, Chatterbox, Piper, XTTS or private speaker-reference file.
- **NFR-018**: Optional provider runtimes MUST be independently installable so incompatible heavy dependencies cannot destabilize the base application or each other.
- **NFR-019**: Provider and voice metadata MUST preserve source, version or revision, checksum and usage-policy evidence sufficient for human license review.
- **NFR-020**: Reference-audio paths and contents MUST remain local, ignored and absent from manifests, logs and committed fixtures; only non-reversible checksums and approved labels may be persisted.

### Additional Success Criteria

- **SC-012**: The same provider-neutral VoiceoverModule workflow can execute with mock, Chatterbox or Piper by changing ProviderConfig only.
- **SC-013**: A human can run one documented comparison command and receive playable Chatterbox and Piper Polish WAV samples with truthful benchmark evidence; XTTS is included only when an approved reference is supplied.
- **SC-014**: Production-mode configuration deterministically rejects the evaluation-only XTTS provider and explains the policy boundary.
- **SC-015**: Provider changes and voice/model asset changes invalidate incompatible cached narration chunks while unchanged compatible runs remain reusable.
- **SC-016**: The complete existing test suite remains green without installing or importing any real M006 provider runtime.

<!-- M006 MULTI-PROVIDER POLISH TTS EXTENSION END -->

<!-- M007 ENGLISH-FIRST YOUTUBE PRODUCTION EXTENSION START -->

## English-First YouTube Production

### User Story 6 - Produce an English source and hand it off for YouTube localization (Priority: P1)

A producer wants the application to generate high-quality English source content, narration, captions and export artifacts, then hand the approved bundle to YouTube-oriented publishing and localization without changing the source workflow language.

**Independent Test**: An offline long-form workflow configured with `language=en` and deterministic providers produces resumable narration plus a YouTube-ready export containing English captions, metadata and localization-handoff state; changing localization targets does not change source artifacts or introduce concrete provider/platform branches into orchestration.

**Acceptance Scenarios**:

1. **Given** an English workflow and a Polish localization target, **When** configuration is validated, **Then** `WorkflowConfig.language` remains `en` and the Polish target is represented only in export/publishing localization configuration.
2. **Given** a long English narration interrupted after completed chunks, **When** it resumes with the same effective synthesis identity, **Then** valid chunks are reused and the final narration artifacts remain provider-neutral and complete.
3. **Given** approved video, narration and caption artifacts, **When** export runs for YouTube handoff, **Then** it produces deterministic metadata and checksummed references without making a publishing request.
4. **Given** a configured publishing provider, **When** the offline publishing boundary is exercised, **Then** provider selection occurs through `ProviderConfig` and `ProviderRegistry`, and mock evidence is produced without network or credentials.
5. **Given** platform auto-dubbing is preferred, **When** handoff state is recorded, **Then** the system requires human acceptance and can record custom localized-audio fallback metadata without claiming that an unsupported auto-dubbing API was called.

### Additional Functional Requirements

- **FR-095**: `WorkflowConfig.language` MUST identify the language generated by content modules and synthesized by the source narration path.
- **FR-096**: The M007 production baseline MUST use English source content and English narration while allowing other source languages to remain valid where already supported.
- **FR-097**: Localization targets and localization method MUST be validated within export/publishing configuration and MUST NOT overload or mutate `WorkflowConfig.language`.
- **FR-098**: English TTS selection MUST continue through the existing ProviderConfig, TTS settings, factory and provider registry without concrete provider branches in VoiceoverModule or CoreWorkflowEngine.
- **FR-099**: The repository MUST provide a reproducible human-operated English Chatterbox production profile and smoke path that records effective model, voice, device, language and WAV evidence.
- **FR-100**: Long-form English narration MUST preserve deterministic technical chunking, compatible cache reuse, resumable synthesis, atomic WAV finalization and truthful artifact/benchmark identity.
- **FR-101**: A YouTube-ready export MUST represent available video, source narration, captions, upload metadata, source-language identity and checksummed artifact references without fabricating missing optional artifacts.
- **FR-102**: English caption export MUST produce deterministic UTF-8 subtitle content with stable ordering and valid non-overlapping timestamps suitable for manual YouTube upload.
- **FR-103**: Publishing MUST remain separate from generation and MUST be composed through PublishingProvider and the existing ProviderRegistry rather than platform branches in workflow orchestration.
- **FR-104**: Real publishing dependencies, credentials and requests MUST remain optional and MUST NOT be required or exercised by default tests.
- **FR-105**: Localization handoff MUST record the preferred platform-auto-dubbing path, target languages, manual acceptance state and optional custom localized-audio fallback metadata without claiming an unsupported platform API.
- **FR-106**: Rejecting or requesting changes to publishing or localization handoff MUST preserve existing source/export artifacts and record the decision.
- **FR-107**: Experimental TTS models under `experiments/tts_local`, including MOSS-TTS, MUST remain outside the production provider registry unless a separate future decision and milestone explicitly authorize integration.

### Additional Non-Functional Requirements

- **NFR-021**: Default M007 tests MUST require no network access, platform credentials, model download, GPU, real TTS runtime or real publishing request.
- **NFR-022**: Export, subtitle and localization-handoff artifacts MUST be deterministic for identical source inputs and configuration.
- **NFR-023**: YouTube-specific behavior MUST remain isolated behind export/publishing contracts so another platform can be added without changing CoreWorkflowEngine or source narration.
- **NFR-024**: Secrets, private custom-dub audio paths and provider tokens MUST NOT appear in committed fixtures, logs, manifests or generated planning artifacts.

### Additional Success Criteria

- **SC-017**: An offline English long-form workflow produces narration, deterministic captions and a YouTube-ready export while preserving `language=en` when Polish localization is requested.
- **SC-018**: Interrupted English narration resumes only compatible chunks and produces the same provider-neutral artifact contract as an uninterrupted run.
- **SC-019**: Mock publishing and localization handoff complete deterministically without network access, while real publishing remains an explicit optional runtime path.
- **SC-020**: Static and behavioral tests prove that CoreWorkflowEngine and VoiceoverModule contain no concrete Chatterbox or YouTube selection branches.

<!-- M007 ENGLISH-FIRST YOUTUBE PRODUCTION EXTENSION END -->

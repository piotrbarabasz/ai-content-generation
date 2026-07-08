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

# Tasks: AI Content Studio MVP

## Phase 1: Repository foundation

- [X] T001 Repository scaffold and documentation index
Milestone: M001
Epic: E001
Risk: medium
Implementation files: `README.md`, `backend/`, `backend/app/`, `backend/tests/`, `docs/INDEX.md`
Test files: `none` (documentation or configuration validation is covered by the repository checks)
Validation commands: `git diff --check`
Final PR review required: yes
Goal: Create the top-level project skeleton and a README that explains the MVP scope, repo layout, and where to find the design sources.
Dependencies: None
Acceptance criteria: The repository has a clear backend-first layout and the README links to all spec-kit docs and source-repo-insights docs needed for implementation.
Test requirements: None.
Parallelizable: no
Notes: Include references to `docs/spec-kit/00-product-context.md`, `docs/spec-kit/01-source-repo-synthesis.md`, `docs/spec-kit/02-domain-model-draft.md`, `docs/spec-kit/03-module-contracts-draft.md`, `docs/spec-kit/04-workflow-presets-draft.md`, `docs/spec-kit/05-mvp-boundary.md`, `docs/source-repo-insights/shorts/repo-modular-pipeline-insights.md`, `docs/source-repo-insights/shorts/repo-product-insights.md`, `docs/source-repo-insights/long-form/repo-modular-pipeline-insights.md`, and `docs/source-repo-insights/long-form/repo-product-insights.md`.

- [X] T002 Add Python project tooling and test conventions
Milestone: M001
Epic: E001
Risk: medium
Implementation files: `pyproject.toml`, `pytest.ini`, `.gitignore`, `backend/requirements.txt`
Test files: `none` (documentation or configuration validation is covered by the repository checks)
Validation commands: `git diff --check`
Final PR review required: yes
Goal: Define the Python 3.11 development baseline, dependency management, linting, formatting, and pytest configuration.
Dependencies: T001
Acceptance criteria: Project tooling is declared in one place and the repo has explicit conventions for running tests and formatting code.
Test requirements: None.
Parallelizable: yes
Notes: Keep the setup minimal and compatible with backend-first development.

- [X] T003 Create package and test skeletons
Milestone: M001
Epic: E001
Risk: medium
Implementation files: `backend/app/__init__.py`, `backend/app/api/__init__.py`, `backend/app/domain/__init__.py`, `backend/app/modules/__init__.py`, `backend/app/providers/__init__.py`, `backend/app/storage/__init__.py`, `backend/app/workflow/__init__.py`, `backend/tests/conftest.py`
Test files: `none` (documentation or configuration validation is covered by the repository checks)
Validation commands: `git diff --check`
Final PR review required: yes
Goal: Add importable package markers and shared test scaffolding so future modules can be implemented without path hacks.
Dependencies: T001
Acceptance criteria: The backend package imports cleanly and pytest can discover the test package without custom path manipulation.
Test requirements: None.
Parallelizable: yes
Notes: Keep the package layout aligned with the implementation plan in `specs/001-ai-content-studio/plan.md`.

## Phase 2: Domain models

- [X] T004 Implement shared domain primitives
Milestone: M001
Epic: E001
Risk: medium
Implementation files: `backend/app/domain/base.py`, `backend/app/domain/enums.py`, `backend/app/domain/types.py`
Test files: none
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Define the common enums, IDs, timestamps, statuses, and base model helpers used by the feature domain.
Dependencies: T003
Acceptance criteria: Shared domain primitives exist and can be reused by all entity models without duplicate status definitions.
Test requirements: Original completion evidence covered the implementation and repository validation; direct behavioral coverage is explicitly provided by remediation task T045.
Parallelizable: yes
Notes: Keep these primitives independent of the workflow engine and storage layer. Original completion evidence was the implemented domain primitive files, repository validation, and the passing task review; dedicated direct tests are added separately in T045.

- [X] T005 Implement project and configuration domain models
Milestone: M001
Epic: E001
Risk: medium
Implementation files: `backend/app/domain/project.py`, `backend/app/domain/content_brief.py`, `backend/app/domain/workflow_config.py`, `backend/app/domain/provider_config.py`
Test files: none
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Create the core project-level models for project setup and workflow configuration.
Dependencies: T004
Acceptance criteria: `Project`, `ContentBrief`, `WorkflowConfig`, and `ProviderConfig` validate the required fields from the spec and data model.
Test requirements: Original completion evidence covered the implementation and repository validation; direct behavioral coverage is explicitly provided by remediation task T046.
Parallelizable: no
Notes: Keep workflow config generic enough to support enabled and disabled modules plus provider selection. Original completion evidence was the implemented project/configuration model files, repository validation, and the passing task review; dedicated direct tests are added separately in T046.

- [ ] T006 Implement run, artifact, and output domain models
Milestone: M001
Epic: E001
Risk: medium
Implementation files: `backend/app/domain/workflow_run.py`, `backend/app/domain/generation_job.py`, `backend/app/domain/artifact.py`, `backend/app/domain/script.py`, `backend/app/domain/narrative_segment.py`, `backend/app/domain/render_scene.py`, `backend/app/domain/voiceover.py`, `backend/app/domain/caption_track.py`, `backend/app/domain/video_render.py`, `backend/app/domain/export_bundle.py`
Test files: `backend/tests/unit/test_t006.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Create the execution and output models used by the workflow engine and export path.
Dependencies: T004, T005, T045, T046
Acceptance criteria: The run and output models capture workflow status, artifact references, and the distinct narrative/render concepts required by the MVP.
Test requirements: Add direct model construction, validation, and serialization tests in this task, including artifact references and the NarrativeSegment versus RenderScene distinction.
Parallelizable: no
Notes: Add artifact reference fields to `WorkflowRun` and `GenerationJob` here so later storage work does not need a second data model refactor.

## Phase 3: Core workflow engine

- [ ] T007 Define module execution contracts and registry types
Milestone: M001
Epic: E002
Risk: high
Implementation files: `backend/app/workflow/module.py`, `backend/app/workflow/registry.py`, `backend/app/workflow/execution.py`
Test files: `backend/tests/unit/test_t007.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Add the module interface, registry, execution context, module result, and execution plan types used by the engine.
Dependencies: T004, T005, T006
Acceptance criteria: The workflow layer can describe a module, register it, validate its dependencies, and represent an execution plan without concrete module logic.
Test requirements: Add direct registry and execution-plan tests in this task.
Parallelizable: no
Notes: Keep the contract explicit about enabled and disabled execution behavior.

- [ ] T008 Implement the core workflow engine
Milestone: M001
Epic: E002
Risk: high
Implementation files: `backend/app/workflow/engine.py`
Test files: `backend/tests/unit/test_t008.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Build the engine that executes modules in order, skips disabled modules, validates dependencies, and surfaces basic failure behavior.
Dependencies: T007
Acceptance criteria: The engine can run a plan, respect enabled and disabled modules, stop on missing required dependencies, and record failed execution states.
Test requirements: Add direct engine order and missing-dependency tests in this task.
Parallelizable: no
Notes: Keep the engine provider-agnostic and free of filesystem assumptions.

## Phase 4: Artifact storage

- [ ] T009 Implement artifact storage abstraction and local store
Milestone: M001
Epic: E003
Risk: high
Implementation files: `backend/app/storage/artifact_store.py`, `backend/app/storage/local_store.py`, `backend/app/storage/manifest.py`, `backend/app/domain/workflow_run.py`, `backend/app/domain/generation_job.py`
Test files: `backend/tests/unit/test_t009.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Create the artifact persistence interface, a local store implementation, and the artifact manifest format.
Dependencies: T006
Acceptance criteria: Artifacts can be saved, read, and listed through an interface, and stored runs/jobs can reference artifact keys instead of raw filesystem paths.
Test requirements: Add direct artifact-store tests in this task.
Parallelizable: no
Notes: The local implementation should honor configured storage roots and avoid hardcoded absolute paths.

## Phase 5: Provider abstraction

- [ ] T010 Define provider interfaces and mock implementations
Milestone: M001
Epic: E003
Risk: high
Implementation files: `backend/app/providers/interfaces.py`, `backend/app/providers/mock_llm.py`, `backend/app/providers/mock_tts.py`, `backend/app/providers/mock_captions.py`, `backend/app/providers/mock_transcription.py`, `backend/app/providers/mock_assets.py`, `backend/app/providers/mock_video_renderer.py`, `backend/app/providers/mock_storage.py`
Test files: `backend/tests/unit/test_t010.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Create provider interfaces for LLM, TTS, captions, transcription, assets, video rendering, and storage, then implement deterministic mocks.
Dependencies: T004, T009
Acceptance criteria: All provider categories are available behind interfaces and the mock versions produce deterministic outputs suitable for tests.
Test requirements: Add direct deterministic mock-provider tests in this task.
Parallelizable: no
Notes: Keep real vendor integrations out of this slice.

## Phase 6: MVP modules

- [ ] T011 Implement BriefModule
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/modules/brief.py`
Test files: `backend/tests/unit/test_t011.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Add the first intake module with deterministic behavior.
Dependencies: T007, T008, T010
Acceptance criteria: The module can transform a topic, brief or transcript into a normalized ContentBrief artifact without external integrations.
Test requirements: Add direct module-behavior tests in this task.
Parallelizable: no
Notes: Keep the module APIs narrow so each module can be tested independently.

- [ ] T012 Implement VoiceoverModule
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/modules/voiceover.py`
Test files: `backend/tests/unit/test_t012.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Add optional voiceover generation using deterministic mock TTS output.
Dependencies: T007, T008, T009, T010, T011
Acceptance criteria: The module can produce a voiceover artifact reference when enabled and can be skipped when disabled without blocking exports that allow missing voiceover.
Test requirements: Add direct output-artifact and disabled-module tests in this task.
Parallelizable: no
Notes: Keep module output formats stable so the export bundle can assemble references without special-case logic.

## Phase 7: Workflow presets

- [ ] T013 Define MVP workflow presets
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/workflow/presets.py`, `backend/app/workflow/registry.py`
Test files: `backend/tests/unit/test_t013.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Create the Short Video preset and Long-form Script + Voiceover preset with explicit module lists and default configuration.
Dependencies: T005, T006, T008, T011, T012
Acceptance criteria: Each preset declares content type, genre defaults, duration defaults, required modules, optional modules, default provider config, and expected artifacts.
Test requirements: Add direct tests for preset declarations, defaults, module lists, provider configuration, and expected artifacts in this task. Cross-preset registration and API smoke coverage remains in T019.
Parallelizable: no
Notes: Keep preset definitions declarative so they can be reused by API and tests. Cross-preset registration and API smoke coverage is handled in T019.

## Phase 8: API layer

- [ ] T014 Create the API application and shared schemas
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/api/main.py`, `backend/app/api/schemas.py`, `backend/app/api/dependencies.py`
Test files: `backend/tests/unit/test_t014.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Add the FastAPI application entrypoint and request/response schemas for projects, workflow configs, workflow runs, artifacts, and export bundles.
Dependencies: T003, T005, T006, T009, T010
Acceptance criteria: The API layer has importable shared schemas and an application object that can be started without extra glue code.
Test requirements: Add direct schema validation and application-construction tests in this task. End-to-end API smoke coverage remains in T019.
Parallelizable: no
Notes: Keep API schemas aligned with the domain models rather than duplicating fields unnecessarily. End-to-end API smoke coverage is handled in T019.

- [ ] T015 Implement minimal API endpoints
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/api/routes/projects.py`, `backend/app/api/routes/workflow_configs.py`, `backend/app/api/routes/workflow_runs.py`, `backend/app/api/routes/artifacts.py`
Test files: `backend/tests/unit/test_t015.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Expose create project, get project, create workflow config, start workflow run, get workflow run status, list artifacts, and export bundle endpoints.
Dependencies: T014, T013
Acceptance criteria: The API can create and retrieve project data, start a workflow run, inspect run status, list artifacts, and request an export bundle.
Test requirements: Add direct route behavior tests for request validation, project/configuration/run handlers, and artifact/export responses in this task. Full API smoke coverage remains in T019.
Parallelizable: no
Notes: Avoid making the CLI the only usable interface. Full API smoke coverage is handled in T019.

## Phase 9: Approval basics

- [ ] T016 Add approval checkpoint domain model and state machine
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/domain/approval.py`
Test files: `backend/tests/unit/test_t016.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Implement approval checkpoints, approval decisions and allowed state transitions.
Dependencies: T006, T008, T015
Acceptance criteria: Approval checkpoints support not_required, pending, approved, rejected, changes_requested and skipped states; rejection preserves artifacts and records an approval decision.
Test requirements: Add direct approval model and state-transition tests in this task. Cross-workflow pause/resume and API approval coverage remains in T041.
Parallelizable: no
Notes: Keep approval simplified for MVP, but model it explicitly in the workflow state. Cross-workflow pause/resume and API approval coverage is handled in T041.

## Phase 10: Tests

- [ ] T017 Add tests for module registry and execution order
Milestone: M001
Epic: E006
Risk: high
Implementation files: `none`
Test files: `backend/tests/unit/test_module_registry.py`, `backend/tests/unit/test_workflow_engine.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Verify module registration, execution order, enabled and disabled module behavior, and missing dependency handling.
Dependencies: T007, T008
Acceptance criteria: The tests demonstrate that the registry and engine enforce order, skip disabled modules, and fail cleanly on missing dependencies.
Test requirements: These tests should be deterministic and run without network or filesystem dependencies.
Parallelizable: yes
Notes: Focus on the engine's contract rather than implementation details.

- [ ] T018 Add tests for artifact storage and mock providers
Milestone: M001
Epic: E006
Risk: high
Implementation files: `none`
Test files: `backend/tests/unit/test_artifact_store.py`, `backend/tests/unit/test_mock_providers.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Verify local artifact persistence, artifact manifests and mock providers.
Dependencies: T009, T010, T012
Acceptance criteria: The tests prove that artifacts are stored and retrieved through the abstraction and mocks are deterministic.
Test requirements: The tests should avoid real provider calls and use only local fixtures.
Parallelizable: yes
Notes: Failed module handling is covered by T044.

- [ ] T019 Add tests for preset registration and API smoke paths
Milestone: M001
Epic: E006
Risk: high
Implementation files: `none`
Test files: `backend/tests/unit/test_presets.py`, `backend/tests/integration/test_api_smoke.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Verify the Short Video preset, Long-form preset registration, and a minimal API happy path.
Dependencies: T013, T014, T015, T016
Acceptance criteria: The tests prove that both presets resolve correctly and the API can drive a basic project, workflow config and workflow run lifecycle.
Test requirements: Keep the integration test thin and deterministic by using mock providers and local storage; export bundle content tests are covered by T042.
Parallelizable: yes
Notes: The API smoke test should verify status transitions rather than implementation internals.

## Phase 11: Migration documentation

- [ ] T020 Document migration from source repos to the new architecture
Milestone: M001
Epic: E004
Risk: high
Implementation files: `docs/migration/shorts-repo-migration-plan.md`, `docs/migration/long-form-repo-migration-plan.md`
Test files: `none`
Validation commands: `git diff --check`
Final PR review required: yes
Goal: Create migration plans that map the shorts and long-form repositories into the unified AI Content Studio architecture.
Dependencies: T011, T012, T013
Acceptance criteria: The documents explain how legacy repo components map to new modules, providers, workflow presets, and artifact storage.
Test requirements: None.
Parallelizable: yes
Notes: Call out what is reused, what is refactored, and what stays out of scope for MVP.

## Phase 12: Remediation - workflow config, providers and security

- [X] T021 Implement canonical WorkflowConfig schema and enum validation
Milestone: M001
Epic: E003
Risk: high
Implementation files: `backend/app/domain/workflow_config.py`, `backend/app/domain/enums.py`
Test files: `backend/tests/unit/test_t021.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Freeze the WorkflowConfig schema and reject invalid enum values, enabled/disabled module conflicts and invalid preset/content-type combinations.
Dependencies: T005
Acceptance criteria: Valid short_video and long_form_script_voiceover configs pass; invalid enum values fail; any module in both enabledModules and disabledModules fails; provider validation runs after config validation.
Test requirements: Add tests for valid short_video config, valid long_form_script_voiceover config, invalid enum, module conflict and validation ordering.
Parallelizable: no
Notes: Canonical workflowPreset values are short_video and long_form_script_voiceover.

- [ ] T022 Implement ProviderRegistry and mock provider registration
Milestone: M001
Epic: E003
Risk: high
Implementation files: `backend/app/providers/registry.py`, `backend/app/providers/interfaces.py`, `backend/app/providers/mocks.py`
Test files: `backend/tests/unit/test_t022.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Register provider implementations by provider type and provider name, then register deterministic mock providers for MVP.
Dependencies: T010, T021
Acceptance criteria: Providers can be registered, resolved by type/name and exposed to module execution context.
Test requirements: Add ProviderRegistry registration and resolution tests.
Parallelizable: no
Notes: Provider types are LLMProvider, TTSProvider, TranscriptionProvider, CaptionProvider, AssetProvider, VideoRendererProvider, StorageProvider and PublishingProvider.

- [ ] T023 Implement ProviderConfig validation before workflow execution
Milestone: M001
Epic: E003
Risk: high
Implementation files: `backend/app/providers/validation.py`, `backend/app/workflow/engine.py`, `backend/app/domain/workflow_config.py`
Test files: `backend/tests/unit/test_t023.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Validate provider availability for enabled modules before a WorkflowRun starts.
Dependencies: T008, T022
Acceptance criteria: Missing provider, invalid provider type and unknown provider name fail before run start; disabled optional modules do not require their providers; valid mock config passes.
Test requirements: Add tests for missing provider, invalid provider type, disabled module not requiring provider and valid mock provider config.
Parallelizable: no
Notes: Fail fast before any module writes artifacts.

- [X] T024 Add security and secret hygiene foundation
Milestone: M001
Epic: E003
Risk: high
Implementation files: `.gitignore`, `.env.example`, `README.md`
Test files: `backend/tests/static/test_secret_hygiene.py`
Validation commands: `git diff --check`
Final PR review required: yes
Goal: Make provider secret handling, runtime artifacts and sample env files explicit and safe.
Dependencies: T001, T002
Acceptance criteria: `.env` and `.env.*` are ignored except placeholder sample files; secrets, credentials, tokens and runtime artifacts are ignored; provider secrets are read from environment/config only; sample env contains placeholders only.
Test requirements: Add a static check that committed config contains no real-looking API keys and sample env values are placeholders.
Parallelizable: yes
Notes: Do not commit real credentials or runtime artifacts.

## Phase 13: Remediation - short video modules and export

- [ ] T025 Implement ScriptGenerationModule
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/modules/script_generation.py`
Test files: `backend/tests/unit/test_t025.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Generate deterministic script and NarrativeSegment artifacts from brief, outline or research context.
Dependencies: T011, T022
Acceptance criteria: The module creates script.txt, optional script.json and narrative_segments.json artifacts using mock providers or deterministic rules.
Test requirements: Add script output and narrative segment tests.
Parallelizable: no
Notes: Keep NarrativeSegment separate from RenderScene.

- [ ] T026 Implement ScenePlanningModule
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/modules/scene_planning.py`
Test files: `backend/tests/unit/test_t026.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Generate RenderScene artifacts for short video workflows.
Dependencies: T011, T025
Acceptance criteria: The module creates render_scenes.json and scene_plan.json and can pause at scene plan approval before rendering.
Test requirements: Add scene planning and NarrativeSegment versus RenderScene separation tests.
Parallelizable: no
Notes: Required for short_video preset.

- [ ] T027 Implement CaptionsModule
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/modules/captions.py`
Test files: `backend/tests/unit/test_t027.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Generate optional captions using deterministic caption provider output.
Dependencies: T010, T012, T026
Acceptance criteria: The module creates captions.srt or captions.json when enabled and is skipped cleanly when disabled.
Test requirements: Add enabled and disabled caption module tests.
Parallelizable: no
Notes: Disabled captions must not require CaptionProvider validation.

- [ ] T028 Implement VideoRenderingModule
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/modules/video_rendering.py`
Test files: `backend/tests/unit/test_t028.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Generate deterministic video render metadata and artifact references for video workflows.
Dependencies: T010, T026, T027
Acceptance criteria: The module creates a video render artifact reference for short_video and is disabled by default for long_form_script_voiceover.
Test requirements: Add render-required and disabled-render tests.
Parallelizable: no
Notes: Do not implement real video rendering in the first slice.

- [ ] T029 Define ExportBundle manifest schema
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/domain/export_bundle.py`, `backend/app/modules/export_manifest.py`
Test files: `backend/tests/unit/test_t029.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Define the manifest contract for required files, conditional artifacts and summary sections.
Dependencies: T006, T009, T021
Acceptance criteria: Manifest schema includes schemaVersion, exportId, projectId, workflowRunId, workflowPreset, contentType, contentGenre, durationProfile, createdAt, includedArtifacts, missingOptionalArtifacts, moduleResults, approvalSummary, providerSummary and artifactReferences.
Test requirements: Add export manifest schema tests.
Parallelizable: no
Notes: Required files are manifest.json, workflow_config.json and workflow_run.json.

- [ ] T030 Implement ExportModule against the manifest contract
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/modules/export.py`
Test files: `backend/tests/unit/test_t030.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Package required workflow files and conditional artifact files or references into an export bundle.
Dependencies: T009, T029
Acceptance criteria: Export includes required files; includes script, narrative segments, render scenes, captions, voiceover, video, QA, research and dossier artifacts when present; records missing optional artifacts.
Test requirements: Add short-video export and long-form export tests.
Parallelizable: no
Notes: Export must work without publishing automation.

## Phase 14: Remediation - long-form workflow

- [ ] T031 Implement ResearchModule
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/modules/research.py`
Test files: `backend/tests/unit/test_t031.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Produce deterministic research artifacts from topic or source inputs when research is enabled.
Dependencies: T009, T022, T023
Acceptance criteria: The module creates research.json linked to WorkflowRun and GenerationJob when enabled and is skipped cleanly when disabled.
Test requirements: Add enabled and disabled research tests.
Parallelizable: no
Notes: Do not implement real web fetching or external research integrations.

- [ ] T032 Implement DossierModule
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/modules/dossier.py`
Test files: `backend/tests/unit/test_t032.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Produce a structured dossier artifact from research output when enabled.
Dependencies: T031
Acceptance criteria: The module creates dossier.json when research/dossier inputs exist and can be skipped when disabled.
Test requirements: Add dossier artifact tests.
Parallelizable: no
Notes: Long-form workflows can still continue from topic with research disabled.

- [ ] T033 Implement OutlineModule
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/modules/outline.py`
Test files: `backend/tests/unit/test_t033.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Produce a long-form outline artifact from topic, brief, research or dossier context.
Dependencies: T011, T031, T032
Acceptance criteria: The module creates outline.json for the long_form_script_voiceover preset.
Test requirements: Add outline artifact tests.
Parallelizable: no
Notes: Outline is required for the long-form MVP path.

- [ ] T034 Implement PostProcessingModule
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/modules/post_processing.py`
Test files: `backend/tests/unit/test_t034.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Normalize generated script text for downstream QA, optional voiceover and export.
Dependencies: T025, T033
Acceptance criteria: The module creates post_processed_script.txt and preserves the original script artifact.
Test requirements: Add post-processing artifact tests.
Parallelizable: no
Notes: Keep this deterministic for MVP.

- [ ] T035 Implement QAModule
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/modules/qa.py`
Test files: `backend/tests/unit/test_t035.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Produce a deterministic QA report for long-form script output.
Dependencies: T034
Acceptance criteria: The module creates qa_report.json and can participate in script approval workflow.
Test requirements: Add QA report tests.
Parallelizable: no
Notes: QA is required for long_form_script_voiceover.

- [ ] T036 Define LongFormWorkflowPreset
Milestone: M001
Epic: E004
Risk: high
Implementation files: `backend/app/workflow/presets.py`
Test files: `backend/tests/unit/test_t036.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Define the canonical long_form_script_voiceover preset with videoRendering disabled by default.
Dependencies: T013, T031, T032, T033, T034, T035
Acceptance criteria: Preset path is sources or topic -> research -> dossier -> outline -> script -> post-processing -> QA -> optional voiceover -> export; expected artifacts are enumerated.
Test requirements: Add preset validation tests.
Parallelizable: no
Notes: Long-form MVP does not need video rendering.

## Phase 15: Remediation - approval workflow and API

- [ ] T037 Integrate approval checkpoints into workflow execution
Milestone: M001
Epic: E005
Risk: high
Implementation files: `backend/app/workflow/engine.py`, `backend/app/workflow/execution.py`
Test files: `backend/tests/unit/test_t037.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Pause and resume workflows at script, scene plan and final export checkpoints.
Dependencies: T016, T026, T030, T035
Acceptance criteria: Pending checkpoints pause before downstream modules; approved checkpoints continue; rejected and changes_requested checkpoints keep workflow paused; resume requires approved or policy-skipped checkpoints.
Test requirements: Add workflow approval pause/resume tests.
Parallelizable: no
Notes: Rejection preserves artifacts and records a decision.

- [ ] T038 Add approval and resume API routes
Milestone: M001
Epic: E005
Risk: high
Implementation files: `backend/app/api/routes/approvals.py`, `backend/app/api/routes/workflow_runs.py`, `backend/app/api/main.py`
Test files: `backend/tests/unit/test_t038.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Expose approval inspection and decision endpoints.
Dependencies: T015, T016, T037
Acceptance criteria: API supports GET /workflow-runs/{runId}/approvals, POST approve, POST reject, POST request-changes and POST /workflow-runs/{runId}/resume.
Test requirements: Add API tests for approve, reject, request changes and blocked resume.
Parallelizable: no
Notes: Route naming may use the repository's established API prefix.

- [ ] T047 Synchronize API schema with WorkflowConfig
Milestone: M001
Epic: E005
Risk: medium
Implementation files: `backend/app/api/schemas.py`
Test files: `backend/tests/unit/test_t047_api_schema_sync.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Keep the API schema aligned with the canonical WorkflowConfig after the domain-only remediation.
Dependencies: T014
Acceptance criteria: API schema reflects the canonical WorkflowConfig fields and enum constraints without reintroducing cross-epic dependency cycles.
Test requirements: Add direct API schema synchronization tests.
Parallelizable: yes
Notes: This remediation task isolates API schema synchronization from the completed domain task T021.

## Phase 16: Remediation - usage tracking and expanded tests

- [ ] T039 Add UsageTracker and NoopCostTracker
Milestone: M001
Epic: E006
Risk: medium
Implementation files: `backend/app/workflow/usage.py`, `backend/app/workflow/execution.py`
Test files: `backend/tests/unit/test_t039.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Add minimal cost/usage infrastructure without billing or analytics.
Dependencies: T007, T008
Acceptance criteria: ModuleResult can include optional usage metadata and workflow execution succeeds when usage metadata is absent.
Test requirements: Add usage metadata absent test.
Parallelizable: yes
Notes: Do not implement billing dashboard or advanced analytics.

- [ ] T040 Add provider and workflow config validation tests
Milestone: M001
Epic: E006
Risk: medium
Implementation files: `none`
Test files: `backend/tests/unit/test_provider_registry.py`, `backend/tests/unit/test_provider_validation.py`, `backend/tests/unit/test_workflow_config_validation.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Cover ProviderRegistry, provider validation and canonical WorkflowConfig validation.
Dependencies: T021, T022, T023
Acceptance criteria: Tests cover registration and resolution, missing provider, invalid provider type, disabled optional modules not requiring providers, valid mock provider config, valid presets and invalid enum rejection.
Test requirements: These are the test cases for this task.
Parallelizable: yes
Notes: Keep tests deterministic.

- [ ] T041 Add approval workflow tests
Milestone: M001
Epic: E006
Risk: medium
Implementation files: `none`
Test files: `backend/tests/unit/test_approval_workflow.py`, `backend/tests/integration/test_approval_api.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Cover script approval, approval decisions and resume behavior.
Dependencies: T016, T037, T038
Acceptance criteria: Tests cover pause at script approval, approve resumes workflow, reject keeps workflow paused, request changes records decision, resume is blocked without approval and final export approval is required when configured.
Test requirements: These are the test cases for this task.
Parallelizable: yes
Notes: Include artifact preservation assertions for rejection.

- [ ] T042 Add export bundle content tests
Milestone: M001
Epic: E006
Risk: medium
Implementation files: `none`
Test files: `backend/tests/unit/test_export_manifest.py`, `backend/tests/integration/test_export_bundle.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Verify required export files and conditional artifact references.
Dependencies: T029, T030
Acceptance criteria: Tests assert required files, conditional artifacts, missing optional artifacts, short-video export and long-form export contents.
Test requirements: These are the test cases for this task.
Parallelizable: yes
Notes: Do not require real media files.

- [ ] T043 Add long-form workflow execution tests
Milestone: M001
Epic: E006
Risk: medium
Implementation files: `none`
Test files: `backend/tests/integration/test_long_form_workflow.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Verify the long-form preset with enabled and disabled optional modules.
Dependencies: T031, T032, T033, T034, T035, T036
Acceptance criteria: Tests cover topic-based long-form execution, research enabled, research disabled, voiceover disabled and export completion without voiceover.
Test requirements: These are the test cases for this task.
Parallelizable: yes
Notes: Use mock providers and local artifact storage only.

- [ ] T044 Add retry, failed module and static secret hygiene tests
Milestone: M001
Epic: E006
Risk: medium
Implementation files: `none`
Test files: `backend/tests/unit/test_retry_behavior.py`, `backend/tests/unit/test_failed_module_handling.py`, `backend/tests/static/test_secret_hygiene.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Cover retry behavior, failed module behavior and committed config hygiene.
Dependencies: T008, T024, T039
Acceptance criteria: Tests cover transient retry, required module failure, optional module skip, no real-looking API keys in committed config and placeholder-only sample env values.
Test requirements: These are the test cases for this task.
Parallelizable: yes
Notes: Static secret checks should be narrow to avoid false positives.

## Phase 17: Remediation - direct domain tests

- [ ] T045 Add direct tests for shared domain primitives
Milestone: M001
Epic: E001
Risk: medium
Implementation files: none
Test files: `backend/tests/unit/test_t045_domain_primitives.py`
Validation commands: `python -m pytest backend/tests/unit/test_t045_domain_primitives.py`; `git diff --check`
Final PR review required: yes
Goal: Add direct behavioral coverage for shared domain primitives without changing their implementation.
Dependencies: T004
Acceptance criteria: Tests cover real enum behavior, type behavior, base model validation, and serialization rather than import-only checks.
Test requirements: These are the direct behavioral test cases for this task.
Parallelizable: yes
Notes: This remediation task supplies the direct evidence that was not part of the original T004 completion package.

- [ ] T046 Add direct tests for project and configuration domain models
Milestone: M001
Epic: E001
Risk: medium
Implementation files: none
Test files: `backend/tests/unit/test_t046_project_config_models.py`
Validation commands: `python -m pytest backend/tests/unit/test_t046_project_config_models.py`; `git diff --check`
Final PR review required: yes
Goal: Add direct behavioral coverage for project and configuration domain models without changing their implementation.
Dependencies: T005, T045
Acceptance criteria: Tests cover valid models, missing required fields, invalid values, serialization, configuration validation, and the absence of duplicated status definitions.
Test requirements: These are the direct behavioral test cases for this task.
Parallelizable: yes
Notes: This remediation task supplies the direct evidence that was not part of the original T005 completion package.

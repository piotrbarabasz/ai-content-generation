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

- [X] T006 Implement run, artifact, and output domain models
Milestone: M001
Epic: E001
Risk: medium
Implementation files: `backend/app/domain/workflow_run.py`, `backend/app/domain/generation_job.py`, `backend/app/domain/artifact.py`, `backend/app/domain/script.py`, `backend/app/domain/narrative_segment.py`, `backend/app/domain/render_scene.py`, `backend/app/domain/voiceover.py`, `backend/app/domain/caption_track.py`, `backend/app/domain/video_render.py`, `backend/app/domain/export_bundle.py`
Test files: `backend/tests/unit/test_t006.py`
Validation commands: `python -m pytest backend/tests/unit/test_t006.py`
Final PR review required: yes
Goal: Create the execution and output models used by the workflow engine and export path.
Dependencies: T004, T005, T045, T046
Acceptance criteria: The run and output models capture workflow status, artifact references, and the distinct narrative/render concepts required by the MVP.
Test requirements: Add direct model construction, validation, and serialization tests in this task, including artifact references and the NarrativeSegment versus RenderScene distinction.
Parallelizable: no
Notes: Add artifact reference fields to `WorkflowRun` and `GenerationJob` here so later storage work does not need a second data model refactor.

## Phase 3: Core workflow engine

- [X] T007 Define module execution contracts and registry types
Milestone: M001
Epic: E002
Risk: high
Implementation files: `backend/app/workflow/module.py`, `backend/app/workflow/registry.py`, `backend/app/workflow/execution.py`
Test files: `backend/tests/unit/test_t007.py`
Validation commands: `python -m pytest backend/tests/unit/test_t007.py`
Final PR review required: yes
Goal: Add the module interface, registry, execution context, module result, and execution plan types used by the engine.
Dependencies: T004, T005, T006
Acceptance criteria: The workflow layer can describe a module, register it, validate its dependencies, and represent an execution plan without concrete module logic.
Test requirements: Add direct registry and execution-plan tests in this task.
Parallelizable: no
Notes: Keep the contract explicit about enabled and disabled execution behavior.

- [X] T008 Implement the core workflow engine
Milestone: M001
Epic: E002
Risk: high
Implementation files: `backend/app/workflow/engine.py`
Test files: `backend/tests/unit/test_t008.py`
Validation commands: `python -m pytest backend/tests/unit/test_t008.py`
Final PR review required: yes
Goal: Build the engine that executes modules in order, skips disabled modules, validates dependencies, and surfaces basic failure behavior.
Dependencies: T007
Acceptance criteria: The engine can run a plan, respect enabled and disabled modules, stop on missing required dependencies, and record failed execution states.
Test requirements: Add direct engine order and missing-dependency tests in this task.
Parallelizable: no
Notes: Keep the engine provider-agnostic and free of filesystem assumptions.

## Phase 4: Artifact storage

- [X] T009 Implement artifact storage abstraction and local store
Milestone: M001
Epic: E003
Risk: high
Implementation files: `backend/app/storage/artifact_store.py`, `backend/app/storage/local_store.py`, `backend/app/storage/manifest.py`, `backend/app/domain/workflow_run.py`, `backend/app/domain/generation_job.py`
Test files: `backend/tests/unit/test_t009.py`
Validation commands: `python -m pytest backend/tests/unit/test_t009.py`
Final PR review required: yes
Goal: Create the artifact persistence interface, a local store implementation, and the artifact manifest format.
Dependencies: T006
Acceptance criteria: Artifacts can be saved, read, and listed through an interface, and stored runs/jobs can reference artifact keys instead of raw filesystem paths.
Test requirements: Add direct artifact-store tests in this task.
Parallelizable: no
Notes: The local implementation should honor configured storage roots and avoid hardcoded absolute paths.

## Phase 5: Provider abstraction

- [X] T010 Define provider interfaces and mock implementations
Milestone: M001
Epic: E003
Risk: high
Implementation files: `backend/app/providers/interfaces.py`, `backend/app/providers/mock_llm.py`, `backend/app/providers/mock_tts.py`, `backend/app/providers/mock_captions.py`, `backend/app/providers/mock_transcription.py`, `backend/app/providers/mock_assets.py`, `backend/app/providers/mock_video_renderer.py`, `backend/app/providers/mock_storage.py`
Test files: `backend/tests/unit/test_t010.py`
Validation commands: `python -m pytest backend/tests/unit/test_t010.py`
Final PR review required: yes
Goal: Create provider interfaces for LLM, TTS, captions, transcription, assets, video rendering, and storage, then implement deterministic mocks.
Dependencies: T004, T009
Acceptance criteria: All provider categories are available behind interfaces and the mock versions produce deterministic outputs suitable for tests.
Test requirements: Add direct deterministic mock-provider tests in this task.
Parallelizable: no
Notes: Keep real vendor integrations out of this slice.

## Phase 6: MVP modules

- [X] T011 Implement BriefModule
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/modules/brief.py`
Test files: `backend/tests/unit/test_t011.py`
Validation commands: `python -m pytest backend/tests/unit/test_t011.py`
Final PR review required: yes
Goal: Add the first intake module with deterministic behavior.
Dependencies: T007, T008, T010
Acceptance criteria: The module can transform a topic, brief or transcript into a normalized ContentBrief artifact without external integrations.
Test requirements: Add direct module-behavior tests in this task.
Parallelizable: no
Notes: Keep the module APIs narrow so each module can be tested independently.

- [X] T012 Implement VoiceoverModule
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/modules/voiceover.py`
Test files: `backend/tests/unit/test_t012.py`
Validation commands: `python -m pytest backend/tests/unit/test_t012.py`
Final PR review required: yes
Goal: Add optional voiceover generation using deterministic mock TTS output.
Dependencies: T007, T008, T009, T010, T011
Acceptance criteria: The module can produce a voiceover artifact reference when enabled and can be skipped when disabled without blocking exports that allow missing voiceover.
Test requirements: Add direct output-artifact and disabled-module tests in this task.
Parallelizable: no
Notes: Keep module output formats stable so the export bundle can assemble references without special-case logic.

## Phase 7: Workflow presets

- [X] T013 Define MVP workflow presets
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/workflow/presets.py`, `backend/app/workflow/registry.py`
Test files: `backend/tests/unit/test_t013.py`
Validation commands: `python -m pytest backend/tests/unit/test_t013.py`
Final PR review required: yes
Goal: Create the Short Video preset and Long-form Script + Voiceover preset with explicit module lists and default configuration.
Dependencies: T005, T006, T008, T011, T012
Acceptance criteria: Each preset declares content type, genre defaults, duration defaults, required modules, optional modules, default provider config, and expected artifacts.
Test requirements: Add direct tests for preset declarations, defaults, module lists, provider configuration, and expected artifacts in this task. Cross-preset registration and API smoke coverage remains in T019.
Parallelizable: no
Notes: Keep preset definitions declarative so they can be reused by API and tests. Cross-preset registration and API smoke coverage is handled in T019.

## Phase 8: API layer

- [X] T014 Create the API application and shared schemas
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/api/main.py`, `backend/app/api/schemas.py`, `backend/app/api/dependencies.py`
Test files: `backend/tests/unit/test_t014.py`
Validation commands: `python -m pytest backend/tests/unit/test_t014.py`
Final PR review required: yes
Goal: Add the FastAPI application entrypoint and request/response schemas for projects, workflow configs, workflow runs, artifacts, and export bundles.
Dependencies: T003, T005, T006, T009, T010
Acceptance criteria: The API layer has importable shared schemas and an application object that can be started without extra glue code.
Test requirements: Add direct schema validation and application-construction tests in this task. End-to-end API smoke coverage remains in T019.
Parallelizable: no
Notes: Keep API schemas aligned with the domain models rather than duplicating fields unnecessarily. End-to-end API smoke coverage is handled in T019.

- [X] T015 Implement minimal API endpoints
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/api/routes/projects.py`, `backend/app/api/routes/workflow_configs.py`, `backend/app/api/routes/workflow_runs.py`, `backend/app/api/routes/artifacts.py`
Test files: `backend/tests/unit/test_t015.py`
Validation commands: `python -m pytest backend/tests/unit/test_t015.py`
Final PR review required: yes
Goal: Expose create project, get project, create workflow config, start workflow run, get workflow run status, list artifacts, and export bundle endpoints.
Dependencies: T014, T013
Acceptance criteria: The API can create and retrieve project data, start a workflow run, inspect run status, list artifacts, and request an export bundle.
Test requirements: Add direct route behavior tests for request validation, project/configuration/run handlers, and artifact/export responses in this task. Full API smoke coverage remains in T019.
Parallelizable: no
Notes: Avoid making the CLI the only usable interface. Full API smoke coverage is handled in T019.

## Phase 9: Approval basics

- [X] T016 Add approval checkpoint domain model and state machine
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/domain/approval.py`
Test files: `backend/tests/unit/test_t016.py`
Validation commands: `python -m pytest backend/tests/unit/test_t016.py`
Final PR review required: yes
Goal: Implement approval checkpoints, approval decisions and allowed state transitions.
Dependencies: T006, T008, T015
Acceptance criteria: Approval checkpoints support not_required, pending, approved, rejected, changes_requested and skipped states; rejection preserves artifacts and records an approval decision.
Test requirements: Add direct approval model and state-transition tests in this task. Cross-workflow pause/resume and API approval coverage remains in T041.
Parallelizable: no
Notes: Keep approval simplified for MVP, but model it explicitly in the workflow state. Cross-workflow pause/resume and API approval coverage is handled in T041.

## Phase 10: Tests

- [X] T017 Add tests for module registry and execution order
Milestone: M003
Epic: E006
Risk: high
Implementation files: `none`
Test files: `backend/tests/unit/test_module_registry.py`, `backend/tests/unit/test_workflow_engine.py`
Validation commands: `python -m pytest backend/tests/unit/test_module_registry.py backend/tests/unit/test_workflow_engine.py`
Final PR review required: yes
Goal: Verify module registration, execution order, enabled and disabled module behavior, and missing dependency handling.
Dependencies: T007, T008
Acceptance criteria: The tests demonstrate that the registry and engine enforce order, skip disabled modules, and fail cleanly on missing dependencies.
Test requirements: These tests should be deterministic and run without network or filesystem dependencies.
Parallelizable: yes
Notes: Focus on the engine's contract rather than implementation details.

- [X] T018 Add tests for artifact storage and mock providers
Milestone: M003
Epic: E006
Risk: high
Implementation files: `none`
Test files: `backend/tests/unit/test_artifact_store.py`, `backend/tests/unit/test_mock_providers.py`
Validation commands: `python -m pytest backend/tests/unit/test_artifact_store.py backend/tests/unit/test_mock_providers.py`
Final PR review required: yes
Goal: Verify local artifact persistence, artifact manifests and mock providers.
Dependencies: T009, T010, T012
Acceptance criteria: The tests prove that artifacts are stored and retrieved through the abstraction and mocks are deterministic.
Test requirements: The tests should avoid real provider calls and use only local fixtures.
Parallelizable: yes
Notes: Failed module handling is covered by T044.

- [X] T019 Add tests for preset registration and API smoke paths
Milestone: M003
Epic: E006
Risk: high
Implementation files: `none`
Test files: `backend/tests/unit/test_presets.py`, `backend/tests/integration/test_api_smoke.py`
Validation commands: `python -m pytest backend/tests/unit/test_presets.py backend/tests/integration/test_api_smoke.py`
Final PR review required: yes
Goal: Verify the Short Video preset, Long-form preset registration, and a minimal API happy path.
Dependencies: T013, T014, T015, T016
Acceptance criteria: The tests prove that both presets resolve correctly and the API can drive a basic project, workflow config and workflow run lifecycle.
Test requirements: Keep the integration test thin and deterministic by using mock providers and local storage; export bundle content tests are covered by T042.
Parallelizable: yes
Notes: The API smoke test should verify status transitions rather than implementation internals.

## Phase 11: Migration documentation

- [X] T020 Document migration from source repos to the new architecture
Milestone: M002
Epic: E004
Risk: high
Implementation files: `docs/migration/shorts-repo-migration-plan.md`, `docs/migration/long-form-repo-migration-plan.md`
Test files: `none`
Validation commands: none
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
Test files: `backend/tests/unit/test_workflow_config_validation.py`
Validation commands: `python -m pytest`; `git diff --check`
Final PR review required: yes
Goal: Freeze the WorkflowConfig schema and reject invalid enum values, enabled/disabled module conflicts and invalid preset/content-type combinations.
Dependencies: T005
Acceptance criteria: Valid short_video and long_form_script_voiceover configs pass; invalid enum values fail; any module in both enabledModules and disabledModules fails; provider validation runs after config validation.
Test requirements: Add tests for valid short_video config, valid long_form_script_voiceover config, invalid enum, module conflict and validation ordering.
Parallelizable: no
Notes: Canonical workflowPreset values are short_video and long_form_script_voiceover.

- [X] T022 Implement ProviderRegistry and mock provider registration
Milestone: M001
Epic: E003
Risk: high
Implementation files: `backend/app/providers/registry.py`, `backend/app/providers/interfaces.py`, `backend/app/providers/mocks.py`
Test files: `backend/tests/unit/test_t022.py`
Validation commands: `python -m pytest backend/tests/unit/test_t022.py`
Final PR review required: yes
Goal: Register provider implementations by provider type and provider name, then register deterministic mock providers for MVP.
Dependencies: T010, T021
Acceptance criteria: Providers can be registered, resolved by type/name and exposed to module execution context.
Test requirements: Add ProviderRegistry registration and resolution tests.
Parallelizable: no
Notes: Provider types are LLMProvider, TTSProvider, TranscriptionProvider, CaptionProvider, AssetProvider, VideoRendererProvider, StorageProvider and PublishingProvider.

- [X] T023 Implement ProviderConfig validation before workflow execution
Milestone: M001
Epic: E003
Risk: high
Implementation files: `backend/app/providers/validation.py`, `backend/app/workflow/engine.py`, `backend/app/domain/workflow_config.py`
Test files: `backend/tests/unit/test_t023.py`
Validation commands: `python -m pytest backend/tests/unit/test_t023.py`
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

- [X] T025 Implement ScriptGenerationModule
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/modules/script_generation.py`
Test files: `backend/tests/unit/test_t025.py`
Validation commands: `python -m pytest backend/tests/unit/test_t025.py`
Final PR review required: yes
Goal: Generate deterministic script and NarrativeSegment artifacts from brief, outline or research context.
Dependencies: T011, T022
Acceptance criteria: The module creates script.txt, optional script.json and narrative_segments.json artifacts using mock providers or deterministic rules.
Test requirements: Add script output and narrative segment tests.
Parallelizable: no
Notes: Keep NarrativeSegment separate from RenderScene.

- [X] T026 Implement ScenePlanningModule
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/modules/scene_planning.py`
Test files: `backend/tests/unit/test_t026.py`
Validation commands: `python -m pytest backend/tests/unit/test_t026.py`
Final PR review required: yes
Goal: Generate RenderScene artifacts for short video workflows.
Dependencies: T011, T025
Acceptance criteria: The module creates render_scenes.json and scene_plan.json and can pause at scene plan approval before rendering.
Test requirements: Add scene planning and NarrativeSegment versus RenderScene separation tests.
Parallelizable: no
Notes: Required for short_video preset.

- [X] T027 Implement CaptionsModule
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/modules/captions.py`
Test files: `backend/tests/unit/test_t027.py`
Validation commands: `python -m pytest backend/tests/unit/test_t027.py`
Final PR review required: yes
Goal: Generate optional captions using deterministic caption provider output.
Dependencies: T010, T012, T026
Acceptance criteria: The module creates captions.srt or captions.json when enabled and is skipped cleanly when disabled.
Test requirements: Add enabled and disabled caption module tests.
Parallelizable: no
Notes: Disabled captions must not require CaptionProvider validation.

- [X] T028 Implement VideoRenderingModule
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/modules/video_rendering.py`
Test files: `backend/tests/unit/test_t028.py`
Validation commands: `python -m pytest backend/tests/unit/test_t028.py`
Final PR review required: yes
Goal: Generate deterministic video render metadata and artifact references for video workflows.
Dependencies: T010, T026, T027
Acceptance criteria: The module creates a video render artifact reference for short_video and is disabled by default for long_form_script_voiceover.
Test requirements: Add render-required and disabled-render tests.
Parallelizable: no
Notes: Do not implement real video rendering in the first slice.

- [X] T029 Define ExportBundle manifest schema
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/domain/export_bundle.py`, `backend/app/modules/export_manifest.py`
Test files: `backend/tests/unit/test_t029.py`
Validation commands: `python -m pytest backend/tests/unit/test_t029.py`
Final PR review required: yes
Goal: Define the manifest contract for required files, conditional artifacts and summary sections.
Dependencies: T006, T009, T021
Acceptance criteria: Manifest schema includes schemaVersion, exportId, projectId, workflowRunId, workflowPreset, contentType, contentGenre, durationProfile, createdAt, includedArtifacts, missingOptionalArtifacts, moduleResults, approvalSummary, providerSummary and artifactReferences.
Test requirements: Add export manifest schema tests.
Parallelizable: no
Notes: Required files are manifest.json, workflow_config.json and workflow_run.json.

- [X] T030 Implement ExportModule against the manifest contract
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/modules/export.py`
Test files: `backend/tests/unit/test_t030.py`
Validation commands: `python -m pytest backend/tests/unit/test_t030.py`
Final PR review required: yes
Goal: Package required workflow files and conditional artifact files or references into an export bundle.
Dependencies: T009, T029
Acceptance criteria: Export includes required files; includes script, narrative segments, render scenes, captions, voiceover, video, QA, research and dossier artifacts when present; records missing optional artifacts.
Test requirements: Add short-video export and long-form export tests.
Parallelizable: no
Notes: Export must work without publishing automation.

## Phase 14: Remediation - long-form workflow

- [X] T031 Implement ResearchModule
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/modules/research.py`
Test files: `backend/tests/unit/test_t031.py`
Validation commands: `python -m pytest backend/tests/unit/test_t031.py`
Final PR review required: yes
Goal: Produce deterministic research artifacts from topic or source inputs when research is enabled.
Dependencies: T009, T022, T023
Acceptance criteria: The module creates research.json linked to WorkflowRun and GenerationJob when enabled and is skipped cleanly when disabled.
Test requirements: Add enabled and disabled research tests.
Parallelizable: no
Notes: Do not implement real web fetching or external research integrations.

- [X] T032 Implement DossierModule
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/modules/dossier.py`
Test files: `backend/tests/unit/test_t032.py`
Validation commands: `python -m pytest backend/tests/unit/test_t032.py`
Final PR review required: yes
Goal: Produce a structured dossier artifact from research output when enabled.
Dependencies: T031
Acceptance criteria: The module creates dossier.json when research/dossier inputs exist and can be skipped when disabled.
Test requirements: Add dossier artifact tests.
Parallelizable: no
Notes: Long-form workflows can still continue from topic with research disabled.

- [X] T033 Implement OutlineModule
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/modules/outline.py`
Test files: `backend/tests/unit/test_t033.py`
Validation commands: `python -m pytest backend/tests/unit/test_t033.py`
Final PR review required: yes
Goal: Produce a long-form outline artifact from topic, brief, research or dossier context.
Dependencies: T011, T031, T032
Acceptance criteria: The module creates outline.json for the long_form_script_voiceover preset.
Test requirements: Add outline artifact tests.
Parallelizable: no
Notes: Outline is required for the long-form MVP path.

- [X] T034 Implement PostProcessingModule
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/modules/post_processing.py`
Test files: `backend/tests/unit/test_t034.py`
Validation commands: `python -m pytest backend/tests/unit/test_t034.py`
Final PR review required: yes
Goal: Normalize generated script text for downstream QA, optional voiceover and export.
Dependencies: T025, T033
Acceptance criteria: The module creates post_processed_script.txt and preserves the original script artifact.
Test requirements: Add post-processing artifact tests.
Parallelizable: no
Notes: Keep this deterministic for MVP.

- [X] T035 Implement QAModule
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/modules/qa.py`
Test files: `backend/tests/unit/test_t035.py`
Validation commands: `python -m pytest backend/tests/unit/test_t035.py`
Final PR review required: yes
Goal: Produce a deterministic QA report for long-form script output.
Dependencies: T034
Acceptance criteria: The module creates qa_report.json and can participate in script approval workflow.
Test requirements: Add QA report tests.
Parallelizable: no
Notes: QA is required for long_form_script_voiceover.

- [X] T036 Define LongFormWorkflowPreset
Milestone: M002
Epic: E004
Risk: high
Implementation files: `backend/app/workflow/presets.py`
Test files: `backend/tests/unit/test_t036.py`
Validation commands: `python -m pytest backend/tests/unit/test_t036.py`
Final PR review required: yes
Goal: Define the canonical long_form_script_voiceover preset with videoRendering disabled by default.
Dependencies: T013, T031, T032, T033, T034, T035
Acceptance criteria: Preset path is sources or topic -> research -> dossier -> outline -> script -> post-processing -> QA -> optional voiceover -> export; expected artifacts are enumerated.
Test requirements: Add preset validation tests.
Parallelizable: no
Notes: Long-form MVP does not need video rendering.

## Phase 15: Remediation - approval workflow and API

- [X] T037 Integrate approval checkpoints into workflow execution
Milestone: M002
Epic: E005
Risk: high
Implementation files: `backend/app/workflow/engine.py`, `backend/app/workflow/execution.py`
Test files: `backend/tests/unit/test_t037.py`
Validation commands: `python -m pytest backend/tests/unit/test_t037.py`
Final PR review required: yes
Goal: Pause and resume workflows at script, scene plan and final export checkpoints.
Dependencies: T016, T026, T030, T035
Acceptance criteria: Pending checkpoints pause before downstream modules; approved checkpoints continue; rejected and changes_requested checkpoints keep workflow paused; resume requires approved or policy-skipped checkpoints.
Test requirements: Add workflow approval pause/resume tests.
Parallelizable: no
Notes: Rejection preserves artifacts and records a decision.

- [X] T038 Add approval and resume API routes
Milestone: M002
Epic: E005
Risk: high
Implementation files: `backend/app/api/routes/approvals.py`, `backend/app/api/routes/workflow_runs.py`, `backend/app/api/main.py`
Test files: `backend/tests/unit/test_t038.py`
Validation commands: `python -m pytest backend/tests/unit/test_t038.py`
Final PR review required: yes
Goal: Expose approval inspection and decision endpoints.
Dependencies: T015, T016, T037
Acceptance criteria: API supports GET /workflow-runs/{runId}/approvals, POST approve, POST reject, POST request-changes and POST /workflow-runs/{runId}/resume.
Test requirements: Add API tests for approve, reject, request changes and blocked resume.
Parallelizable: no
Notes: Route naming may use the repository's established API prefix.

- [X] T047 Synchronize API schema with WorkflowConfig
Milestone: M002
Epic: E005
Risk: medium
Implementation files: `backend/app/api/schemas.py`
Test files: `backend/tests/unit/test_t047_api_schema_sync.py`
Validation commands: `python -m pytest backend/tests/unit/test_t047_api_schema_sync.py`
Final PR review required: yes
Goal: Keep the API schema aligned with the canonical WorkflowConfig after the domain-only remediation.
Dependencies: T014
Acceptance criteria: API schema reflects the canonical WorkflowConfig fields and enum constraints without reintroducing cross-epic dependency cycles.
Test requirements: Add direct API schema synchronization tests.
Parallelizable: yes
Notes: This remediation task isolates API schema synchronization from the completed domain task T021.

## Phase 16: Remediation - usage tracking and expanded tests

- [X] T039 Add UsageTracker and NoopCostTracker
Milestone: M003
Epic: E006
Risk: medium
Implementation files: `backend/app/workflow/usage.py`, `backend/app/workflow/execution.py`, `backend/app/workflow/engine.py`
Test files: `backend/tests/unit/test_t039.py`
Validation commands: `python -m pytest backend/tests/unit/test_t039.py`
Final PR review required: yes
Goal: Add minimal cost/usage infrastructure without billing or analytics.
Dependencies: T007, T008
Acceptance criteria: ModuleResult can include optional usage metadata and workflow execution succeeds when usage metadata is absent.
Test requirements: Add usage metadata absent test.
Parallelizable: yes
Notes: Do not implement billing dashboard or advanced analytics.

- [X] T040 Add provider and workflow config validation tests
Milestone: M003
Epic: E006
Risk: medium
Implementation files: `none`
Test files: `backend/tests/unit/test_provider_registry.py`, `backend/tests/unit/test_provider_validation.py`, `backend/tests/unit/test_workflow_config_validation.py`
Validation commands: `python -m pytest backend/tests/unit/test_provider_registry.py backend/tests/unit/test_provider_validation.py backend/tests/unit/test_workflow_config_validation.py`
Final PR review required: yes
Goal: Cover ProviderRegistry, provider validation and canonical WorkflowConfig validation.
Dependencies: T021, T022, T023
Acceptance criteria: Tests cover registration and resolution, missing provider, invalid provider type, disabled optional modules not requiring providers, valid mock provider config, valid presets and invalid enum rejection.
Test requirements: These are the test cases for this task.
Parallelizable: yes
Notes: Keep tests deterministic.

- [X] T041 Add approval workflow tests
Milestone: M003
Epic: E006
Risk: medium
Implementation files: `none`
Test files: `backend/tests/unit/test_approval_workflow.py`, `backend/tests/integration/test_approval_api.py`
Validation commands: `python -m pytest backend/tests/unit/test_approval_workflow.py backend/tests/integration/test_approval_api.py`
Final PR review required: yes
Goal: Cover script approval, approval decisions and resume behavior.
Dependencies: T016, T037, T038
Acceptance criteria: Tests cover pause at script approval, approve resumes workflow, reject keeps workflow paused, request changes records decision, resume is blocked without approval and final export approval is required when configured.
Test requirements: These are the test cases for this task.
Parallelizable: yes
Notes: Include artifact preservation assertions for rejection.

- [X] T042 Add export bundle content tests
Milestone: M003
Epic: E006
Risk: medium
Implementation files: `none`
Test files: `backend/tests/unit/test_export_manifest.py`, `backend/tests/integration/test_export_bundle.py`
Validation commands: `python -m pytest backend/tests/unit/test_export_manifest.py backend/tests/integration/test_export_bundle.py`
Final PR review required: yes
Goal: Verify required export files and conditional artifact references.
Dependencies: T029, T030
Acceptance criteria: Tests assert required files, conditional artifacts, missing optional artifacts, short-video export and long-form export contents.
Test requirements: These are the test cases for this task.
Parallelizable: yes
Notes: Do not require real media files.

- [X] T043 Add long-form workflow execution tests
Milestone: M003
Epic: E006
Risk: medium
Implementation files: `none`
Test files: `backend/tests/integration/test_long_form_workflow.py`
Validation commands: `python -m pytest backend/tests/integration/test_long_form_workflow.py`
Final PR review required: yes
Goal: Verify the long-form preset with enabled and disabled optional modules.
Dependencies: T031, T032, T033, T034, T035, T036
Acceptance criteria: Tests cover topic-based long-form execution, research enabled, research disabled, voiceover disabled and export completion without voiceover.
Test requirements: These are the test cases for this task.
Parallelizable: yes
Notes: Use mock providers and local artifact storage only.

- [X] T044 Add retry, failed module and static secret hygiene tests
Milestone: M003
Epic: E006
Risk: medium
Implementation files: `backend/app/workflow/engine.py`
Test files: `backend/tests/unit/test_retry_behavior.py`, `backend/tests/unit/test_failed_module_handling.py`, `backend/tests/static/test_secret_hygiene.py`
Validation commands: `python -m pytest backend/tests/unit/test_retry_behavior.py backend/tests/unit/test_failed_module_handling.py backend/tests/static/test_secret_hygiene.py`
Final PR review required: yes
Goal: Cover retry behavior, failed module behavior and committed config hygiene.
Dependencies: T008, T024, T039
Acceptance criteria: Tests cover transient retry, required module failure, optional module skip, no real-looking API keys in committed config and placeholder-only sample env values.
Test requirements: These are the test cases for this task.
Parallelizable: yes
Notes: Static secret checks should be narrow to avoid false positives.

## Phase 17: Remediation - direct domain tests

- [X] T045 Add direct tests for shared domain primitives
Milestone: M001
Epic: E001
Risk: medium
Implementation files: none
Test files: `backend/tests/unit/test_t045_domain_primitives.py`
Validation commands: `python -m pytest backend/tests/unit/test_t045_domain_primitives.py`
Final PR review required: yes
Goal: Add direct behavioral coverage for shared domain primitives without changing their implementation.
Dependencies: T004
Acceptance criteria: Tests cover real enum behavior, type behavior, base model validation, and serialization rather than import-only checks.
Test requirements: These are the direct behavioral test cases for this task.
Parallelizable: yes
Notes: This remediation task supplies the direct evidence that was not part of the original T004 completion package.

- [X] T046 Add direct tests for project and configuration domain models
Milestone: M001
Epic: E001
Risk: medium
Implementation files: none
Test files: `backend/tests/unit/test_t046_project_config_models.py`
Validation commands: `python -m pytest backend/tests/unit/test_t046_project_config_models.py`
Final PR review required: yes
Goal: Add direct behavioral coverage for project and configuration domain models without changing their implementation.
Dependencies: T005, T045
Acceptance criteria: Tests cover valid models, missing required fields, invalid values, serialization, configuration validation, and the absence of duplicated status definitions.
Test requirements: These are the direct behavioral test cases for this task.
Parallelizable: yes
Notes: This remediation task supplies the direct evidence that was not part of the original T005 completion package.

<!-- M004 REAL TTS TASKS EXTENSION START -->

## Phase 18: Real TTS foundation and narration fixtures

- [X] T048 Add and validate fixed Polish narration fixtures
Milestone: M004
Epic: E007
Risk: low
Implementation files: `backend/tests/fixtures/narrations/story_01_1min.txt`, `backend/tests/fixtures/narrations/story_02_5min.txt`, `backend/tests/fixtures/narrations/story_03_8min.txt`, `backend/tests/fixtures/narrations/story_04_15min.txt`, `backend/tests/fixtures/narrations/metadata.json`
Test files: `backend/tests/unit/test_t048.py`
Validation commands: `python -m pytest backend/tests/unit/test_t048.py`
Final PR review required: yes
Goal: Establish deterministic Polish narration inputs for repeatable one-, five-, eight- and fifteen-minute TTS comparisons.
Dependencies: None
Acceptance criteria: All four UTF-8 fixture files exist; metadata records title, language, target duration, actual word count, expected range and feature tags; validation recalculates word counts; each fixture contains Polish diacritics, punctuation variety, numbers or dates, abbreviations and dialogue; no test invokes an LLM or network service.
Test requirements: Add direct fixture-discovery, UTF-8, metadata, word-count and required-feature tests. The test must report the failing fixture and field clearly.
Parallelizable: no
Notes: The planning pack supplies initial fixture content. The task still owns verification, metadata correction if required and automated validation. Do not rewrite fixtures dynamically during tests.

## Phase 18A: Provider-neutral audio result

- [X] T049 Add an explicit provider-neutral TTS synthesis result model
Milestone: M004
Epic: E007
Risk: medium
Implementation files: `backend/app/providers/tts_result.py`, `backend/app/providers/__init__.py`
Test files: `backend/tests/unit/test_t049.py`
Validation commands: `python -m pytest backend/tests/unit/test_t049.py`
Final PR review required: yes
Goal: Define a typed result that can carry actual audio bytes and provider-neutral metadata without changing runtime behavior in this task.
Dependencies: T048
Acceptance criteria: `TTSSynthesisResult` is immutable or otherwise protected from accidental mutation; validates non-empty bytes, positive sample rate, non-negative duration, supported normalized audio format and non-empty provider name; metadata is copied defensively; invalid values raise actionable errors; existing provider and voiceover behavior remains compatible until T050.
Test requirements: Add construction, validation, serialization/helper and defensive-copy tests. Run the full suite because this introduces a shared provider type.
Parallelizable: no
Notes: Do not migrate `TTSProvider`, `MockTTSProvider` or `VoiceoverModule` in this task. Keeping T049 additive ensures the repository is green after one commit.

- [X] T050 Migrate mock TTS and VoiceoverModule to real WAV bytes
Milestone: M004
Epic: E007
Risk: high
Implementation files: `backend/app/providers/interfaces.py`, `backend/app/providers/mock_tts.py`, `backend/app/modules/voiceover.py`, `backend/app/providers/tts_result.py`
Test files: `backend/tests/unit/test_t010.py`, `backend/tests/unit/test_t012.py`, `backend/tests/unit/test_t050.py`, `backend/tests/unit/test_mock_providers.py`, `backend/tests/integration/test_long_form_workflow.py`
Validation commands: `python -m pytest backend/tests/unit/test_t010.py backend/tests/unit/test_t012.py backend/tests/unit/test_t050.py backend/tests/unit/test_mock_providers.py backend/tests/integration/test_long_form_workflow.py`
Final PR review required: yes
Goal: Make `voiceover.wav` contain valid audio data instead of a string reference while preserving provider abstraction and existing workflow outputs.
Dependencies: T049
Acceptance criteria: `TTSProvider.synthesize` returns `TTSSynthesisResult`; `MockTTSProvider` generates deterministic readable mono PCM WAV bytes using standard-library tooling; `VoiceoverModule` persists `audio_bytes`; metadata contains sample rate, duration, provider and source reference; tests prove the stored artifact starts with RIFF/WAVE and can be opened with `wave`; deterministic calls return identical bytes; existing long-form integration remains green; no Chatterbox import is introduced.
Test requirements: Add direct regression proving that a path/URI string cannot be silently stored as completed `voiceover.wav`. Update impacted legacy tests in the same task without weakening assertions.
Parallelizable: no
Notes: Preserve `voiceover.wav` naming and export compatibility. Do not change `CoreWorkflowEngine`, API routes or semantic speech timing in this task.

## Phase 19: Optional Chatterbox Multilingual V3 runtime

- [X] T051 Add optional Chatterbox dependency and runtime hygiene contract
Milestone: M004
Epic: E008
Risk: medium
Implementation files: `pyproject.toml`, `backend/requirements.txt`, `.gitignore`, `docs/tts/CHATTERBOX_SETUP.md`, `docs/tts/CHATTERBOX_MANUAL_SPIKE.md`, `docs/INDEX.md`
Test files: `backend/tests/unit/test_t051.py`
Validation commands: `python -m pytest backend/tests/unit/test_t051.py`
Final PR review required: yes
Goal: Document and declare an optional local Chatterbox Multilingual V3 runtime without making it part of the default CI or application import path.
Dependencies: T050
Acceptance criteria: Python 3.11 is documented; Chatterbox dependencies are optional; compatible PyTorch and torchaudio are selected per device outside the default dependency set; the Chatterbox source/version is pinned deterministically; the `setuptools<81` requirement is documented and pinned in the optional environment; normal project tests do not require CUDA; `.gitignore` excludes model caches, generated WAV files and local voice references; the successful manual spike is recorded; the built-in voice requires no speaker reference; optional speaker-reference cloning may be supported later; no private file path or model weight is committed.
Test requirements: Add or update lightweight planning validation only where existing coverage validates dependency metadata, ignore patterns or documentation references; base tests must import without optional packages installed.
Parallelizable: no
Notes: Read `docs/tts/CHATTERBOX_MANUAL_SPIKE.md`. Do not download packages or models during pytest.

- [X] T052 Implement Chatterbox Multilingual V3 provider adapter
Milestone: M004
Epic: E008
Risk: high
Implementation files: `backend/app/providers/chatterbox_v3.py`, `backend/app/providers/tts_result.py`, `backend/app/providers/interfaces.py`
Test files: `backend/tests/unit/test_t052.py`
Validation commands: `python -m pytest backend/tests/unit/test_t052.py`
Final PR review required: yes
Goal: Implement a real Chatterbox Multilingual V3 adapter behind the existing generic TTS contract.
Dependencies: T051
Acceptance criteria: Provider exposes stable `provider_type` and `provider_name`; it selects multilingual V3 explicitly with `t3_model="v3"`; language safely defaults to `pl` only where appropriate; the built-in voice works without speaker audio; optional `audio_prompt_path` may be accepted but cannot be required; model/backend loading is lazy and occurs at most once per provider instance; there is no import-time torch, CUDA, network or model initialization; output WAV is validated at 24 kHz; valid output returns `TTSSynthesisResult`; typed actionable errors cover missing optional dependency, unsupported device, unavailable CUDA, model-load failure, generation failure, a configured-but-missing optional audio prompt, and invalid or empty WAV output; errors do not expose private absolute paths.
Test requirements: Use injected fake loaders/backends only. Tests must fail if real Chatterbox is imported unexpectedly.
Parallelizable: no
Notes: Do not import this class from workflow or module code. Do not add chunking, scene logic or API changes.

- [X] T053 Add typed TTS settings and provider factory
Milestone: M004
Epic: E008
Risk: high
Implementation files: `backend/app/providers/tts_settings.py`, `backend/app/providers/tts_factory.py`, `backend/app/providers/__init__.py`, `backend/app/providers/registry.py`
Test files: `backend/tests/unit/test_t053.py`
Validation commands: `python -m pytest backend/tests/unit/test_t053.py`
Final PR review required: yes
Goal: Add typed Chatterbox TTS settings and compose mock or `chatterbox_v3` provider instances from the existing ProviderConfig through the existing ProviderRegistry without changing workflow orchestration.
Dependencies: T052
Acceptance criteria: Settings support `provider`, `device`, `language_id`, `model_variant`, `audio_prompt_path`, `exaggeration`, `cfg_weight`, `temperature`, `repetition_penalty`, `min_p` and `top_p`; the provider remains `mock` unless explicitly configured; device does not assume CUDA; `language_id` may default to `pl` for `chatterbox_v3`; `model_variant` must be `v3`; `audio_prompt_path` defaults to null and no committed private path is used; constructing the provider does not load the model; the factory supports `mock` and `chatterbox_v3`; no second generic provider registry or competing workflow configuration model is introduced; `CoreWorkflowEngine` and `VoiceoverModule` do not import concrete TTS providers.
Test requirements: Add factory-selection, settings-validation, unknown-provider, lazy-construction and import-boundary tests.
Parallelizable: no
Notes: Keep this as a composition layer around ProviderConfig and ProviderRegistry. Do not duplicate ProviderRegistry resolution logic or redesign other provider categories.

- [X] T054 Add offline Chatterbox provider contract and failure tests
Milestone: M004
Epic: E008
Risk: medium
Implementation files: `none` unless a minimal test seam correction is required in `backend/app/providers/chatterbox_v3.py`
Test files: `backend/tests/unit/test_t054.py`
Validation commands: `python -m pytest backend/tests/unit/test_t054.py`
Final PR review required: yes
Goal: Harden the Chatterbox provider boundary against accidental model/network usage and common configuration failures.
Dependencies: T053
Acceptance criteria: Tests prove default collection does not import torch or Chatterbox; no network or Hugging Face call is made; provider construction does not initialize a model; CPU and CUDA device choices are forwarded deterministically; `t3_model="v3"` and `language_id="pl"` are forwarded; built-in voice generation works without `audio_prompt_path`; optional `audio_prompt_path` is forwarded when configured; provider reuse does not reload the model; missing dependencies include installation guidance; invalid or empty WAV output is rejected; private paths are redacted from errors; model initialization never occurs at module import time.
Test requirements: Use monkeypatches and fake backends only.
Parallelizable: yes
Notes: Do not duplicate T052 behavior tests without adding a regression guarantee.

- [X] T055 Add manual Chatterbox V3 smoke runner and report
Milestone: M004
Epic: E008
Risk: medium
Implementation files: `backend/app/tooling/__init__.py`, `backend/app/tooling/tts_smoke.py`, `docs/tts/CHATTERBOX_SMOKE.md`, `docs/INDEX.md`
Test files: `backend/tests/unit/test_t055.py`
Validation commands: `python -m pytest backend/tests/unit/test_t055.py`
Final PR review required: yes
Goal: Provide one documented command that converts a fixture to WAV through the configured provider and emits machine-readable evidence.
Dependencies: T054
Acceptance criteria: Runner accepts provider, input text file, output WAV, language, device, optional audio prompt and Chatterbox generation settings; the default provider remains mock and `chatterbox_v3` is selected explicitly; model variant is V3; no private speaker file is the default and the built-in voice is the default Chatterbox voice path; it creates parent directories safely; it writes a machine-readable JSON report with provider, model variant, device, language, text word count, generation time, audio duration, sample rate, output checksum and whether the built-in or reference voice was used; it validates the final WAV; it returns non-zero on failure; `--help` works without optional dependencies; tests never download the model; the runner is excluded from normal integration execution.
Test requirements: Invoke the command entry function with a fake provider/factory and temporary files. Test success, invalid input, existing-output policy and non-zero failure paths.
Parallelizable: no
Notes: Real Chatterbox execution is manual human evidence and must not be added to CI. Do not wire the runner through API endpoints.

## Phase 20: Long narration reliability

- [X] T056 Implement deterministic technical narration chunking
Milestone: M004
Epic: E009
Risk: high
Implementation files: `backend/app/tts/__init__.py`, `backend/app/tts/chunking.py`
Test files: `backend/tests/unit/test_t056.py`
Validation commands: `python -m pytest backend/tests/unit/test_t056.py`
Final PR review required: yes
Goal: Split long Polish narration into stable TTS-sized chunks without semantic scene analysis or source-text loss.
Dependencies: T055
Acceptance criteria: Chunker prefers paragraph then sentence boundaries; emits ordered non-empty chunks with stable ids, indices, source offsets, word counts and text hashes; normalized concatenation equals normalized source; punctuation and Polish characters are preserved; an oversized sentence is handled deterministically and marked; identical input/settings yield identical chunks; 1-, 5-, 8- and 15-minute fixtures satisfy preservation tests.
Test requirements: Add boundary, repeated sentence, abbreviation, decimal/date, dialogue, oversized sentence, empty input and all-fixture preservation tests.
Parallelizable: no
Notes: This is technical request chunking only. Do not introduce `RenderScene`, shot descriptions, image prompts or semantic topic segmentation.

- [X] T057 Add resumable chunk synthesis and compatible PCM WAV assembly
Milestone: M004
Epic: E009
Risk: high
Implementation files: `backend/app/tts/assembly.py`, `backend/app/tts/manifest.py`, `backend/app/tts/chunk_synthesis.py`
Test files: `backend/tests/unit/test_t057.py`
Validation commands: `python -m pytest backend/tests/unit/test_t057.py`
Final PR review required: yes
Goal: Synthesize chunks independently, validate persisted outputs, resume interrupted work and assemble one final WAV without re-encoding.
Dependencies: T056
Acceptance criteria: Each chunk receives status, input/config hash, WAV checksum, duration and audio parameters; successful matching chunks are reused; missing, changed, corrupt or incompatible chunks are regenerated or rejected; retry is scoped per chunk; assembly validates channel count, sample width, sample rate and compression type; final frame count equals the sum of chunk frame counts; partial failure never registers a completed final WAV; manifests use relative artifact/runtime references rather than hardcoded machine paths.
Test requirements: Add simulated interruption/resume, changed text/config, corrupt WAV, mismatched sample rate/channels, retry, partial failure and frame-sum tests using deterministic small WAV fixtures.
Parallelizable: no
Notes: Use standard-library PCM WAV assembly for the first slice. Do not add FFmpeg unless the current task demonstrates a requirement that standard `wave` cannot satisfy and records a scope blocker.

- [X] T058 Add TTS benchmark report generation
Milestone: M004
Epic: E009
Risk: medium
Implementation files: `backend/app/tts/benchmark.py`, `backend/app/tts/manifest.py`, `backend/app/tooling/tts_smoke.py`
Test files: `backend/tests/unit/test_t058.py`
Validation commands: `python -m pytest backend/tests/unit/test_t058.py`
Final PR review required: yes
Goal: Produce stable performance evidence for comparing providers, devices and chunk settings without changing synthesis behavior.
Dependencies: T057
Acceptance criteria: Report includes provider, model, device, language, text word count, chunk count, generation wall time, audio duration, real-time factor, sample rate, output checksum and failed chunk ids; zero-duration and partial-failure cases are handled explicitly; JSON output is deterministic apart from declared timing/timestamp fields; benchmark collection wraps rather than duplicates synthesis logic.
Test requirements: Add calculation, rounding, serialization, zero-duration, failure and integration-with-smoke-runner tests.
Parallelizable: yes
Notes: Do not introduce cost accounting, dashboards or external telemetry.

- [X] T059 Integrate resumable long narration with VoiceoverModule
Milestone: M004
Epic: E009
Risk: high
Implementation files: `backend/app/modules/voiceover.py`, `backend/app/tts/chunking.py`, `backend/app/tts/chunk_synthesis.py`, `backend/app/tts/manifest.py`, `backend/app/tts/benchmark.py`
Test files: `backend/tests/unit/test_t059.py`, `backend/tests/unit/test_t012.py`, `backend/tests/integration/test_long_form_workflow.py`
Validation commands: `python -m pytest backend/tests/unit/test_t059.py backend/tests/unit/test_t012.py backend/tests/integration/test_long_form_workflow.py`
Final PR review required: yes
Goal: Allow `VoiceoverModule` to use either single-request or chunked/resumable synthesis while remaining provider-neutral and export-compatible.
Dependencies: T058
Acceptance criteria: Module selects chunked mode through generic configuration based on explicit settings rather than concrete provider type; short text remains supported; final `voiceover.wav`, speech timeline and synthesis manifest are persisted through the artifact store; module output identifies chunk count and benchmark artifact; a simulated interrupted 15-minute fixture run resumes without regenerating valid chunks; mock/fake provider tests complete offline; no workflow engine, API, scene-planning, caption or rendering code is modified.
Test requirements: Add one-minute direct mode, long fixture chunk mode, resume, provider substitution, failure propagation, artifact metadata and regression integration tests.
Parallelizable: no
Notes: If current `VoiceoverModule` dependency rules prevent direct narration execution, make the smallest contract correction and add a regression test; do not refactor workflow presets or fix unrelated short-video dependencies in this milestone.

<!-- M004 REAL TTS TASKS EXTENSION END -->

<!-- M005 TTS RUNTIME HARDENING TASKS EXTENSION START -->

## Phase 21: TTS runtime hardening

- [X] T060 Add effective TTS synthesis identity and settings propagation
Milestone: M005
Epic: E010
Risk: high
Implementation files: `backend/app/providers/interfaces.py`, `backend/app/providers/mock_tts.py`, `backend/app/providers/chatterbox_v3.py`, `backend/app/providers/tts_settings.py`, `backend/app/providers/tts_factory.py`
Test files: `backend/tests/unit/test_t060.py`, `backend/tests/unit/test_t052.py`, `backend/tests/unit/test_t053.py`
Validation commands: `python -m pytest backend/tests/unit/test_t060.py backend/tests/unit/test_t052.py backend/tests/unit/test_t053.py`
Final PR review required: yes
Goal: Expose a stable provider-neutral description of the effective synthesis configuration and forward all configured Chatterbox generation defaults.
Dependencies: T059
Acceptance criteria: TTSProvider exposes a JSON-compatible deterministic synthesis identity contract; MockTTSProvider and ChatterboxV3Provider implement it; Chatterbox identity includes provider name, model variant, device, effective language, effective generation settings and built-in or reference voice mode; reference voice identity uses a content checksum rather than an absolute path; missing reference files produce an actionable typed error without exposing the private path; settings from TTSSettings are forwarded by build_tts_provider as provider defaults; request-level voice_config values override defaults deterministically; model_variant remains v3; identity calculation does not import torch, torchaudio or Chatterbox and performs no network or model initialization.
Test requirements: Add fake-provider tests for stable identity, changed device, language, generation values, built-in voice, changed reference-file content, request overrides, settings propagation and lazy optional imports.
Parallelizable: no
Notes: Do not add provider-specific checks to VoiceoverModule and do not introduce a second provider registry.

- [X] T061 Harden cache identity and prune stale narration chunks
Milestone: M005
Epic: E010
Risk: high
Implementation files: `backend/app/tts/manifest.py`, `backend/app/tts/chunk_synthesis.py`
Test files: `backend/tests/unit/test_t061.py`, `backend/tests/unit/test_t057.py`
Validation commands: `python -m pytest backend/tests/unit/test_t061.py backend/tests/unit/test_t057.py`
Final PR review required: yes
Goal: Reuse persisted chunks only when the complete effective synthesis identity matches and keep manifests limited to the current narration.
Dependencies: T060
Acceptance criteria: ResumableChunkSynthesizer obtains the effective synthesis identity from the provider contract; config_hash includes the normalized provider identity and effective request configuration; identical effective configuration reuses valid chunks; changes to provider, model, device, language, generation defaults, request overrides or reference-audio content invalidate incompatible cache entries; absolute private paths are not stored in the manifest; records not present in the current ordered chunk set are removed before synthesis; orphaned chunk WAV files under the controlled runtime chunk directory are removed safely; files outside the runtime root are never touched; manifest chunk_count reflects only the current narration; changing one chunk without changing the effective provider identity regenerates only affected chunks where stable chunk identities still match.
Test requirements: Add full-cache-hit, partial text change, shortened narration, changed provider identity, changed reference-content checksum, stale-record pruning, orphan cleanup and path-boundary tests.
Parallelizable: no
Notes: Cache invalidation must be deterministic and local. Do not add timestamps, databases or external cache services.

- [X] T062 Make manifest lifecycle and WAV finalization crash-safe
Milestone: M005
Epic: E010
Risk: high
Implementation files: `backend/app/tts/assembly.py`, `backend/app/tts/manifest.py`, `backend/app/tts/chunk_synthesis.py`
Test files: `backend/tests/unit/test_t062.py`, `backend/tests/unit/test_t057.py`
Validation commands: `python -m pytest backend/tests/unit/test_t062.py backend/tests/unit/test_t057.py`
Final PR review required: yes
Goal: Ensure an interrupted or failed rerun cannot expose stale completion evidence or a partially written WAV.
Dependencies: T061
Acceptance criteria: At the beginning of a synthesis run the manifest transitions to running and clears final status fields from the previous run; the transition is persisted before chunk processing begins; a previous final WAV is removed or quarantined before the current run can be considered active; chunk WAV writes use a temporary file, validation and atomic replace; final assembly writes to a temporary path, validates parameters, frame count and checksum, and performs atomic replace only after success; final_status becomes completed only after the final file exists and matches recorded evidence; failure leaves final_status failed with no final artifact reference and no completed final WAV for the current run; a stale running manifest can be resumed; successfully completed chunks remain reusable after interruption; manifest writes remain atomic.
Test requirements: Simulate interruption after partial chunk completion, interruption before manifest finalization, failure during final write, stale completed output, corrupt temporary files, stale running recovery and successful atomic replacement.
Parallelizable: no
Notes: Standard-library PCM WAV handling remains the default. Do not add FFmpeg or platform-specific file locking.

- [X] T063 Make benchmark and smoke evidence reflect actual synthesis
Milestone: M005
Epic: E010
Risk: high
Implementation files: `backend/app/tts/benchmark.py`, `backend/app/tts/manifest.py`, `backend/app/tts/chunk_synthesis.py`, `backend/app/tts/assembly.py`, `backend/app/tooling/tts_smoke.py`, `backend/app/modules/voiceover.py`
Test files: `backend/tests/unit/test_t063.py`, `backend/tests/unit/test_t058.py`, `backend/tests/unit/test_t059.py`
Validation commands: `python -m pytest backend/tests/unit/test_t063.py backend/tests/unit/test_t058.py backend/tests/unit/test_t059.py`
Final PR review required: yes
Goal: Produce benchmark and smoke reports from actual provider identity, WAV parameters and per-run cache behavior.
Dependencies: T062
Acceptance criteria: Benchmark provider, model, device, language and voice mode come from the effective synthesis identity rather than guessed voice_config defaults; report includes generated_chunk_count, reused_chunk_count and failed_chunk_count; a full cache hit is explicitly distinguishable from model generation; real_time_factor is documented as current-run wall time divided by final audio duration; synthesis manifest records enough current-run evidence to build the report without inspecting private provider fields; VoiceoverModule uses provider-neutral manifest and identity data; tts_smoke uses the shared PCM WAV inspector and records actual channels, sample width, sample rate, compression type, frame count and duration; smoke and VoiceoverModule reject the same incompatible WAV formats; reports do not expose private absolute speaker-reference paths; JSON remains deterministic apart from declared timing fields.
Test requirements: Add cache miss, partial reuse, full reuse, failed chunk, actual identity, PCM parameter, smoke mismatch, path-redaction and VoiceoverModule benchmark regression tests.
Parallelizable: no
Notes: Do not add cost accounting, dashboards, telemetry, network upload or provider-specific logic to VoiceoverModule.

- [X] T064 Add 15-minute interruption and resume acceptance coverage
Milestone: M005
Epic: E010
Risk: high
Implementation files: `backend/app/modules/voiceover.py`, `backend/app/tts/chunk_synthesis.py`, `backend/app/tts/manifest.py`, `backend/app/tts/benchmark.py`
Test files: `backend/tests/unit/test_t064.py`, `backend/tests/unit/test_t059.py`, `backend/tests/integration/test_long_form_workflow.py`
Validation commands: `python -m pytest backend/tests/unit/test_t064.py backend/tests/unit/test_t059.py backend/tests/integration/test_long_form_workflow.py`
Final PR review required: yes
Goal: Prove offline that a real long narration workflow survives interruption, resumes valid work and exports one consistent final WAV.
Dependencies: T063
Acceptance criteria: The existing 15-minute Polish narration fixture is processed through VoiceoverModule with deterministic chunking and a fake local provider; the first run fails after a controlled number of successful chunks and does not expose a completed final WAV; a second run with the same workflow_run_id and effective synthesis identity reuses every valid completed chunk and generates only missing chunks; final WAV is valid mono 16-bit uncompressed PCM and its frame count equals the sum of current chunks; final manifest contains exactly the current ordered chunks; benchmark counts generated, reused and failed chunks correctly; changing provider identity invalidates incompatible reuse; changing reference-audio content invalidates incompatible reuse; an integration workflow executes with VoiceoverModule enabled and makes voiceover, timeline, synthesis manifest and benchmark artifacts available to export; existing one-request short narration behavior remains unchanged; all tests run without torch, Chatterbox, CUDA, network access or model downloads.
Test requirements: Use story_04_15min.txt, a deterministic fake provider and controlled temporary runtime directories. Test interruption, resume, provider substitution, reference identity change, artifact export and direct-mode regression.
Parallelizable: no
Notes: This is technical long-narration reliability testing. Do not introduce semantic scene splitting, captions, image generation, rendering or real Chatterbox execution in CI.

<!-- M005 TTS RUNTIME HARDENING TASKS EXTENSION END -->

<!-- M006 MULTI-PROVIDER POLISH TTS TASKS EXTENSION START -->

## Phase 22: TTS runtime baseline and provider capabilities

- [X] T065 Reproduce and lock Chatterbox Multilingual V3 runtime compatibility
Milestone: M006
Epic: E011
Risk: high
Implementation files: `pyproject.toml`, `backend/app/providers/chatterbox_v3.py`, `docs/tts/CHATTERBOX_SETUP.md`, `docs/tts/CHATTERBOX_SMOKE.md`
Test files: `backend/tests/unit/test_t065.py`, `backend/tests/unit/test_t054.py`, `backend/tests/unit/test_t055.py`
Validation commands: `python -m pytest backend/tests/unit/test_t065.py backend/tests/unit/test_t054.py backend/tests/unit/test_t055.py`
Final PR review required: yes
Goal: Convert the successful manual debugging result and direct PCM16 hotfix into a reproducible Chatterbox V3 runtime contract.
Dependencies: T064
Acceptance criteria: The optional dependency points to a validated Chatterbox source revision or released version whose `ChatterboxMultilingualTTS.from_pretrained` accepts the configured V3 model selector; the adapter verifies the expected callable contract before loading weights and raises an actionable compatibility error on mismatch; generated tensors are saved as mono signed 16-bit PCM WAV at 24 kHz; runtime errors retain a safe causal summary without exposing private paths or tokens; the default import path remains lazy; the manual setup document records the validated Python, torch, torchaudio, CUDA and Chatterbox versions; no real package or model is required by pytest.
Test requirements: Use fake modules to verify API-signature mismatch, V3 argument forwarding, `encoding=PCM_S`, `bits_per_sample=16`, valid WAV output, lazy imports and safe error messages.
Parallelizable: no
Notes: Do not silently fall back to the legacy multilingual model. Do not place a moving Git branch in production dependencies; pin an immutable revision or validated release.

- [X] T066 Add isolated TTS runtime profiles, setup scripts and health checks
Milestone: M006
Epic: E011
Risk: medium
Implementation files: `scripts/setup-tts-runtime.ps1`, `scripts/check-tts-runtime.ps1`, `scripts/run-tts-demo.ps1`, `docs/tts/RUNTIME_PROFILES.md`, `.gitignore`
Test files: `backend/tests/static/test_t066.py`
Validation commands: `python -m pytest backend/tests/static/test_t066.py`
Final PR review required: yes
Goal: Make heavy local TTS runtimes reproducible without contaminating the agent or CI environment.
Dependencies: T065
Acceptance criteria: Documentation defines `.venv-ci311`, `.venv-tts311`, `.venv-piper311` and `.venv-xtts311`; scripts invoke explicit environment interpreters, are idempotent where practical and never activate or repoint `agent.python`; the Chatterbox setup script validates Python 3.11, matching torch/torchaudio versions, CUDA visibility, provider imports and one opt-in real smoke; health-check output is machine-readable as well as human-readable; generated environments, model caches, reference audio and comparison outputs are ignored; scripts fail before installing when prerequisites are unsupported.
Test requirements: Add static tests for path resolution, explicit interpreter use, ignore coverage, no hook-config mutation, no embedded credentials and documented failure modes. Real installation remains manual.
Parallelizable: no
Notes: PowerShell is the validated Windows path. A POSIX equivalent may be added later but is not required by this task.

- [X] T067 Add provider capability and usage-policy contracts
Milestone: M006
Epic: E011
Risk: high
Implementation files: `backend/app/providers/interfaces.py`, `backend/app/providers/tts_capabilities.py`, `backend/app/providers/mock_tts.py`, `backend/app/providers/chatterbox_v3.py`, `backend/app/providers/tts_settings.py`, `backend/app/providers/tts_factory.py`
Test files: `backend/tests/unit/test_t067.py`, `backend/tests/unit/test_t060.py`, `backend/tests/unit/test_t053.py`
Validation commands: `python -m pytest backend/tests/unit/test_t067.py backend/tests/unit/test_t060.py backend/tests/unit/test_t053.py`
Final PR review required: yes
Goal: Describe provider features and permitted runtime role without loading optional model code.
Dependencies: T066
Acceptance criteria: TTSProvider exposes deterministic capability metadata containing provider name, supported languages, voice modes, reference-audio requirement, speaking-rate support and usage policy; mock and Chatterbox implement the contract; capability inspection imports no heavy optional modules and performs no network access; TTSSettings validates provider-neutral policy mode separately from provider-specific generation settings; the factory continues to use the existing ProviderRegistry; unsupported provider capabilities fail with actionable provider-neutral errors; effective synthesis identity remains request-specific and separate from static capabilities.
Test requirements: Add serialization, deterministic ordering, lazy-import, policy-mode, unsupported-language, unsupported-voice-mode and existing-factory regression tests.
Parallelizable: no
Notes: Do not encode a UI schema and do not add a second registry.

## Phase 23: Piper Polish provider

- [X] T068 Add an optional lazy Piper TTS provider
Milestone: M006
Epic: E012
Risk: high
Implementation files: `pyproject.toml`, `backend/app/providers/piper_tts.py`, `backend/app/providers/tts_settings.py`, `backend/app/providers/tts_factory.py`, `backend/app/providers/tts_result.py`
Test files: `backend/tests/unit/test_t068.py`, `backend/tests/unit/test_t053.py`
Validation commands: `python -m pytest backend/tests/unit/test_t068.py backend/tests/unit/test_t053.py`
Final PR review required: yes
Goal: Generate provider-neutral PCM WAV results from a local Piper ONNX voice without changing VoiceoverModule.
Dependencies: T067
Acceptance criteria: Piper is installed only through an optional dependency/runtime profile; imports and model loading are lazy; configuration accepts an explicit managed model key or approved local model path but manifests never persist absolute paths; the provider uses the Piper Python API and returns TTSSynthesisResult with truthful sample rate and duration; output is mono 16-bit uncompressed PCM WAV compatible with shared inspection and chunk assembly; missing runtime, missing model, invalid config and invalid WAV produce typed actionable errors; no model is downloaded during provider construction or tests.
Test requirements: Use a fake Piper runtime to verify lazy import, model resolution, synthesis config forwarding, WAV validation, metadata, identity and error handling.
Parallelizable: no
Notes: The default implementation is CPU-first. CUDA acceleration is optional and must not become a test requirement.

- [X] T069 Add a curated Polish Piper voice catalog and asset identity
Milestone: M006
Epic: E012
Risk: high
Implementation files: `backend/app/providers/piper_catalog.py`, `backend/app/providers/piper_tts.py`, `docs/tts/PIPER_SETUP.md`, `docs/tts/PIPER_VOICES.md`, `scripts/setup-piper-runtime.ps1`, `scripts/check-piper-runtime.ps1`
Test files: `backend/tests/unit/test_t069.py`, `backend/tests/static/test_t069.py`
Validation commands: `python -m pytest backend/tests/unit/test_t069.py backend/tests/static/test_t069.py`
Final PR review required: yes
Goal: Resolve known Polish Piper voices reproducibly and preserve source, checksum and license evidence.
Dependencies: T068
Acceptance criteria: The catalog initially covers `pl_PL-bass-high`, `pl_PL-darkman-medium`, `pl_PL-gosia-medium`, `pl_PL-mc_speech-medium` and `pl_PL-mls_6892-low` only after each model card is reviewed; every entry records provider key, language, quality, expected sample rate, source repository, immutable revision when available, required files, checksums and license identifier; setup downloads are explicit human actions and verify checksums before activation; catalog identity is included in effective synthesis identity; changed model bytes or catalog revision invalidate cache reuse; unknown voices fail without attempting arbitrary downloads; the engine and model-license review is documented separately from technical installation.
Test requirements: Add catalog validation, duplicate key, missing checksum, changed checksum, path redaction, unknown voice and deterministic identity tests. Static tests must ensure no model binaries are committed.
Parallelizable: no
Notes: Model catalog inclusion is not automatic approval for commercial distribution.

- [X] T070 Add Piper controls and manual smoke comparison support
Milestone: M006
Epic: E012
Risk: medium
Implementation files: `backend/app/providers/piper_tts.py`, `backend/app/providers/tts_settings.py`, `backend/app/tooling/tts_smoke.py`, `backend/app/tooling/tts_compare.py`, `scripts/run-tts-provider-comparison.ps1`, `docs/tts/PIPER_SMOKE.md`
Test files: `backend/tests/unit/test_t070.py`, `backend/tests/unit/test_t063.py`
Validation commands: `python -m pytest backend/tests/unit/test_t070.py backend/tests/unit/test_t063.py`
Final PR review required: yes
Goal: Compare Chatterbox neutral against selected Polish Piper voices using the same text and truthful metrics.
Dependencies: T069
Acceptance criteria: Piper settings support validated `length_scale`, volume, noise scale and noise-width scale without leaking unsupported values to other providers; `tts_smoke` accepts Piper and reports the resolved voice/model identity; the comparison runner invokes providers sequentially, writes one WAV and report per profile, a summary JSON and playlist, and continues after one profile failure while recording the reason; the default comparison includes Chatterbox neutral and the curated Piper voices; comparison output is ignored; tests use fake providers only and assert identical normalized text across profiles.
Test requirements: Add settings bounds, provider-specific rejection, same-text, sequential execution, partial failure, summary aggregation, playlist and benchmark identity tests.
Parallelizable: no
Notes: Loudness-normalized derivatives may be added later, but original provider outputs must always be preserved.

## Phase 24: XTTS evaluation and provider selection

- [X] T071 Add an evaluation-only XTTS-v2 provider
Milestone: M006
Epic: E013
Risk: high
Implementation files: `pyproject.toml`, `backend/app/providers/xtts_v2.py`, `backend/app/providers/tts_settings.py`, `backend/app/providers/tts_factory.py`, `docs/tts/XTTS_SETUP.md`
Test files: `backend/tests/unit/test_t071.py`, `backend/tests/unit/test_t053.py`
Validation commands: `python -m pytest backend/tests/unit/test_t071.py backend/tests/unit/test_t053.py`
Final PR review required: yes
Goal: Evaluate Polish XTTS-v2 voice cloning without presenting it as a production-approved provider.
Dependencies: T070
Acceptance criteria: XTTS dependencies are isolated in `.venv-xtts311` and imported lazily; the provider name is `xtts_v2_eval`; capabilities declare `usage_policy=evaluation_only`, Polish support and required reference audio; provider construction and identity calculation perform no network access; synthesis requires an existing approved reference WAV and stores only its checksum and approved label; output is validated mono 16-bit PCM WAV with truthful sample rate; production policy rejects the provider before model loading; tests use fake runtime objects and generated WAV fixtures only.
Test requirements: Add evaluation-policy, required-reference, consent-label, checksum identity, path redaction, lazy import, generation argument, WAV validation and production rejection tests.
Parallelizable: no
Notes: Do not add built-in public-figure voices, bundled private samples or a production override hidden in provider settings.

- [X] T072 Complete provider selection through configuration and tooling
Milestone: M006
Epic: E013
Risk: high
Implementation files: `backend/app/providers/tts_settings.py`, `backend/app/providers/tts_factory.py`, `backend/app/providers/registry.py`, `backend/app/tooling/tts_smoke.py`, `backend/app/modules/voiceover.py`
Test files: `backend/tests/unit/test_t072.py`, `backend/tests/unit/test_t053.py`, `backend/tests/unit/test_t059.py`
Validation commands: `python -m pytest backend/tests/unit/test_t072.py backend/tests/unit/test_t053.py backend/tests/unit/test_t059.py`
Final PR review required: yes
Goal: Select mock, Chatterbox, Piper or evaluation XTTS by changing ProviderConfig only.
Dependencies: T071
Acceptance criteria: TTSSettings accepts provider-specific validated sub-settings without a union of silently ignored fields; build_tts_provider composes all supported providers through the existing registry; smoke tooling uses the same factory path instead of a separate hard-coded constructor switch; VoiceoverModule remains unaware of concrete provider classes; production/evaluation policy mode is explicit at the composition boundary; unsupported language, voice mode, model asset or policy is rejected before model loading; provider selection changes effective identity and invalidates incompatible cache entries.
Test requirements: Add one ProviderConfig composition test per provider, strict cross-provider settings tests, policy tests, smoke/factory parity, Voiceover substitution and cache identity regressions.
Parallelizable: no
Notes: A CLI flag is a configuration input, not a second provider-selection implementation.

- [X] T073 Add a reproducible cross-provider Polish comparison harness
Milestone: M006
Epic: E013
Risk: medium
Implementation files: `backend/app/tooling/tts_compare.py`, `scripts/run-tts-provider-comparison.ps1`, `docs/tts/PROVIDER_COMPARISON.md`, `backend/tests/fixtures/narrations/story_01_1min.txt`
Test files: `backend/tests/unit/test_t073.py`
Validation commands: `python -m pytest backend/tests/unit/test_t073.py`
Final PR review required: yes
Goal: Produce auditable same-text evidence for human provider and voice selection.
Dependencies: T072
Acceptance criteria: A manifest-driven comparison defines profiles separately from implementation code; all profiles receive the same normalized input text and declared random seed where the provider supports it; providers run sequentially; each result records status, effective identity, generation wall time, audio duration, real-time factor, PCM parameters, checksum and output path; failures are isolated per profile; XTTS is skipped unless an approved reference is supplied; summary output includes no private absolute paths; a human scoring template covers naturalness, Polish pronunciation, pace, timbre, expression and artifacts.
Test requirements: Add deterministic manifest parsing, duplicate profile, same-text, seed propagation, skip, failure isolation, redaction, metrics and scoring-template tests using fake providers.
Parallelizable: no
Notes: Human listening decides quality; automated metrics are evidence only.

- [X] T074 Add multi-provider acceptance coverage and operational decision record
Milestone: M006
Epic: E013
Risk: high
Implementation files: `backend/app/modules/voiceover.py`, `backend/app/tts/chunk_synthesis.py`, `backend/app/tts/manifest.py`, `docs/tts/M006_PROVIDER_DECISION.md`, `docs/INDEX.md`
Test files: `backend/tests/unit/test_t074.py`, `backend/tests/unit/test_t064.py`, `backend/tests/integration/test_long_form_workflow.py`
Validation commands: `python -m pytest backend/tests/unit/test_t074.py backend/tests/unit/test_t064.py backend/tests/integration/test_long_form_workflow.py`
Final PR review required: yes
Goal: Prove offline that provider substitution preserves workflow behavior, cache correctness and policy boundaries.
Dependencies: T073
Acceptance criteria: One provider-neutral integration scenario executes the same short and resumable narration workflow with deterministic fake Chatterbox, Piper and XTTS adapters; output artifact contracts remain identical apart from truthful provider metadata and sample rate; changing provider, model/voice asset, speaking-rate controls or reference content invalidates incompatible chunks; unchanged configuration reuses valid chunks; production policy rejects evaluation XTTS; capability metadata and effective identity contain no private paths; docs record provider roles, runtime profiles, license-review requirements and the human comparison decision process; the full suite passes without real runtimes or network access.
Test requirements: Add provider substitution, short mode, resumable mode, cache invalidation, full reuse, policy rejection, artifact parity, path redaction and documentation-link tests.
Parallelizable: no
Notes: Completion of M006 enables a later UI or API selector but does not implement one.

<!-- M006 MULTI-PROVIDER POLISH TTS TASKS EXTENSION END -->

<!-- M007 ENGLISH-FIRST YOUTUBE PRODUCTION TASKS EXTENSION START -->

## Phase 25: English-first localization boundary

- [X] T075 Close M006 and record the English-first localization architecture decision
Milestone: M007
Epic: E014
Risk: medium
Implementation files: `docs/tts/M006_PROVIDER_DECISION.md`, `docs/decisions/0002-english-first-localization-boundary.md`, `docs/INDEX.md`
Test files: `backend/tests/static/test_t075.py`
Validation commands: `python -m pytest backend/tests/static/test_t075.py`
Final PR review required: yes
Goal: Lock the completed Polish TTS investigation outcome and the English-source/platform-localization product boundary into reviewable documentation and static architecture guards.
Dependencies: T074
Acceptance criteria: The M006 manifest is completed while E011-E013 and T065-T074 history remain unchanged; the provider decision preserves the historical M006 implementation and adds the final outcome; Chatterbox is identified as the current general production-capable baseline, Piper as a fast local provider rather than the product quality baseline, and XTTS as evaluation-only; MOSS and other experimental runners remain outside the production TTS registry; the ADR states that English is the primary production language, WorkflowConfig.language describes generated source content, localization belongs to export/publishing configuration, platform auto-dubbing is preferred and custom localized narration is a fallback; documentation is indexed; static guards reject concrete Chatterbox/YouTube selection branches in CoreWorkflowEngine or VoiceoverModule.
Test requirements: Add offline static checks for milestone/epic completion consistency, required decision statements, documentation links, absence of MOSS production registration and absence of concrete provider/platform selection branches in orchestration.
Parallelizable: no
Notes: Preserve the historical record; do not rewrite M006 as if it had originally been English-first and do not add an experimental provider to TTSFactory.

- [X] T076 Add validated localization and export configuration
Milestone: M007
Epic: E014
Risk: high
Implementation files: `backend/app/domain/export_config.py`, `backend/app/domain/workflow_config.py`, `backend/app/domain/__init__.py`, `backend/app/api/schemas.py`, `backend/app/workflow/presets.py`
Test files: `backend/tests/unit/test_t076.py`, `backend/tests/unit/test_t046_project_config_models.py`, `backend/tests/unit/test_workflow_config_validation.py`
Validation commands: `python -m pytest backend/tests/unit/test_t076.py backend/tests/unit/test_t046_project_config_models.py backend/tests/unit/test_workflow_config_validation.py`
Final PR review required: yes
Goal: Represent source-language generation and downstream localization as separate validated configuration concerns.
Dependencies: T075
Acceptance criteria: WorkflowConfig.language remains the non-empty source content and narration language; a typed validated export configuration carries ordered unique localization targets, preferred localization mode, manual acceptance policy and optional custom-audio fallback policy; localization configuration round-trips through API schemas and preset composition without becoming a second source-language field; the existing long-form YouTube path defaults to English source production and platform-first localization while other preset behavior remains backward compatible; unsupported modes, duplicate/invalid target languages, a target equal to the source language and unknown fields fail before workflow execution; CoreWorkflowEngine and VoiceoverModule receive provider-neutral configuration and contain no provider or platform selection branches.
Test requirements: Add model/API round-trip, default, backward-compatibility, strict unknown-field, language separation, duplicate target, same-as-source target and static no-branch tests.
Parallelizable: no
Notes: Keep localization within export/publishing configuration; do not replace WorkflowConfig.language or introduce YouTube-specific orchestration.

## Phase 26: English narration production baseline

- [X] T077 Add a reproducible English Chatterbox production baseline and manual smoke path
Milestone: M007
Epic: E015
Risk: high
Implementation files: `backend/app/providers/chatterbox_v3.py`, `backend/app/providers/tts_capabilities.py`, `backend/app/tooling/tts_smoke.py`, `backend/tests/fixtures/narrations/metadata.json`, `backend/tests/fixtures/narrations/story_en_01_1min.txt`, `scripts/setup-tts-runtime.ps1`, `scripts/check-tts-runtime.ps1`, `scripts/run-tts-demo.ps1`, `docs/tts/CHATTERBOX_ENGLISH_BASELINE.md`, `docs/INDEX.md`
Test files: `backend/tests/unit/test_t077.py`, `backend/tests/static/test_t077.py`, `backend/tests/unit/test_t065.py`
Validation commands: `python -m pytest backend/tests/unit/test_t077.py backend/tests/static/test_t077.py backend/tests/unit/test_t065.py`
Final PR review required: yes
Goal: Make the existing Chatterbox Multilingual V3 integration reproducibly usable as the English production narration baseline without changing provider-neutral composition.
Dependencies: T076
Acceptance criteria: Chatterbox capabilities truthfully advertise English and existing supported languages; English language selection is forwarded through the existing TTS settings/factory path; a fixed English one-minute fixture and metadata are deterministic; setup and health checks retain explicit isolated interpreter paths and validated immutable/runtime version evidence; one documented manual command generates an English WAV and JSON evidence with effective provider, model, voice mode, device, language, duration, PCM parameters and checksum; built-in and approved-reference voice modes remain explicit; missing runtime/device/reference failures are actionable and path-safe; default tests use fake runtime objects and make no model or network request.
Test requirements: Add English capability, language forwarding, effective identity, fixture metadata, manual command, lazy import, runtime-profile and existing Polish compatibility regressions using fakes only.
Parallelizable: no
Notes: Do not add MOSS or another experimental model to the production registry and do not run a real model in CI.

- [X] T078 Validate English long-form resumable narration and artifact parity
Milestone: M007
Epic: E015
Risk: high
Implementation files: `backend/app/modules/voiceover.py`, `backend/app/tts/chunk_synthesis.py`, `backend/app/tts/manifest.py`, `backend/app/tts/benchmark.py`
Test files: `backend/tests/unit/test_t078.py`, `backend/tests/unit/test_t064.py`, `backend/tests/integration/test_long_form_workflow.py`
Validation commands: `python -m pytest backend/tests/unit/test_t078.py backend/tests/unit/test_t064.py backend/tests/integration/test_long_form_workflow.py`
Final PR review required: yes
Goal: Prove that long-form English narration can resume deterministically and produces the same artifact contracts through any compatible TTS provider.
Dependencies: T077
Acceptance criteria: A deterministic long English fixture is chunked in stable text order; an interrupted run preserves completed chunks without publishing a stale final WAV; resume with identical effective synthesis identity reuses every valid chunk and generates only missing chunks; changes to provider, model/voice, language, generation settings or reference content invalidate incompatible reuse; final WAV, synthesis manifest and benchmark are atomically finalized and contain truthful English/provider identity; artifact names and shapes are identical across deterministic fake Chatterbox and another fake TTS provider apart from truthful provider/sample-rate metadata; the workflow engine and VoiceoverModule remain unaware of concrete provider classes.
Test requirements: Add interruption/resume, full reuse, source-language identity, provider substitution, incompatible cache, final-WAV integrity, manifest/benchmark parity and no-concrete-import tests without real runtimes.
Parallelizable: no
Notes: Preserve technical chunking as distinct from NarrativeSegment and RenderScene; do not add provider-specific retry or fallback behavior.

## Phase 27: YouTube-ready export

- [X] T079 Add YouTube-ready export metadata and platform handoff artifacts
Milestone: M007
Epic: E016
Risk: high
Implementation files: `backend/app/domain/export_bundle.py`, `backend/app/domain/platform_handoff.py`, `backend/app/modules/export_manifest.py`, `backend/app/modules/export.py`, `docs/publishing/YOUTUBE_HANDOFF.md`, `docs/INDEX.md`
Test files: `backend/tests/unit/test_t079.py`, `backend/tests/unit/test_export_manifest.py`, `backend/tests/integration/test_export_bundle.py`
Validation commands: `python -m pytest backend/tests/unit/test_t079.py backend/tests/unit/test_export_manifest.py backend/tests/integration/test_export_bundle.py`
Final PR review required: yes
Goal: Package existing production artifacts and deterministic upload metadata into an auditable YouTube-ready handoff without publishing.
Dependencies: T078
Acceptance criteria: Export configuration validates a YouTube target without adding platform branches to CoreWorkflowEngine; the export bundle includes deterministic source-language metadata, title, description, tags and user-supplied audience settings plus checksummed references for available video, English narration and captions; missing optional artifacts are reported rather than fabricated; platform handoff state is provider-neutral and serializable; private paths, credentials and tokens are absent; identical inputs produce identical handoff content apart from explicitly documented creation timestamps/identifiers; final export approval remains required before publishing; existing generic exports remain backward compatible.
Test requirements: Add metadata validation, deterministic ordering, artifact inclusion/missing reporting, checksum, redaction, generic-export regression, approval boundary and no-engine-platform-branch tests using temporary artifact stores.
Parallelizable: no
Notes: This task creates upload-ready artifacts only; it must not call the YouTube API or claim that a bundle was published.

- [X] T080 Add deterministic English caption and subtitle export for YouTube handoff
Milestone: M007
Epic: E016
Risk: high
Implementation files: `backend/app/domain/caption_track.py`, `backend/app/modules/captions.py`, `backend/app/providers/mock_captions.py`, `backend/app/modules/export.py`, `docs/publishing/YOUTUBE_HANDOFF.md`
Test files: `backend/tests/unit/test_t080.py`, `backend/tests/unit/test_t027.py`, `backend/tests/integration/test_export_bundle.py`
Validation commands: `python -m pytest backend/tests/unit/test_t080.py backend/tests/unit/test_t027.py backend/tests/integration/test_export_bundle.py`
Final PR review required: yes
Goal: Emit deterministic source-English subtitle artifacts that can be inspected and manually uploaded with the YouTube-ready bundle.
Dependencies: T079
Acceptance criteria: Caption segments use stable source-text ordering, non-negative non-overlapping timestamps and deterministic identifiers; UTF-8 SRT is emitted as its own artifact with stable sequence numbers and line endings while existing captions.json remains available; the export bundle references both artifacts and records source language `en`; malformed, empty, overlapping or out-of-order segments fail before export; caption generation remains behind CaptionProvider and uses deterministic fakes in tests; this task does not translate captions or create localized audio.
Test requirements: Add Unicode, timestamp formatting, ordering, overlap, empty segment, stable serialization, artifact-store, export inclusion and existing CaptionsModule regression tests.
Parallelizable: no
Notes: Keep caption timing separate from narrative segmentation and rendering; localization targets do not change the source-English subtitle track.

## Phase 28: YouTube publishing and localization handoff

- [X] T081 Add a YouTube publishing provider boundary with offline tests and optional runtime dependencies
Milestone: M007
Epic: E017
Risk: high
Implementation files: `pyproject.toml`, `backend/app/providers/interfaces.py`, `backend/app/providers/registry.py`, `backend/app/providers/publishing_factory.py`, `backend/app/providers/youtube_publishing.py`, `backend/app/providers/mocks.py`, `docs/publishing/YOUTUBE_HANDOFF.md`
Test files: `backend/tests/unit/test_t081.py`, `backend/tests/unit/test_provider_registry.py`, `backend/tests/unit/test_t022.py`
Validation commands: `python -m pytest backend/tests/unit/test_t081.py backend/tests/unit/test_provider_registry.py backend/tests/unit/test_t022.py`
Final PR review required: yes
Goal: Compose YouTube publishing behind the existing generic provider boundary while keeping default execution deterministic, credential-free and offline.
Dependencies: T080
Acceptance criteria: PublishingProvider exposes a typed request/result contract for an approved export bundle; mock publishing is deterministic and remains the default test implementation; an optional YouTube adapter is selected through ProviderConfig and ProviderRegistry rather than CoreWorkflowEngine branches; optional real client dependencies are isolated from the default install and imported lazily; provider construction performs no network request; credentials come only from explicit runtime configuration and are never serialized; tests inject a fake transport and cover request mapping, idempotency identity, error translation and redaction; publishing is rejected before provider invocation when final export approval is absent.
Test requirements: Add protocol/factory, registry composition, lazy dependency, missing credential, fake transport, deterministic mock, approved-export, idempotency, safe error and no-network tests.
Parallelizable: no
Notes: Do not use a real account or network in pytest, do not bypass approval and do not place `if platform == "youtube"` in workflow orchestration.

- [ ] T082 Add auto-dubbing handoff state, manual acceptance and custom-dub fallback metadata
Milestone: M007
Epic: E017
Risk: high
Implementation files: `backend/app/domain/localization_handoff.py`, `backend/app/domain/approval.py`, `backend/app/modules/publishing.py`, `backend/app/api/routes/publishing.py`, `backend/app/api/schemas.py`, `backend/app/modules/export.py`, `docs/publishing/YOUTUBE_HANDOFF.md`
Test files: `backend/tests/unit/test_t082.py`, `backend/tests/unit/test_approval_workflow.py`, `backend/tests/integration/test_publishing_handoff.py`
Validation commands: `python -m pytest backend/tests/unit/test_t082.py backend/tests/unit/test_approval_workflow.py backend/tests/integration/test_publishing_handoff.py`
Final PR review required: yes
Goal: Track platform-first localization as a truthful human-reviewed handoff and preserve custom localized narration as an explicit fallback.
Dependencies: T081
Acceptance criteria: Localization handoff records source language, ordered target languages, preferred platform-auto-dubbing mode, provider/publish reference when present and a manual per-language acceptance state; no state claims an automatic-dubbing API call that the adapter does not support; reviewers can accept, reject or request changes while preserving the approved source export and decision history; rejected/unavailable platform localization can record a custom-dub fallback requirement plus artifact checksum and approved label without private absolute paths; publishing/localization status is exposed through API schemas and persisted in the export handoff; retries are idempotent and do not duplicate decisions or overwrite accepted artifacts; CoreWorkflowEngine and VoiceoverModule remain provider/platform neutral.
Test requirements: Add state-transition, per-language acceptance, unsupported transition, rejection preservation, changes-requested, custom-fallback checksum/redaction, idempotent retry, API serialization, artifact persistence and no-fabricated-auto-dub-result tests.
Parallelizable: no
Notes: Auto-dubbing availability and acceptance are manual handoff facts in this milestone; do not invent an undocumented platform endpoint.

<!-- M007 ENGLISH-FIRST YOUTUBE PRODUCTION TASKS EXTENSION END -->

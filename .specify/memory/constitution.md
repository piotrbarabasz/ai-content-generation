<!--
Sync Impact Report
- Version change: 0.1.0 → 1.0.0
- Modified principles: none → 10 principles defined for AI Content Studio
- Added sections: Architecture and Delivery Constraints, Development Workflow
- Removed sections: none
- Templates requiring updates: ✅ .specify/templates/spec-template.md, ✅ .specify/templates/plan-template.md, ✅ .specify/templates/tasks-template.md
- Follow-up TODOs: none
-->

# AI Content Studio Constitution

## Core Principles

### I. Modular Workflow First
The system MUST be designed as a configurable workflow engine, not as one fixed video generator. Features MUST be delivered as workflows that can enable or disable modules, swap providers and support multiple output types such as short video, long-form video, audio-only and script-only.

### II. Explicit Module Contracts
Every module MUST expose an input schema, output schema, config schema, dependencies, enabled/disabled behavior, retry policy, artifact outputs and error behavior. Modules MUST be usable both independently and as part of a larger workflow.

### III. Provider Abstraction
LLM, TTS, transcription, captions, rendering, assets, storage and publishing MUST be accessed through provider interfaces. Modules MUST depend on abstractions rather than hardcoded vendor implementations.

### IV. Artifact Traceability
Every workflow run MUST persist intermediate and final artifacts with metadata that includes artifact type, owning workflow run, module source, version and storage reference. The system MUST be able to inspect or replay a run from its artifacts.

### V. Review and Approval
Important stages such as script, scene plan, QA, voiceover, captions and render MUST support manual review or approval checkpoints. A workflow MUST allow a human to approve, reject or revise an artifact before continuing.

### VI. MVP Scope Discipline
The MVP MUST prove the modular workflow engine with short video and long-form script/voiceover workflows. The MVP MUST NOT include full publishing automation, advanced analytics, full billing, marketplace assets or complex collaboration.

### VII. Separation of Narrative and Render Models
NarrativeSegment and RenderScene MUST remain separate concepts. Narrative planning MUST NOT be collapsed into rendering decisions and vice versa.

### VIII. Testability
Modules MUST be independently testable with deterministic mock providers. Unit and integration tests MUST cover module contracts, enabled/disabled paths, retry behavior and artifact outputs.

### IX. No Hardcoded Local Paths
Modules MUST NOT depend on absolute filesystem paths or implicit local directories. Execution MUST use configuration and an artifact store abstraction for file resolution and persistence.

### X. Security and Secrets
Credentials, tokens, secrets and private runtime artifacts MUST NOT be committed. Runtime caches, temp files and secrets MUST be excluded from the repository and handled through environment-based or secret-managed configuration.

## Architecture and Delivery Constraints
The implementation MUST keep module orchestration explicit and observable. The system MUST define a shared workflow model for project, workflow run, generation job and artifact state. Provider adapters MUST be used for external systems, and artifact outputs MUST remain deterministic and inspectable.

## Development Workflow
All specs, plans and tasks MUST describe how a feature respects these principles. They MUST identify affected modules, providers and artifacts, include explicit MVP scope boundaries and define review checkpoints for any workflow stage that changes content or output. Implementation is not considered complete until tests cover module contracts, provider abstraction and artifact traceability.

## Governance
This constitution supersedes ad-hoc implementation choices for AI Content Studio. Proposals that remove or materially change a principle MUST update this constitution first, document the rationale and include a migration plan if existing workflows or modules are affected. Any change to a principle or governance rule MUST be reviewed against the affected spec, plan and tasks artifacts before implementation. Compliance review MUST verify module contracts, provider abstraction, artifact persistence, review checkpoints and MVP scope discipline.

**Version**: 1.0.0 | **Ratified**: 2026-07-06 | **Last Amended**: 2026-07-06

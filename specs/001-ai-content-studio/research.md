# Research: AI Content Studio MVP

## Decision: Use a Python-first modular workflow engine with explicit domain models

- Rationale: The spec and the existing repository insights both point to a workflow-oriented system rather than a single monolithic generator. A Python backend/core-first approach keeps the MVP implementable while preserving a clean separation between domain, workflow, modules, providers and infrastructure.
- Alternatives considered: A pure scripting approach coupled to the existing repo structure, or a full frontend-heavy application. Both were rejected because they would either preserve the current hardcoded behavior or expand scope beyond the MVP.

## Decision: Use local filesystem artifact storage behind a provider interface

- Rationale: The constitution requires artifact persistence without hardcoded local paths. A storage provider abstraction allows the MVP to start with local disk storage while keeping future cloud storage options open.
- Alternatives considered: Direct filesystem access from every module or a database-first approach. The direct approach would violate the constitution and increase coupling; a database-first approach would add unnecessary complexity for the MVP.

## Decision: Use deterministic mock providers for the first implementation slice

- Rationale: The clarified MVP decisions require mock providers first so the core workflow can run without external credentials. This also improves repeatability and testability.
- Alternatives considered: Immediate integration with real LLM/TTS/render providers. Rejected to keep scope strict and avoid provider-specific setup in the first slice.

## Decision: Use a synchronous-first execution model with job state tracking

- Rationale: The spec requires WorkflowRun and GenerationJob from the start, while the first implementation can run modules synchronously and locally. This supports the vertical slices without overbuilding an async queue.
- Alternatives considered: Queue-based execution from day one. Rejected because it adds operational complexity not required for the MVP.

## Decision: Keep the MVP focused on short video and long-form script plus voiceover

- Rationale: The constitution and spec explicitly limit the MVP to two workflow presets and the required review checkpoints. This focuses the implementation and avoids expanding into publishing, analytics or collaboration.
- Alternatives considered: Supporting all content types and presets immediately. Rejected because the MVP should prove the engine before expanding the surface area.

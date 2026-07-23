# Testing Checklist: AI Content Studio

**Purpose**: Validate that the plan and requirements define tests for module interfaces, workflow execution, disabled modules, storage, provider mocks and export bundles.
**Created**: 2026-07-06
**Feature**: [spec.md](../spec.md)

## Remediation Test Coverage

- [X] CHK011 Are tests required for ProviderRegistry registration and provider resolution by type and name? [Coverage, Spec FR-014a to FR-014c]
- [X] CHK012 Are tests required for disabled optional modules not requiring providers? [Coverage, Spec FR-022b]
- [X] CHK013 Are tests required for canonical WorkflowConfig enum validation and enabled/disabled module conflicts? [Coverage, Spec FR-011a to FR-011c]
- [X] CHK014 Are tests required for export bundle required files and conditional artifact references? [Coverage, Spec FR-043a, FR-043b]
- [X] CHK015 Are tests required for approval pause, approve, reject, request-changes and resume behavior? [Coverage, Spec FR-047a to FR-047g]
- [X] CHK016 Are tests required for long-form workflow execution with research enabled, research disabled and voiceover disabled? [Coverage, User Story 3]
- [X] CHK017 Are tests required to ensure missing usage metadata does not fail workflow execution? [Coverage, Spec FR-052a]
- [X] CHK018 Are static checks required for no real-looking API keys in committed config and placeholder-only sample env values? [Coverage, Spec NFR-009]

## Module and Workflow Tests

- [X] CHK001 Are unit tests required for module interface contracts and module registry registration? [Completeness, Plan]
- [X] CHK002 Are integration tests required for workflow execution using mock providers? [Completeness, Plan]
- [X] CHK003 Are tests required for disabled-module behavior and fallback/skip logic? [Coverage, Spec Â§FR-015, FR-019]
- [X] CHK004 Are tests required for workflow approval pauses and resumption after review? [Coverage, Spec Â§FR-046, FR-047]

## Provider and Storage Tests

- [X] CHK005 Are tests required for the artifact store abstraction and local filesystem implementation? [Completeness, Plan]
- [X] CHK006 Are deterministic mock provider tests required for the first implementation slice? [Completeness, Spec Â§FR-021, NFR-010]
- [X] CHK007 Are tests required for provider validation and configuration errors before run start? [Coverage, Spec Â§FR-022]

## Export and Artifact Tests

- [X] CHK008 Are tests required for export bundle contents and manifest generation? [Completeness, Spec Â§FR-041 to FR-043]
- [X] CHK009 Are tests required for artifact persistence metadata and traceability linkage to WorkflowRun and GenerationJob? [Completeness, Spec Â§FR-048 to FR-051]
- [X] CHK010 Are tests required for retry behavior and transient failure handling? [Coverage, Spec Â§FR-016, NFR-005]

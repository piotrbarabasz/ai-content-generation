# Testing Checklist: AI Content Studio

**Purpose**: Validate that the plan and requirements define tests for module interfaces, workflow execution, disabled modules, storage, provider mocks and export bundles.
**Created**: 2026-07-06
**Feature**: [spec.md](../spec.md)

## Remediation Test Coverage

- [ ] CHK011 Are tests required for ProviderRegistry registration and provider resolution by type and name? [Coverage, Spec FR-014a to FR-014c]
- [ ] CHK012 Are tests required for disabled optional modules not requiring providers? [Coverage, Spec FR-022b]
- [ ] CHK013 Are tests required for canonical WorkflowConfig enum validation and enabled/disabled module conflicts? [Coverage, Spec FR-011a to FR-011c]
- [ ] CHK014 Are tests required for export bundle required files and conditional artifact references? [Coverage, Spec FR-043a, FR-043b]
- [ ] CHK015 Are tests required for approval pause, approve, reject, request-changes and resume behavior? [Coverage, Spec FR-047a to FR-047g]
- [ ] CHK016 Are tests required for long-form workflow execution with research enabled, research disabled and voiceover disabled? [Coverage, User Story 3]
- [ ] CHK017 Are tests required to ensure missing usage metadata does not fail workflow execution? [Coverage, Spec FR-052a]
- [ ] CHK018 Are static checks required for no real-looking API keys in committed config and placeholder-only sample env values? [Coverage, Spec NFR-009]

## Module and Workflow Tests

- [ ] CHK001 Are unit tests required for module interface contracts and module registry registration? [Completeness, Plan]
- [ ] CHK002 Are integration tests required for workflow execution using mock providers? [Completeness, Plan]
- [ ] CHK003 Are tests required for disabled-module behavior and fallback/skip logic? [Coverage, Spec §FR-015, FR-019]
- [ ] CHK004 Are tests required for workflow approval pauses and resumption after review? [Coverage, Spec §FR-046, FR-047]

## Provider and Storage Tests

- [ ] CHK005 Are tests required for the artifact store abstraction and local filesystem implementation? [Completeness, Plan]
- [ ] CHK006 Are deterministic mock provider tests required for the first implementation slice? [Completeness, Spec §FR-021, NFR-010]
- [ ] CHK007 Are tests required for provider validation and configuration errors before run start? [Coverage, Spec §FR-022]

## Export and Artifact Tests

- [ ] CHK008 Are tests required for export bundle contents and manifest generation? [Completeness, Spec §FR-041 to FR-043]
- [ ] CHK009 Are tests required for artifact persistence metadata and traceability linkage to WorkflowRun and GenerationJob? [Completeness, Spec §FR-048 to FR-051]
- [ ] CHK010 Are tests required for retry behavior and transient failure handling? [Coverage, Spec §FR-016, NFR-005]

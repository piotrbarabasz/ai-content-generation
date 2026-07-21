# Workstream manifest schema

Workstream manifests are committed planning metadata for
`specs/001-ai-content-studio`. They must not contain runtime state, generated
output, credentials, or agent-maintained timestamps.

## Milestone manifest

Required fields:

```yaml
id: M###
title: string
status: planned | active | review | completed | blocked
goal: string
epics: [E###, ...]
completion_criteria:
  - string
```

## Epic manifest

Required fields:

```yaml
id: E###
title: string
milestone: M###
feature: specs/001-ai-content-studio
base_branch: string
branch: string
status: planned | active | review | completed | blocked
risk: low | medium | high | critical
depends_on: [E###, ...]
tasks: [T### | T###A, ...]
required_checks:
  - string
pr_policy:
  one_pr_per_epic: true
  merge_requires_human: true
commit_policy:
  one_commit_per_task: true
  commit_requires_human: true
```

## Validation rules

- IDs use `M###`, `E###`, and `T###` or `T###A` formats.
- Each epic must reference an existing milestone.
- A task may belong to at most one epic.
- Every `depends_on` value must reference an existing epic.
- An epic `branch` must not equal its `base_branch`.
- `feature` must resolve to `specs/001-ai-content-studio`.
- Milestone epic IDs must reference existing epics, and those epics must point
  back to the milestone.
- `required_checks` must be present and contain explicit checks.
- Status and risk values are restricted to the enumerations above.
- Merge, commit, push, and deployment remain human-controlled; no manifest
  field authorizes automation for those operations.

These files are static metadata. The single-task loop must not write runtime
state to this directory.

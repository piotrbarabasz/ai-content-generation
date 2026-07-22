# AI Content Studio

[![agent-system-validation](https://github.com/piotrbarabasz/ai-content-generation/actions/workflows/agent-system-validation.yml/badge.svg?branch=master)](https://github.com/piotrbarabasz/ai-content-generation/actions/workflows/agent-system-validation.yml)

AI Content Studio is a Python-first modular workflow engine for AI-assisted content production. The MVP focuses on two workflows:

- Short Video (`short_video`)
- Long-form Script + Voiceover (`long_form_script_voiceover`)

The first implementation slice establishes the backend foundation, domain models, workflow configuration validation, local development conventions and secret hygiene. Production provider integrations, workflow execution, modules and API endpoints are intentionally deferred to later tasks.

## Repository Layout

- `backend/app/` - backend package
- `backend/app/domain/` - domain models and validation
- `backend/app/api/` - API-facing schemas and future routes
- `backend/tests/` - unit, integration and static tests
- `docs/spec-kit/` - product, domain, module and preset source documents
- `docs/source-repo-insights/` - source repository analysis for shorts and long-form pipelines
- `specs/001-ai-content-studio/` - active feature specification, plan and tasks
- `.specify/workstreams/` - milestone and epic manifests grouping feature tasks

## Local Setup

Use Python 3.11 or newer.

```powershell
py -3 -m pip install -r backend/requirements.txt
py -3 -m pytest backend/tests
```

The tests are written with `unittest` and are also pytest-discoverable once pytest is installed.

## Configuration

Runtime configuration should come from environment variables or local config files that are excluded from version control. Use `.env.example` as a placeholder-only reference.

Do not commit credentials, API keys, generated artifacts or agent runtime state.

## Agent-assisted Spec Kit implementation

The project Codex workflow implements one Spec Kit task per explicit run. A read-only manager selects or validates the task, gates it on real dependencies and a clean task-specific baseline, and prepares a bounded package for implementation, validation and independent review.

Start the next dependency-ready task with:

```text
$speckit-loop next
```

Or request one exact task with:

```text
$speckit-loop T006
$speckit-loop T006A
```

The task is closed only after the reviewer returns `PASS` and confirms it is safe to close. The loop never starts another task automatically and does not commit, push or deploy changes. Review the complete diff and validation results before making any manual commit.

Delivery hierarchy is milestone -> epic -> task -> commit: an epic groups tasks on one branch and into one pull request, while each task remains an independent `$speckit-loop` run and a human-controlled commit.

Before running `$speckit-loop`, place the active epic ID (for example `E001`)
in the local ignored file `.specify/runtime/active-epic` and check out the branch
declared by that epic manifest. The loop validates this context before baseline
capture by running `validate_manifests()`, `validate_task_epic_consistency()`,
and `validate_active_epic()`, then never creates or switches branches.
If the guard fails, the loop stops immediately and does not start any agent.

Task IDs are exact uppercase identifiers matching `T\d{3}[A-Z]?`, for example `T006` or `T006A`.

After all tasks in an epic are complete, run `$speckit-epic-review` for a
read-only review of the full epic diff, commits, tests, acceptance criteria,
security and scope. It reports whether a human may create the PR; it never
creates, merges or pushes one.

When the review passes, the root orchestrator writes an ignored receipt at
`.specify/runtime/reviews/<EPIC_ID>.json` using the current `HEAD` SHA and the
current base SHA. A later commit or base-branch change invalidates that
receipt, so `$speckit-epic-pr` must re-check both SHAs before trusting it.

When the reviewed epic branch is already pushed, `$speckit-epic-pr` can create
only a draft PR after all safety gates pass. It never pushes, merges, enables
auto-merge or changes epic status; otherwise it prepares title and body for
manual use.

## Design References

- `docs/spec-kit/00-product-context.md`
- `docs/spec-kit/01-source-repo-synthesis.md`
- `docs/spec-kit/02-domain-model-draft.md`
- `docs/spec-kit/03-module-contracts-draft.md`
- `docs/spec-kit/04-workflow-presets-draft.md`
- `docs/spec-kit/05-mvp-boundary.md`
- `docs/spec-kit/06-analysis-remediation.md`
- `docs/source-repo-insights/shorts/repo-modular-pipeline-insights.md`
- `docs/source-repo-insights/shorts/repo-product-insights.md`
- `docs/source-repo-insights/long-form/repo-modular-pipeline-insights.md`
- `docs/source-repo-insights/long-form/repo-product-insights.md`

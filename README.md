# AI Content Studio

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

## Local Setup

Use Python 3.11 or newer.

```powershell
py -3 -m unittest discover -s backend/tests
```

The tests are written with `unittest` and are also pytest-discoverable once pytest is installed.

## Configuration

Runtime configuration should come from environment variables or local config files that are excluded from version control. Use `.env.example` as a placeholder-only reference.

Do not commit credentials, API keys, generated artifacts or agent runtime state.

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

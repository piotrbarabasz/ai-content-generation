# AI Content Studio

[![tests](https://github.com/piotrbarabasz/ai-content-generation/actions/workflows/tests.yml/badge.svg?branch=master)](https://github.com/piotrbarabasz/ai-content-generation/actions/workflows/tests.yml)

AI Content Studio is a Python backend for configurable content production:
short video and long-form script with optional voiceover. It contains domain
models, a content workflow engine, artifact storage, modular processing,
FastAPI endpoints, deterministic mocks and optional real TTS/publishing adapters.

The engine and modules work through direct application composition. The run API
currently stores status records without invoking the engine, and mock rendering
produces references rather than playable video. Completing that application path
is the first priority in [the product roadmap](docs/ROADMAP.md).

## Setup and tests

Use Python 3.11+; Python 3.11 is the CI and documented optional TTS baseline.
Create an isolated environment and activate it using your shell's normal command.
On Windows, `py -3.11 -m venv .venv` selects Python 3.11 explicitly; on Linux/macOS,
use `python3.11 -m venv .venv`. An existing `.venv-ci311` is also suitable.

From the repository root, with that environment's `python` active:

```sh
python -m pip install -e .
python -m pytest backend/tests
git diff --check
```

Alternatively, `scripts/setup-dev.ps1` or `sh scripts/setup-dev.sh` installs the
project and runs the test suite using the active Python. Setup does not install
Git hooks. CI performs checkout, Python setup, editable installation and pytest.
Default tests use mocks/fakes and require no provider credentials, GPU or models.

## API

The application entrypoint is `app.api.main:app`. To serve it locally, install
an ASGI server in the application environment, for example:

```sh
python -m pip install uvicorn
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

OpenAPI and interactive documentation are available at `/openapi.json` and `/docs`.
Projects, configurations, runs, approvals and localization records are currently
in-memory. Catalog discovery and cached voice-preview routes already work through
application services; real synthesis requires its separately configured runtime.
See [API and storage](docs/architecture/api-and-storage.md) for exact boundaries.

## Repository layout

- `backend/app/domain/` ? validated entities and configuration.
- `backend/app/workflow/` ? content execution, registries, presets and usage hooks.
- `backend/app/modules/` ? content-processing and export modules.
- `backend/app/providers/` ? protocols, mocks and optional adapters.
- `backend/app/storage/` ? artifact persistence and manifests.
- `backend/app/tts/` ? narration, cache, preview and selection services.
- `backend/app/api/` ? HTTP schemas, routes and service dependencies.
- `backend/app/tooling/` ? TTS smoke and provider comparison commands.
- `backend/tests/` ? unit, integration and product static checks.
- `scripts/` ? developer setup and explicit TTS runtime operations.
- `experiments/tts_local/` ? isolated experiments outside the production catalog.
- `docs/architecture/` ? current architecture; `docs/archive/` ? historical evidence.

## TTS and publishing

Chatterbox Multilingual V3 and curated Piper voices remain available behind the
existing TTS contract. XTTS-v2 remains evaluation-only. Technical chunking,
interruption/resume, PCM validation, benchmarks and provider-neutral tempo are
implemented. Preview cache defaults to ignored `.runtime/tts-previews`.

Optional runtimes stay in separate environments. Follow the documented setup
rather than installing heavy model packages into the base test environment:

- [Runtime profiles](docs/tts/RUNTIME_PROFILES.md)
- [Chatterbox setup](docs/tts/CHATTERBOX_SETUP.md)
- [Piper setup](docs/tts/PIPER_SETUP.md)
- [TTS catalog and voice preview API](docs/tts/TTS_SELECTION_API.md)
- [YouTube publishing/localization handoff](docs/publishing/YOUTUBE_HANDOFF.md)

## Development

Read [AGENTS.md](AGENTS.md), inspect existing code, implement one coherent change
and run its tests plus full pytest. No development orchestration system or task
metadata is required. Keep source language separate from downstream localization,
preserve artifacts and review history, and isolate providers behind contracts.

Use `.env.example` only as a placeholder reference; settings must be wired through
explicit runtime configuration. Never commit secrets, private voices, weights,
caches or generated outputs. Existing ignored data from retired tooling remains
private and is not consumed by the application.

See [documentation index](docs/INDEX.md), [architecture](docs/architecture/overview.md),
[roadmap](docs/ROADMAP.md) and [historical task audit](docs/archive/legacy-task-audit.md).

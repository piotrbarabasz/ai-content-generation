# AI Content Studio

[![tests](https://github.com/piotrbarabasz/ai-content-generation/actions/workflows/tests.yml/badge.svg?branch=master)](https://github.com/piotrbarabasz/ai-content-generation/actions/workflows/tests.yml)

AI Content Studio is a desktop-first Windows application for building narrated
videos. The PySide6 editor supports project and script editing, scene/storyboard
work, audio controls, timeline editing, preview and export. Its local project data
uses SQLite and files; application services keep provider and media work isolated.

## What works today

- Create and reopen project workspaces; edit, split, merge and order narrative
  sections while retaining revisions.
- Plan scenes, edit visual prompts, import or generate images, and manage selected
  visuals through the editor.
- Generate section audio through supported optional runtimes, edit a timeline,
  preview scenes, and render playable MP4 with FFmpeg.
- Run deterministic mock workflows and the offline automated tests without AI
  credentials, GPU access or model downloads.

The production pipeline is still under acceptance. In particular, the complete
installed Windows workflow, signed installer and clean-machine human acceptance
remain release gates. AI generated output also depends on optional providers and
their configuration. See the current state in [architecture docs](docs/architecture/overview.md)
and the [MVP acceptance record](docs/desktop/D025_MVP_ACCEPTANCE.md).

## Install and run

Use Python 3.11 or newer. From the repository root, create and activate a virtual
environment, then install development dependencies:

```sh
python -m venv .venv
# Activate .venv using your shell's command, then:
python -m pip install -e ".[dev]"
```

Start the desktop editor with:

```sh
python -m app.desktop
```

Or install the `ai-content-studio` command entry point through the editable
installation. On Windows, the project also maintains an installer build workflow;
see [D041](docs/desktop/D041_WINDOWS_INSTALLER.md).

Run the deterministic offline suite and whitespace check:

```sh
python -m pytest backend/tests
git diff --check
```

Qt tests use the offscreen platform in headless environments. Tests do not require
credentials, external models or GPU access. FFmpeg based rendering acceptance uses
local media tools and synthetic fixtures; provider smoke tests are separate.

## Optional providers and API

The desktop can use optional OpenAI text/image APIs and local image and TTS
runtimes. API keys are read through explicit environment configuration; for
example, OpenAI features require `OPENAI_API_KEY`. Local Chatterbox, Piper and
image runtimes have separate setup and hardware requirements. XTTS remains
evaluation-only. Installing the base package or dev extra does not install model
weights or optional heavyweight runtimes.

- [Environment configuration](docs/desktop/ENVIRONMENT_CONFIGURATION.md)
- [Runtime profiles](docs/tts/RUNTIME_PROFILES.md)
- [Chatterbox setup](docs/tts/CHATTERBOX_SETUP.md)
- [Piper setup](docs/tts/PIPER_SETUP.md)
- [YouTube publishing handoff](docs/publishing/YOUTUBE_HANDOFF.md)

FastAPI is an optional adapter for integration and automation. Install the `api`
extra and an ASGI server such as uvicorn to run it locally:

```sh
python -m pip install -e ".[api]" uvicorn
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

The API is not required by the desktop. Some API metadata endpoints remain
in-memory and run status does not execute the workflow engine; see
[API and storage boundaries](docs/architecture/api-and-storage.md).

## Project guidance

The [implementation plan](docs/desktop/IMPLEMENTATION_PLAN.md) is the authoritative
D### backlog; [roadmap](docs/ROADMAP.md) provides milestone navigation. Follow
[AGENTS.md](AGENTS.md) when changing code. The [architecture docs](docs/architecture/overview.md)
describe inspected behavior. Historical task evidence remains in `docs/archive/`.

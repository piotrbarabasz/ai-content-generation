# Architecture overview

AI Content Studio's accepted direction is a desktop-first Windows application
using PySide6 and UI-independent Python services. See
[ADR 0003](../decisions/0003-desktop-first-architecture.md) and the authoritative
[desktop implementation plan](../desktop/IMPLEMENTATION_PLAN.md).

This page describes the current Python foundation: a reusable execution engine,
deterministic modules and mocks, local artifact storage, optional real TTS adapters
and a YouTube publishing boundary. The desktop editor, SQLite project store and
real MP4 renderer are planned, not implemented capabilities.

## Code boundaries

| Area | Responsibility |
| --- | --- |
| `backend/app/domain/` | Validated entities, source-language and export configuration, approval and localization state |
| `backend/app/workflow/` | Module/preset registries, ordered execution, dependencies, retries, approval pause/resume and usage hooks |
| `backend/app/modules/` | Brief, research, dossier, outline, script, QA, scenes, narration, captions, rendering and export |
| `backend/app/providers/` | Protocols, registry, mocks, lazy adapters, settings and composition factories |
| `backend/app/storage/` | Artifact interface, local bytes and JSON sidecar manifests |
| `backend/app/tts/` | Chunking, cache identity, PCM assembly, benchmarks, tempo, discovery, previews and selection mapping |
| `backend/app/api/` | FastAPI routes, camelCase schemas and application dependencies |
| `backend/app/tooling/` | Human-operated TTS smoke and provider comparison commands |
| `experiments/tts_local/` | Isolated experiments; never production catalog discovery inputs |

The runtime imports no development orchestration package and requires no epic,
task metadata or agent receipt. Use [AGENTS.md](../../AGENTS.md) for short working
instructions and [ROADMAP](../ROADMAP.md) for milestone navigation. D### task
definitions exist only in the desktop plan.

## Current capabilities and limits

- The engine and modules execute directly in process. The long-form integration
  tests demonstrate artifact-producing workflows with deterministic providers.
- Two preset definitions exist: `short_video` and `long_form_script_voiceover`.
  The short preset currently omits `scriptGeneration`, which its scene module
  requires; its canonical composition needs correction before real end-to-end use.
- API project/config/run metadata lives in module-level dictionaries. Starting
  or resuming a run updates status but does not invoke `CoreWorkflowEngine`.
  The API export endpoint constructs metadata; `ExportModule` performs actual
  artifact packaging when called through application code.
- The deterministic mock remains the offline/default LLM. An opt-in OpenAI
  Responses adapter provides strict structured script and visual-prompt output.
  Mock transcription, captions, assets and rendering continue to prove their
  contracts; the mock renderer returns a reference rather than playable video.
- Chatterbox V3 and Piper are optional real TTS adapters. XTTS-v2 is evaluation-only.
  Narration bytes, resumable chunks, cache integrity, previews and tempo already
  exist. Catalog discovery needs no model installation.
- Publishing has a mock, an optional YouTube transport and an approval-gated
  application boundary. Localization is a manual handoff; it is not an automatic
  dubbing API. HTTP mutations of handoff decisions are currently in-memory only.

## Engineering invariants

Keep concrete providers behind protocols and composition factories. The workflow
engine and `VoiceoverModule` must not select vendors. Disabled optional modules
must not require their providers. Keep `NarrativeSegment`, `RenderScene` and
technical TTS chunks distinct. Persist real narration bytes through artifact
storage and validate WAV data before declaring completion. Rejection preserves
reviewed artifacts and decision history. Resolve paths through configured stores;
never hardcode machine-specific locations or expose private paths in public data.

Tests use deterministic fakes and temporary stores. Default installation must not
load optional models, use GPUs, download weights or contact providers. Real TTS and
publishing runs use explicitly configured optional runtimes. Keep secrets, model
weights, private reference audio and generated outputs outside Git.

## Windows desktop distribution

The D041 product build is a Nuitka standalone application wrapped by a per-user
Inno Setup installer. Core, desktop, API and development dependency groups are
separate. Installed code resolves pinned FFmpeg/ffprobe beside the executable;
source runs may use PATH. Mutable runtimes, models, caches and logs live under the
user's local application-data directory, never under the installation or inside
project workspaces. The default audio composition only attaches an already active,
verified D008 Piper runtime and never provisions or downloads one at startup.

Exact binary inventory, component provenance, notices and signing outcome are
release artifacts. Unsigned builds are test-only. See
[D041 Windows installer](../desktop/D041_WINDOWS_INSTALLER.md) for the build and
clean-machine acceptance boundary.

## Documentation

- [Domain model](domain-model.md)
- [Provider contracts](provider-contracts.md)
- [Module contracts](module-contracts.md)
- [Workflow execution](workflow-engine.md)
- [API and storage](api-and-storage.md)
- [TTS selection API](../tts/TTS_SELECTION_API.md)
- [Publishing handoff](../publishing/YOUTUBE_HANDOFF.md)
- [Windows installer](../desktop/D041_WINDOWS_INSTALLER.md)
- [Architecture decisions](../INDEX.md#architecture-decisions)

These pages describe inspected code, replacing early design drafts. Historical
assumptions and remaining gaps are recorded in the [task audit](../archive/legacy-task-audit.md).

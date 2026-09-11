# ADR 0003: Desktop-first AI Content Studio

## Status

Accepted

## Context

The product is becoming a local video production editor: users edit narrative
sections, generate local speech, assign visuals to scenes and export a film.
Large local files, long-running model inference, offline project recovery and
selective regeneration are central requirements. An HTTP-first workflow is not
the primary user experience for this product.

The repository already has reusable Python domain models, a provider-neutral
workflow engine, artifact manifests and substantial TTS infrastructure. It does
not yet have a desktop editor, durable editable projects or a real MP4 renderer.
Current API routes and mock outputs must not be mistaken for those capabilities.

## Decision

- Build a desktop-first Windows application using PySide6/Qt. Start with Qt
  Widgets, Qt Multimedia and Timeline Lite, not a professional multitrack editor.
- Keep Python application services independent of presentation. Project, editing,
  generation, job coordination and invalidation operations are shared core logic.
- Store project metadata in SQLite and large artifacts in files. Use stable
  identities, immutable revisions, explicit active selections and relative keys.
- Regenerate only artifacts whose actual inputs changed. Preserve old variants
  and prevent an old job from replacing a newer edit.
- Use a local durable job queue and isolated processes for heavy models and
  FFmpeg. Do not introduce Redis, Celery or a distributed scheduler for the MVP.
- Manage AI runtimes separately from the application. Download versioned models
  on demand. Initially allow one active GPU operation across the application.
- Render a versioned timeline description with FFmpeg into a real MP4. A full
  final encode may be repeated without regenerating unaffected AI outputs.
- Retain FastAPI as an optional adapter for integrations, automation and future
  web use. The desktop does not require a local HTTP server.
- Deliver a Windows installer with a bundled application runtime and managed
  optional AI runtimes. Do not require a system Python or ship a copied dev venv.

This decision changes product orientation, not the provider-neutral execution
principles of [ADR 0001](0001-modular-workflow-engine.md). The source/localization
separation and existing English production default in
[ADR 0002](0002-english-first-localization-boundary.md) remain in force. A selected
Polish Piper profile can validate the desktop path without silently changing
that default or claiming unsupported English voices.

## Alternatives considered

| Alternative | Assessment |
| --- | --- |
| PySide6 with Python services and isolated workers | Selected: reuses the existing language and core, fits local files and media, and adds the smallest new application stack. Packaging and playback require an early Windows spike. |
| Tauri with Python over IPC or local HTTP | Viable second choice, especially if sharing a web UI becomes a near-term priority. Adds frontend/Rust tooling and transport/media-access boundaries without solving Python/GPU packaging. |
| Electron with Python | Viable for a team with Electron expertise or a concrete Chromium requirement. Adds a browser runtime and another process/security boundary to maintain. |
| Classic web application with FastAPI | Better for collaboration and centrally hosted computation. Local models still need a local component; remote execution adds large transfers and operating costs. |

No alternative is rejected permanently. Reconsideration requires concrete product
or deployment evidence and a new explicit decision, not incidental task scope.

## Consequences

### Positive

- Existing TTS adapters, selection, caching, PCM validation and resume remain useful.
- Editing and regeneration map to individual sections/scenes rather than full runs.
- Users own a recoverable local project with inspectable media and retained variants.
- A UI-independent core keeps future API and server adapters possible.
- Separating runtime/model delivery keeps the base application manageable.

### Negative

- The project must support Windows installers, media plugins, worker lifecycle,
  runtime compatibility and device failures.
- Qt UI cannot be reused directly as a browser UI.
- SQLite/file publication requires explicit crash recovery; immutable variants
  require storage accounting and eventual garbage collection.
- GPU compatibility, model licenses and runtime downloads remain release work.
- The existing API-first backlog must be reordered; a desktop label alone does
  not make the current backend a usable editor.

The [implementation plan](../desktop/IMPLEMENTATION_PLAN.md) is the authoritative
desktop backlog. This ADR records the decision, not completed implementation.

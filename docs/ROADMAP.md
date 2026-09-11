# Product roadmap

AI Content Studio is desktop-first. The accepted direction is PySide6/Qt,
UI-independent Python application services, SQLite plus immutable media files,
selective regeneration, local workers and FFmpeg.

[ADR 0003](decisions/0003-desktop-first-architecture.md) records the decision.
[Desktop IMPLEMENTATION_PLAN](desktop/IMPLEMENTATION_PLAN.md) is the **single
source of truth for task definitions, dependencies, priorities and acceptance**.
This page provides milestone navigation only. It replaces the previous API-first
ordering; it does not duplicate that backlog or claim desktop features already exist.

## First implementation steps

Start with D001, stable narrative sections and immutable revisions. Perform D002,
the packaged PySide6/media/worker spike, immediately afterward. Then follow the
plan's dependency-ordered P0 sequence. One explicitly selected D### task is
implemented and reviewed at a time; milestone numbers do not establish readiness.

## Milestone direction

| Milestone | Product outcome |
| --- | --- |
| M1 — Editable project foundation | Stable sections, immutable revisions, SQLite and safe artifacts |
| M2 — Local execution foundation | Durable jobs, worker protocol and managed CPU runtime |
| M3 — Audio pipeline | Piper voice download, section WAV, tempo and measured boundaries |
| M4 — Script and scene pipeline | Structured sections, semantic scenes and editable prompts |
| M5 — Visual pipeline | Imported images, generation contract and variants |
| M6 — Timeline and rendering | Timeline snapshots, real MP4 and coherent preview |
| M7 — Desktop editor | Project/section/audio/scene panels and Timeline Lite |
| M8 — Regeneration and recovery | Selective rebuilds and protection from obsolete job results |
| M9 — Installable MVP | Early packaging proof and complete installed-Windows acceptance |
| M10 — Production AI integrations | Real LLM/images, optional local image runtime, GPU/Chatterbox, references and alignment/captions |
| M11 — Product durability and optimization | History, storage cleanup, safe updates and measured rendering improvements |
| M12 — Optional integrations and output expansion | FastAPI, source-grounded research, review, exports, publishing and additional assets |

The exact milestone membership and all task details live in the
[implementation plan](desktop/IMPLEMENTATION_PLAN.md#milestones). Some work crosses
milestone ordering: packaging is tested early, and revision-aware publication is
required before real generation. Foundational job/persistence work is not deferred
until after API delivery.

## MVP boundary

An installed Windows application lets a user create/open a project, enter a topic
or their own text, edit structured sections, generate real local speech, plan
scenes, edit prompts, import or generate images, inspect Timeline Lite/preview
and export a playable MP4. After closing/reopening, editing one section rebuilds
only actual dependencies and preserves other generated media. A new final encode
is permitted without rerunning unaffected AI generation.

The initial verified CPU path can explicitly select a supported Polish Piper voice;
this does not change the existing English production default or source/localization
boundary. Manual text and images make the app useful before real AI integrations.
Mock content is labeled and does not count as production-quality generation.

See the [complete MVP acceptance boundary](desktop/IMPLEMENTATION_PLAN.md#mvp-definition).
The desktop editor and real renderer are still planned, not existing capabilities.

## Preserved later work

FastAPI stays an optional adapter to the same application services. Legacy preset
compatibility, durable review, real bundle downloads, supplied-source research,
reference voices, alignment/captions, publishing/localization and thumbnails are
retained in the plan rather than discarded. Optional video intake and local image
models have separate tasks; advanced editing and live collaboration remain deferred.

The plan's [backlog provenance](desktop/IMPLEMENTATION_PLAN.md#backlog-provenance-and-deferred-scope)
accounts for the previous 37 analysis items and the former roadmap's useful work.
Existing TTS providers, catalog/selection, preview/cache, resume, WAV validation,
tempo and provider-neutral modules are reused, not replaced by a new framework.

[Current architecture](architecture/overview.md) describes implemented behavior;
[historical task audit](archive/legacy-task-audit.md) records retired task evidence.
No Spec Kit, coding-agent graph or runtime task orchestration is introduced.

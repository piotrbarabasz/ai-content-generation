# Product roadmap

AI Content Studio is desktop-first. The implemented application uses PySide6/Qt,
UI-independent Python services, SQLite project workspaces, files, local workers
and FFmpeg. [ADR 0003](decisions/0003-desktop-first-architecture.md) records the
accepted direction.

[Desktop IMPLEMENTATION_PLAN](desktop/IMPLEMENTATION_PLAN.md) is the single
source of truth for task definitions, dependencies, priorities and acceptance.
This page provides milestone navigation. Implemented code and remaining
acceptance gates are described in [architecture docs](architecture/overview.md)
and the linked task records.

## Milestone direction

| Milestone | Product outcome |
| --- | --- |
| M1 | Editable project foundation |
| M2 | Local execution foundation |
| M3 | Audio pipeline |
| M4 | Script and scene pipeline |
| M5 | Visual pipeline |
| M6 | Timeline and real MP4 rendering |
| M7 | Desktop editor |
| M8 | Regeneration and recovery |
| M9 | Installable MVP and clean-Windows acceptance |
| M10 | Production AI integrations and optional runtimes |
| M11 | Product durability and optimization |
| M12 | Optional integrations and output expansion |

The editor and real renderer are implemented, and D025 automated acceptance is
green. The MVP is not yet accepted for release: signed-installer and clean-Windows
human-operated workflow evidence remain outstanding. See the
[complete MVP acceptance boundary](desktop/IMPLEMENTATION_PLAN.md#mvp-definition)
and [D025 evidence](desktop/D025_MVP_ACCEPTANCE.md).

## Preserved later work

FastAPI remains an optional adapter. Optional providers, reference voices,
alignment/captions, publishing/localization and other output expansion remain
tracked in the implementation plan. The roadmap is navigation, not a second
backlog. Historical evidence is retained in [docs/archive](archive/).

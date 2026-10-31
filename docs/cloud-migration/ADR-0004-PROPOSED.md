# ADR-0004 — Cloud-first/hybrid z lokalnym edytorem

Status: **PROPOSED**, 2026-10-28. Decydent: właściciel projektu; decyzja nieudzielona.

## Kontekst

ADR-0003 przyjął desktop-first, lokalne projekty SQLite i Windows/PySide6. Nowa wizja zakłada tygodniowy long-form około 10 minut i trzy pochodne Shorts, prywatny dashboard, trwałe artefakty i opcjonalne GPU finishing. Kod ma wartościowe usługi mediów, lecz API jest procesowe, a automatyzacja zależy od drivera edytora.

## Proponowana decyzja

Po akceptacji kierunek cloud-first/hybrid zastępuje produktowe pierwszeństwo desktopu z ADR-0003. Zachowuje Python core, provider ports, revision/artifact identity, approval history, edytor i workspace format. UI/API: jeden FastAPI/Jinja2/HTMX service za IAP. Batch: jeden resumable Cloud Run Job i Scheduler. Firestore: control plane. GCS: immutable bytes i snapshoty. Windows headless worker: neural image upscale i FFmpeg. Publishing centralnie z owner OAuth, private-first i approval. Jedno środowisko GCP oraz lokalne development/test, bez DEV/PROD promotion.

## Konsekwencje i alternatywy

Potrzebne są headless composition, checkpointy, budget ledger, fencing, handoff i upload reconciliation. Prywatny SQLite ogranicza refactoring, kosztem materializacji/snapshotów i braku live multiwriter editor. Cloud SQL i task-per-stage pozostają opcjami po pomiarze. Desktop-only nie realizuje unattended cloud production; rewrite/microservices zwiększają ryzyko i koszt.

## Warunki przyjęcia

Zapisać decydenta, datę i zakres zaakceptowanych CA-01–CA-17. Następnie utworzyć accepted `docs/decisions/0004-cloud-first-hybrid-architecture.md` i zaktualizować AGENTS, overview, ROADMAP i indeksy. ADR-0003 zachować z superseded-by, bez przepisywania historii. D### pozostaje backlogiem desktopu; CLOUD po akceptacji staje się backlogiem migracji. Implementować jedno wybrane zadanie. Scheduling, paid operations i publikacja wymagają osobnych bramek.

Bez akceptacji obowiązujący kierunek się nie zmienia.

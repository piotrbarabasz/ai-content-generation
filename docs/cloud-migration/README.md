# ARCH-001 — AI Content Studio: migracja cloud-first / hybrid

**Status:** `PROPOSED / REVIEW REQUIRED` — wyłącznie dokumentacja, bez zmiany kodu i infrastruktury.

**Data audytu:** 2026-10-31. **Źródło:** dostarczony pakiet sześciu dokumentów (pliki z sufiksem `(1)`), zachowany z historii PR #111. Integracja i korekty planu w lokalnym checkout na bazie `d745817`; pierwotne odniesienie do Tripse nie było ponownie weryfikowane.

**Granica dowodowa:** pierwotny audyt obejmował wybrane pliki zdalne; integracja potwierdza lokalnie główne granice workflow/driver, API, SQLite i proxy. Nie jest to pełny audyt importów ani odbiór produkcji. `CONFIRMED` oznacza dowód z kodu, `PROPOSED` rozwiązanie planowane, `VERIFY` niezweryfikowaną gotowość lub lukę. Testy dokumentacji nie dowodzą gotowości cloud.

## Cel i decyzja kierunkowa

Przekształcić rozbudowany edytor PySide6 w **minimalny system automatycznej produkcji**, bez kopiowania edytora do przeglądarki. Pierwszy wynik: powtarzalne utworzenie skryptu, narracji, obrazów, scen i poprawnego timeline'u, zakodowanie **MP4 proxy 640×360/25 fps**, zachowanie oryginalnych mediów i kompletnego pakietu produkcyjnego. Dopiero później scheduler, lokalny worker GPU, Shorts i YouTube.

**Rekomendowany kształt:** modułowy monolit Python + FastAPI; React/TypeScript/Vite jako statyczny panel zbudowany do tego samego obrazu Cloud Run Service; osobny Cloud Run Job do długiej produkcji; Firestore do metadanych i kontroli stanu, GCS do źródeł i checkpointów; lokalny GPU finisher na Windows jako niezależny klient pakietu. **To rekomendacje do zatwierdzenia, nie wdrożone komponenty.**

## Mapa dokumentów

| Dokument | Zakres |
| --- | --- |
| [CURRENT_STATE_AUDIT.md](CURRENT_STATE_AUDIT.md) | Fakty z kodu, klasyfikacja modułów, zależności, ryzyka i dowody testów |
| [PRODUCT_SIMPLIFICATION.md](PRODUCT_SIMPLIFICATION.md) | Minimalizacja produktu; panel desktop vs ważne mechanizmy integralności; kandydaci do wygaszenia |
| [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md) | Warstwy, data flow, granica Cloud/Local, kontrakt pakietu, bezpieczeństwo i deployment |
| [MIGRATION_BACKLOG.md](MIGRATION_BACKLOG.md) | Zadania, zależności, testy, kryteria odbioru, pionowe przekroje MVP |
| [OPEN_DECISIONS.md](OPEN_DECISIONS.md) | Propozycje decyzji architektonicznych i akceptacje właściciela |

## Kolejność wdrożenia — PROPOSED

Headless lokalnie → kompletny offline package i real proxy → jedno środowisko GCP + persistent job → ręcznie zatwierdzony, budget-limited OpenAI smoke przez CLI/API. `UI-001` może nastąpić niezależnie; React nie blokuje smoke. Storage rośnie od manifestu, przez basic GCS i persistent metadata, po lease/checkpoint/recovery hardening przed unattended scheduler. Krótki syntetyczny smoke (30–90 s materiału) jest oddzielony od droższego pełnego ~10-min acceptance; pełna generacja nie jest wymagana w regularnym CI.

## Najważniejsze fakty — CONFIRMED z zastrzeżeniami audytu

1. `app.application.automatic_workflow.AutomaticWorkflow` już deleguje operacje do `driver`, ale `DesktopPipelineDriver` czyta stan paneli Qt i wywołuje ich metody odświeżania. Wydzielenie headless composition jest głównym zadaniem `CORE-001`.
2. `FFmpegRenderer(proxy=True)` koduje faktyczne MP4 640×360 (landscape) lub 360×640 (portrait) z wymaganym `OutputTimebase(1,25)`; **nie oznacza** to, że OpenAI Images może natywnie generować obrazy 640×360.
3. Obecne `/api/v1/workflow-runs` przechowuje stan w pamięci i **nie uruchamia produkcji**. Nie należy eksponować go w chmurze jako gotowego API zadań.
4. `ProjectRepository` i `JobRepository` pracują na jednym lokalnym projekcie i SQLite z blokadą EXCLUSIVE. Nie są rozproszoną bazą wieloinstancyjną.
5. `ShortsService` wiąże wybór i montaż Shorts ze zweryfikowanym finalnym source MP4 oraz jego identyfikacją/timingiem; koncepcja „Shorts z proxy” wymaga osobnego spike'u i kontraktów.
6. Istnieją: historię rewizji, fingerprinty wejść, kontrolę publikacji, walidację timingów, odzyskiwanie odpowiedzi TTS i mechanizmy lokalnego FFmpeg. **Minimalizujemy UI, nie te gwarancje.**

## Status planów i ADR

[ADR 0003](../../docs/decisions/0003-desktop-first-architecture.md) pozostaje `Accepted`, a [desktop/IMPLEMENTATION_PLAN.md](../../docs/desktop/IMPLEMENTATION_PLAN.md) pozostaje autorytatywnym backlogiem D### dla desktopu. Niniejsze `ARCH-001` jest **propozycją nowego planu migracji**: po zatwierdzeniu nowego ADR będzie autorytatywne **wyłącznie dla zadań CLOUD/CORE/STORAGE/API/UI/LOCAL/...**. Nie usuwać ani nie nadpisywać starego planu. Zadanie `ADR-001` w backlogu formalizuje supersession, bez działania wstecz.

## Jak używać dokumentacji

1. Właściciel akceptuje albo modyfikuje decyzje w [OPEN_DECISIONS.md](OPEN_DECISIONS.md).
2. W następnym PR powstaje osobny ADR z migracją i jasnymi granicami dotychczasowej decyzji.
3. Wykonywać pojedyncze zadania z [MIGRATION_BACKLOG.md](MIGRATION_BACKLOG.md), zaczynając od `CORE-001A`; każda zmiana ma osobne testy i kontrolę kompatybilności.
4. Po każdym etapie aktualizować fakty z testów i realnych deployów; **nie traktować stanu projektowanego jako zaimplementowanego**.

## Weryfikacja zakresu ARCH-001

Zintegrowano dostarczone sześć dokumentów i uzgodnione korekty planu. Nie wykonywano refaktoryzacji, płatnych żądań API, migracji danych, operacji IAM/Terraform ani wdrożeń. Real OpenAI calls pozostają wyłączone.

- Sześć kanonicznych plików pakietu, brak kopii z sufiksem `(1)`; cztery wcześniejsze dokumenty zachowane.
- 38 lokalnych odnośników rozwiązuje się poprawnie; nagłówki Markdown i pięć bloków Mermaid mają poprawne fences. Nie wykonywano renderowania Mermaid.
- 23 zadania w tabelach; graf zależności acykliczny, bez zależności paid smoke od UI; recovery hardening i pełny acceptance wymagane przed schedulerem.
- Python 3.11.9, istniejący izolowany `.venv`, bez instalowania zależności. Testy skoncentrowane na repozytorium/timeline/long-form/regeneration/QA: **84 passed** (37.30 s).
- `git diff --check` bez błędów; wynik pełnego `python -m pytest backend/tests` zostanie podany w PR integracji. Testy aplikacji nie stanowią odbioru cloud ani real OpenAI.

## Wcześniejsze dokumenty i historia

Pakiet ARCH-001 obejmuje dokładnie sześć kanonicznych plików z mapy powyżej (łącznie z tym README). Sześć kopii z sufiksem `(1)` znormalizowano do tych nazw. Zachowano cztery wcześniejsze dokumenty: [MIGRATION_PLAN](MIGRATION_PLAN.md), [ARCHITECTURAL_DECISIONS](ARCHITECTURAL_DECISIONS.md), [DEPLOYMENT_AND_COSTS](DEPLOYMENT_AND_COSTS.md), [ADR-0004-PROPOSED](ADR-0004-PROPOSED.md). Wraz z sześcioma plikami ARCH-001 katalog zawiera dziesięć dokumentów. Wcześniejsze plany są kontekstem historycznym, a nie równoległym backlogiem ARCH-001; ich identyfikatory CLOUD nie odpowiadają nowym zadaniom. Projekt ADR pozostaje PROPOSED i nie jest zmieniany ani akceptowany przez tę integrację.

Wcześniejszy README (commit `ed92da4`) raportował testy: 2674 passed, 12 skipped, 1 failed (`test_d078_cloud_measurements_drive_duration_repair_without_images`: `ValueError: Section synthesis incomplete; valid chunks remain available for retry.`). To historyczny baseline, nie wynik bieżącej integracji; nie naprawiamy kodu w tym zadaniu.

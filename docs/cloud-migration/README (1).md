# ARCH-001 — AI Content Studio: migracja cloud-first / hybrid

**Status:** `PROPOSED / REVIEW REQUIRED` — wyłącznie dokumentacja, bez zmiany kodu i infrastruktury.

**Data audytu:** 2026-10-31. **Źródło:** `piotrbarabasz/ai-content-generation`, domyślna gałąź `master` (inspekcja zdalna przez GitHub); odniesienie `piotrbarabasz/tripse-ai-demo`, `master`.

**Granica dowodowa:** analiza wybranych rzeczywistych plików i testów; brak pełnego checkoutu/drzewa repozytorium oraz uruchomienia całej aplikacji w tym środowisku. Ustalenia potwierdzone konkretnym plikiem są oznaczane `CONFIRMED`, projektowane rozwiązania `PROPOSED`, luki `VERIFY`.

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

## Najważniejsze fakty

1. `app.application.automatic_workflow.AutomaticWorkflow` już deleguje operacje do `driver`, ale `DesktopPipelineDriver` czyta stan paneli Qt i wywołuje ich metody odświeżania. Wydzielenie headless composition jest głównym zadaniem `CORE-001`.
2. `FFmpegRenderer(proxy=True)` koduje faktyczne MP4 640×360 (landscape) lub 360×640 (portrait) z wymaganym `OutputTimebase(1,25)`; **nie oznacza** to, że OpenAI Images może natywnie generować obrazy 640×360.
3. Obecne `/api/v1/workflow-runs` przechowuje stan w pamięci i **nie uruchamia produkcji**. Nie należy eksponować go w chmurze jako gotowego API zadań.
4. `ProjectRepository` i `JobRepository` pracują na jednym lokalnym projekcie i SQLite z blokadą EXCLUSIVE. Nie są rozproszoną bazą wieloinstancyjną.
5. `ShortsService` wiąże wybór i montaż Shorts ze zweryfikowanym finalnym source MP4 oraz jego identyfikacją/timingiem; koncepcja „Shorts z proxy” wymaga osobnego spike'u i kontraktów.
6. Istnieją: historię rewizji, fingerprinty wejść, kontrolę publikacji, walidację timingów, odzyskiwanie odpowiedzi TTS i mechanizmy lokalnego FFmpeg. **Minimalizujemy UI, nie te gwarancje.**

## Status planów i ADR

[ADR 0003](https://github.com/piotrbarabasz/ai-content-generation/blob/master/docs/decisions/0003-desktop-first-architecture.md) pozostaje `Accepted`, a [desktop/IMPLEMENTATION_PLAN.md](https://github.com/piotrbarabasz/ai-content-generation/blob/master/docs/desktop/IMPLEMENTATION_PLAN.md) pozostaje autorytatywnym backlogiem D### dla desktopu. Niniejsze `ARCH-001` jest **propozycją nowego planu migracji**: po zatwierdzeniu nowego ADR będzie autorytatywne **wyłącznie dla zadań CLOUD/CORE/STORAGE/API/UI/LOCAL/...**. Nie usuwać ani nie nadpisywać starego planu. Zadanie `ADR-001` w backlogu formalizuje supersession, bez działania wstecz.

## Jak używać dokumentacji

1. Właściciel akceptuje albo modyfikuje decyzje w [OPEN_DECISIONS.md](OPEN_DECISIONS.md).
2. W następnym PR powstaje osobny ADR z migracją i jasnymi granicami dotychczasowej decyzji.
3. Wykonywać pojedyncze zadania z [MIGRATION_BACKLOG.md](MIGRATION_BACKLOG.md), zaczynając od `CORE-001A`; każda zmiana ma osobne testy i kontrolę kompatybilności.
4. Po każdym etapie aktualizować fakty z testów i realnych deployów; **nie traktować stanu projektowanego jako zaimplementowanego**.

## Weryfikacja zakresu ARCH-001

Opracowano lokalny pakiet sześciu plików Markdown z inspekcji repozytoriów. Nie wykonano refaktoryzacji, płatnych żądań API, migracji danych, operacji IAM/Terraform ani wdrożeń. Pełne `pytest backend/tests` oraz smoke rzeczywistych wyjść nie zostały uruchomione, ponieważ nie ma lokalnego checkoutu repo. Kontrole formatowania wygenerowanego pakietu są niezależne od testów aplikacji.

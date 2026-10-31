# Propozycja migracji AI Content Studio do cloud/hybrid

Status: **PROPOSED**, 2026-10-28. Audyt kodu, bez implementacji i wdrożenia.

| Dokument | Zawartość |
| --- | --- |
| [CURRENT_STATE_AUDIT](CURRENT_STATE_AUDIT.md) | Dowody w kodzie, macierz ponownego użycia, ograniczenia i testy |
| [TARGET_ARCHITECTURE](TARGET_ARCHITECTURE.md) | Produkcja, kontrakty danych/API, bezpieczeństwo i worker |
| [ARCHITECTURAL_DECISIONS](ARCHITECTURAL_DECISIONS.md) | Rekomendacje, alternatywy i decyzje właściciela |
| [MIGRATION_PLAN](MIGRATION_PLAN.md) | Backlog CLOUD, zależności, zakresy i bramki akceptacji |
| [DEPLOYMENT_AND_COSTS](DEPLOYMENT_AND_COSTS.md) | Jedno środowisko GCP, CI/CD, koszty i operacje |
| [ADR-0004-PROPOSED](ADR-0004-PROPOSED.md) | Projekt ADR bez zmiany historycznego ADR-0003 |

Obowiązuje [ADR-0003](../decisions/0003-desktop-first-architecture.md), a [plan D###](../desktop/IMPLEMENTATION_PLAN.md) pozostaje autorytatywny dla desktopu. Ten katalog jest propozycją przyszłego planu CLOUD, nie drugim aktywnym backlogiem. Przyjęcie wymaga decyzji właściciela w CLOUD-001, jawnej aktualizacji instrukcji i wyboru jednego zadania. Numer zadania ani status dokumentu D### nie dowodzi gotowości zależności. Plan desktopowy pozostaje bez zmian.

Czytanie: audyt → architektura/decyzje → wdrożenie/koszty → plan. Pierwsze zadania: CLOUD-001–CLOUD-005. Najwcześniejszy lokalny przekrój: CLOUD-010; użyteczny przekrój GCP: CLOUD-021. Żadna decyzja właściciela nie została tu oznaczona ACCEPTED.

Nie wykonano płatnych operacji, provisioning, deploy, upload, push ani merge. Nie zmieniono kodu produktu, projektów SQLite, mediów ani repozytorium Tripse.

## Weryfikacja tego audytu

- Środowisko: istniejący izolowany `.venv-ci311`, Python 3.11.9, Windows; bez instalowania zależności.
- `python -m pytest backend/tests`: **2674 passed, 12 skipped, 1 failed**, 1739.14 s. Test `backend/tests/integration/test_duration_repair.py::test_d078_cloud_measurements_drive_duration_repair_without_images` kończy się `ValueError: Section synthesis incomplete; valid chunks remain available for retry.` przez `desktop/cloud_audio.py:406`. Uruchomiony osobno również nie przeszedł. Przyczyna źródłowa nie została ustalona; nie zmieniano kodu/testów w ramach audytu. To odtwarzalny baseline blocker do wyjaśnienia przed M1 acceptance, nie dowód gotowości narracji headless.
- Sprawdzono 41 kart: wymagane pola, poprawne task references i acykliczny graf zależności; lokalne odnośniki Markdown, UTF-8 i parzyste code fences poprawne.
- `git diff --check`: bez błędów. Zmiany tej sesji ograniczone do tego katalogu i linku w `docs/INDEX.md`.
- Nie wykonywano cloud/GPU/OAuth/YouTube acceptance. Statusy PROPOSED i niezweryfikowane bramki pozostają jawne.

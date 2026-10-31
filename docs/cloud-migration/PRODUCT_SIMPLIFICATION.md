# ARCH-001 — PRODUCT_SIMPLIFICATION

**Status:** `PROPOSED`; nie usuwać kodu. Cel: platforma produkcji, nie edytor. Wiersze oznaczają **przyszłą prezentację funkcji**, nie rezygnację z integralności. Podstawa: `desktop/editor.py`, `desktop/*_panel.py`, `desktop/pipeline_driver.py`, `application/*`, dokumenty D071/D073/D075/D076/D078/D079.

Najpierw powstaje headless production i offline package/proxy, potem persistent GCP job i ręcznie zatwierdzony OpenAI smoke CLI/API. Poniższa nawigacja dotyczy późniejszego `UI-001`; nie jest zależnością tego smoke. Krótki smoke 30–90 s i pełny ~10-min production acceptance są osobnymi poziomami walidacji.

## 1. Minimalna nawigacja web MVP

1. **Dashboard:** ostatnie produkcje, statusy, błędy i budżety.
2. **Channels:** kilka pól profilu — język `en`, głos, ton, typ filmu, styl obrazu, orientacja, polityka kosztów; przypięcie **konkretnej rewizji**.
3. **New Production:** temat/brief, kanał, docelowa długość (~10 min), potwierdzony koszt maksymalny, Start.
4. **Production detail:** timeline etapów, płatne/bezpłatne próby, źródła, raport QA, podgląd MP4, ponów bezpieczny etap, zaakceptuj/odrzuć rezultat.
5. **Artifacts:** manifest, komplet źródeł, link do paczki na lokalny finishing.

**Poza MVP:** panel ręcznego timeline, wielotrackowość, edycja grafów scen, wybieranie modeli lokalnych, audio tuning, szczegółowy crop, automatyczny upload do YouTube, harmonogram. Dashboard może wyświetlać „Shorts: not produced yet”, bez obiecywania ich automatycznej produkcji.

## 2. Funkcje bezwzględnie zachowane

| Zdolność | Nowe zachowanie | Powód |
| --- | --- | --- |
| Channel configuration | Wersjonowany immutable preset + snapshot na produkcji | Powtarzalność, ochrona poprzednich wydań |
| Script generation | Automatycznie z planu, opcjonalny approve before paid | Kontrola treści i jakości |
| OpenAI Speech | Rewizjonowana synteza; preflight koszt/limit; retry proof | Koszty i recovery |
| OpenAI Images | Generacja oryginałów, weryfikacja MIME/rozmiar/checksum | Final GPU worker potrzebuje realnych źródeł |
| Scene and timing | Automatyczny acceptance, precyzyjne mapowanie audio↔scene | Resynchronizacja, reprodukcja |
| Timeline and proxy FFmpeg | Zapis frozen timeline, wejść oraz MP4 proxy | Podgląd nie jest źródłem prawdy |
| Production status | Trwałe etapy/progress/attempts | Możliwość resume/diagnostyki |
| Artifact preservation | Niemutowalne manifesty, revision IDs, checksums | Historia i audyt |
| Quality | Podstawowy technical QA + status redakcyjny oddzielnie | Brak fałszywego „APPROVED” |
| Retry & selective regeneration | Reuse przez dependency fingerprints, limit paid operations | Unikanie duplikacji wydatków |

## 3. Macierz upraszczania dotychczasowych edytorów

| Dotychczasowy element | Decyzja UI | Zachować pod spodem | Podstawa / ryzyko |
| --- | --- | --- | --- |
| Advanced script editor | `manual approval` / ukryta korekta tekstu dla jednej sekcji dopiero w fazie 2 | ScriptRevision, expected revision, differential invalidation | `video_plan_panel.py`, `ProjectSession`; ryzyko wysokie |
| Manual scene editing | `automatic operation`, opcjonalny advanced override | Accepted plan, timing, scene IDs, approval history | `scene_services.py`; wysokie |
| Visual prompt editor | Channel-level visual style preset; per-scene regenerate action | PromptRevision, current context, fingerprint | `pipeline_driver.py::prompt_ready`; średnie |
| Timeline editor | Read-only storyboard/timecodes; final editable timeline później | `TimelineCompiler`, media mapping, frozen revision | `timeline_composition.py`; wysokie |
| Audio processing controls | Jeden `original` wariant domyślny; advanced `processed` dopiero po kompatybilności | WAV chunks, clocks, waveform integrity | `automatic_workflow.py`, `D078`; wysokie |
| Narration revision controls | Voice preset + „regenerate affected narration” + log kosztów | Immutable delivery settings, retry outcomes, raw TTS archive | `cloud_audio.py`; bardzo wysokie |
| Image selection controls | Wybrany obraz w review + opcjonalny regenerate | selected artifact ID + all retained variants | `scene_services.py`; średnie |
| Motion controls | Channel preset `subtle/off`, bez suwaczków klatka po klatce | Deterministic motion policy + version | `scene_motion`, renderer; średnie |
| Branding editor | Wersjonowane profile kanału i intro/outro assets, proste UI | ChannelLibrary version refs, exact checksum | `D076`, `ExportPanel`; wysokie |
| Shorts framing/editorial | W MVP nieobecne; po finalizacji: sugestie 3 Shorts + approve | Crop/focal-point edit revision, source frame/sample boundaries | `D071`, `D073_SHORTS_UI`; wysokie |
| Advanced publishing package | Automatyczny metadata draft, approve przed uploadem w osobnym PUBLISH-001 | History/provenance/reports | `api/routes/publishing.py`, D070; średnie |
| Deep QA controls | Status quick QA z przyciskiem detali | Validator thresholds/reports; nie mieszaj preview/final | `production_quality`, D073; średnie |
| Obecne UI panelowe | `UI_REPLACE` gdy API/UI działa | Warstwa `application` i repozytoria | `desktop/editor.py`; wysokie |

## 4. Które pozornie skomplikowane elementy są nieusuwalne

**Nie usuwać wraz z panelami:**

- historii immutable revisions: script, section, accepted scene plan, prompt, image, audio, timeline, rendering, channel.
- optimistic checks (`expected_active_revision_id`), rozróżnienia `current` i `historical`, append-only selections i editorial approvals.
- `DependencyDeclaration`, `RequestFingerprint`, cache identity włącznie z modelem/wariantem/parametrami i checksums.
- rezerwacji budżetu **przed** wysłaniem płatnego requestu oraz archiwizowania otrzymanych raw responses **przed** dekodowaniem.
- rozdziału: sources/original images, upscaled motion masters, proxy mp4, final mp4, Shorts i ich mappingu.
- 25 FPS frame/timebase, 48 kHz audio mapping, timingu per section/scene i sample boundaries.
- rejection/provenance od real QA, render verification i publish approval.
- cancel, stale execution fencing, recoverable state, partial publication quarantine.

Minimalny frontend może schować te rekordy w zakładce `Advanced / Evidence`. Core musi nadal je wymuszać.

## 5. Potencjalne wygaszanie — bez usuwania podczas ARCH-001

| Kandydat | Przesłanki | Warunek przed usunięciem | Pewność | Ryzyko |
| --- | --- | --- | --- | --- |
| Chatterbox V3 (`chatterbox-v3` extra, optional runtime) | Nowy target OpenAI Speech; osobna instalacja torch/GPU | Globalny import graph, legacy workspace scan, test retained audio/migration, dokument praw licencyjnych i snapshot archiwum | średnia | średnie/wysokie |
| Piper (`piper` extra i runtime/provisioning) | Nowy target OpenAI Speech | Dowód braku aktywnego użycia i kompatybilności otwierania starych projektów | średnia | średnie |
| XTTS (`xtts` extra) | Oznaczone evaluation-only w arch docs | Reference check: docs, CI, importy i komplet eksperymentów | wysoka co do „poza MVP”, niska co do bezpiecznego delete | niskie/średnie |
| Local Stable Diffusion | Cloud obrazy przez OpenAI | Weryfikacja faktycznej factory, starych workspace i kontraktów providerów, smoke OpenAI images | średnia | średnie |
| Desktop Qt panels | Nowy Web UI | Parytet jakości produkcji, co najmniej jedna realna produkcja, export/historia/restoration, osobna decyzja o retirement | wysoka co do UI_REPLACE, niska co do natychmiastowego delete | wysokie |
| `DirectorPanel` / zaawansowane Shorts UI | Niepotrzebne dla cloud preview | Weryfikacja użycia, dostęp do historii istniejących Shorts, lokalny worker final | średnia | wysokie |
| `modules/*` i `workflow/*` | Dwie ścieżki workflow w repo; część może być historyczna | Dependency graph + test coverage + zachowana offline long-form integracja | niska | wysokie |
| `tooling/*`, `experiments/tts_local/*` | Poza targetem produkcyjnym | Global import/CI references i decyzja o archiwizacji | średnia | niskie |
| Stare composition i shim kompatybilności | Możliwe duplikaty | Nowy headless composition + equivalence tests, kontrakty starych workspace, plan rollbacku | niska | wysokie |
| Installer Windows + runtime catalog | Nie będą potrzebne cloud service | Działający i zaakceptowany lokalny finishing worker (może nadal korzystać z runtime managera) | średnia | wysokie |

### Bezpieczna kolejność usuwania

`CORE-001` headless parity → `CORE-002` proxy MP4 → `STORAGE-001` portable package → `API-001/DEPLOY-001/UI-001` chmura → `LOCAL-001` final GPU → dane historyczne/read-only legacy compatibility → przegląd usage/dead-code → **dopiero** `CLEANUP-001` z osobnymi PR. Wcześniejsze „usunąć edytor” oznacza jedynie brak go w nowym UI, nie kasowanie kodu.

## 6. Do weryfikacji przed decyzją o martwym kodzie

Pierwotny audyt bez pełnego checkoutu nie zweryfikował wszystkich provider compositions, wszystkich PySide6 paneli i wszystkich referencji w testach. `CORE-001A` wymaga: `rg` importów, `python -m pytest --collect-only`, analiza `pyproject.toml` extras, `git grep` odwołań do runtime i package entrypoints; rozróżnić kod runtime, test-only, docs-only, historycznie używane workspace. Raport usuwania musi wskazywać **dokładne ścieżki i konsumentów**, nie tylko nazwę providera.

# Repo Product & Technical Insights

## 1. Executive Summary

Repozytorium jest prototypem systemu do automatycznego tworzenia dłuższych narracyjnych treści video/tekstowych z wykorzystaniem modeli AI. W praktyce realizuje pipeline od zbierania źródeł, przez research i tworzenie dossier, po planowanie narracji, generowanie segmentów, edycję i prostą ocenę jakości. Problem, który rozwiązuje, to skrócenie i zautomatyzowanie procesu tworzenia angażującego contentu opartygo o źródła faktów i narracje „storytelling”. Obecnie obsługuje przede wszystkim content typu documentaire/long-form explainer o charakterze faktograficznym i narracyjnym, z naciskiem na tematykę news/analysis. Potencjalnym użytkownikiem byłby twórca contentu, redaktor, marketer lub zespół produkcyjny, który chce szybko uzyskać szkic lub draft materiału do dalszej edycji. Repo wygląda bardziej jak prototyp pipeline AI + backend CLI niż pełna aplikacja; ma silne elementy automatyzacji, ale brakuje warstwy UI, zarządzania projektami, autoryzacji i produkcyjnego workflow. 

## 2. Current End-to-End Workflow

### Krok po kroku

1. Wejście użytkownika / konfiguracja
   - Wejście: temat, konfiguracja pipeline, ustawienia długości, model LLM.
   - Kod: [transcript/config.py](transcript/config.py), [transcript/main.py](transcript/main.py).
   - Wynik: stan początkowy pipeline i parametry runu.

2. Ingestion / research source materials
   - Wejście: manifest źródeł w [transcript/data/input/sources.jsonl](transcript/data/input/sources.jsonl) oraz opcjonalnie źródła raw.
   - Kod: [transcript/pipeline/ingestion.py](transcript/pipeline/ingestion.py).
   - Wynik: walidacja źródeł, pobranie dokumentów raw, zbudowanie korpusu curated.

3. Retrieval / RAG
   - Wejście: temat i korpus danych.
   - Kod: [transcript/pipeline/rag.py](transcript/pipeline/rag.py).
   - Wynik: notatki RAG, streszczenie, lista faktów i kontekstu do dalszej pracy.

4. Story dossier
   - Wejście: wyniki RAG.
   - Kod: [transcript/pipeline/dossier.py](transcript/pipeline/dossier.py).
   - Wynik: ustrukturyzowany dossier narracyjny: timeline, key people, key places, confirmed/disputed facts.

5. Planning narracji
   - Wejście: dossier i konfiguracja długości.
   - Kod: [transcript/pipeline/planner.py](transcript/pipeline/planner.py).
   - Wynik: plan segmentów i subsegmentów z target word count, rolami narracyjnymi i motywami wizualnymi.

6. Generowanie subsegmentów i segmentów
   - Wejście: plan narracji + dossier.
   - Kod: [transcript/pipeline/segment_writer.py](transcript/pipeline/segment_writer.py).
   - Wynik: drafty tekstowe segmentów/subsegmentów, z diagnostyką jakości i fallbackami.

7. Editing / merge
   - Wejście: wygenerowane segmenty.
   - Kod: [transcript/pipeline/editor.py](transcript/pipeline/editor.py).
   - Wynik: połączony transcript finalny w [transcript/data/output/transcript_v1.txt](transcript/data/output/transcript_v1.txt).

8. QA / critique
   - Wejście: merged transcript + dossier + plan.
   - Kod: [transcript/pipeline/qa.py](transcript/pipeline/qa.py).
   - Wynik: raport QA w [transcript/data/output/qa_report.json](transcript/data/output/qa_report.json).

9. Voiceover (oddzielny, ale powiązany workflow)
   - Wejście: transcript po QA.
   - Kod: [voiceover/application/generate_voiceover.py](voiceover/application/generate_voiceover.py), [voiceover/services](voiceover/services).
   - Wynik: audio WAV, cleaned transcript, chunk metadata.

### Diagram tekstowy

input → ingestion → research/RAG → dossier → planning → segment writing → editing → QA → export

W wersji voiceover:

transcript → cleaning → chunking → TTS → audio assembly → output WAV

## 3. Existing Capabilities

### Idea generation
- Opis: repo nie ma klasycznego generatora pomysłów z promptu; bardziej jest to pipeline tworzenia narracji wokół konkretnego tematu.
- Status: partial
- Najważniejsze pliki: [transcript/pipeline/rag.py](transcript/pipeline/rag.py), [transcript/pipeline/dossier.py](transcript/pipeline/dossier.py)
- Reuse: tak, ale jako warstwa research/story framing, nie jako generator idei z pustego ekranu.

### Script generation
- Opis: tworzenie długiego skryptu narracyjnego, segmentów i subsegmentów.
- Status: implemented
- Najważniejsze pliki: [transcript/pipeline/segment_writer.py](transcript/pipeline/segment_writer.py), [transcript/pipeline/editor.py](transcript/pipeline/editor.py)
- Reuse: bardzo wysokie

### Research
- Opis: walidacja źródeł, pobieranie dokumentów, filtrowanie szumu, selekcja faktów, RAG-like retrieval.
- Status: implemented
- Najważniejsze pliki: [transcript/pipeline/ingestion.py](transcript/pipeline/ingestion.py), [transcript/pipeline/rag.py](transcript/pipeline/rag.py)
- Reuse: bardzo wysokie

### Scene planning
- Opis: planowanie segmentów, subsegmentów, celów narracyjnych, motywów wizualnych i target word count.
- Status: implemented
- Najważniejsze pliki: [transcript/pipeline/planner.py](transcript/pipeline/planner.py)
- Reuse: bardzo wysokie

### Voiceover / TTS
- Opis: przygotowanie transcriptu do TTS, dzielenie na chunki, synteza lokalna do WAV.
- Status: implemented
- Najważniejsze pliki: [voiceover/application/generate_voiceover.py](voiceover/application/generate_voiceover.py), [voiceover/models/kokoro_tts.py](voiceover/models/kokoro_tts.py), [voiceover/services/chunker.py](voiceover/services/chunker.py)
- Reuse: średnie do wysokiego, ale wymaga refaktoryzacji pod pełny workflow produkcyjny.

### Subtitles / captions
- Status: missing but implied
- Najważniejsze pliki: brak implementacji
- Reuse: tak jako funkcja przyszłościowa, ale obecnie brak.

### Asset selection
- Opis: repo nie ma prawdziwego systemu assetów; jest jedynie konceptualne wsparcie motywów wizualnych i faktów.
- Status: partial / prototype
- Najważniejsze pliki: [transcript/pipeline/planner.py](transcript/pipeline/planner.py)
- Reuse: niskie bez dodatkowej warstwy asset management.

### Video rendering
- Opis: repo nie renderuje pełnego video; jedynie przygotowuje audio i artefakty pośrednie.
- Status: missing but implied
- Najważniejsze pliki: brak
- Reuse: niskie; należy projektować od zera pod bardziej złożony pipeline renderingu.

### Export
- Opis: eksport transcriptu, raportu QA i audio WAV.
- Status: implemented
- Najważniejsze pliki: [transcript/utils/io.py](transcript/utils/io.py), [voiceover/services/artifact_writer.py](voiceover/services/artifact_writer.py)
- Reuse: tak

### Publishing
- Opis: brak publikacji do platform social/YouTube/website.
- Status: missing
- Najważniejsze pliki: brak
- Reuse: nie bez projektu nowej warstwy.

### Configuration
- Opis: silna konfiguracja pipeline, env vars, model providers, target duration, word budget.
- Status: implemented
- Najważniejsze pliki: [transcript/config.py](transcript/config.py), [voiceover/config.py](voiceover/config.py)
- Reuse: bardzo wysokie

### Logging
- Opis: logowanie do stdout, komunikaty o stanie LLM, fallbackach, błędach.
- Status: partial
- Najważniejsze pliki: [transcript/utils/llm.py](transcript/utils/llm.py), [transcript/main.py](transcript/main.py)
- Reuse: tak, ale do ujednolicenia.

### Retry / error handling
- Opis: repo ma mechanizmy fallbacku i diagnostyki, ale nie pełną kolejkę retry ani operacje biznesowe.
- Status: partial
- Najważniejsze pliki: [transcript/pipeline/segment_writer.py](transcript/pipeline/segment_writer.py), [transcript/utils/llm.py](transcript/utils/llm.py)
- Reuse: tak, jako podstawowy mechanizm, ale wymaga rozbudowania.

### UI / API / CLI
- Opis: obecnie jest CLI-first, z prostym wejściem przez skrypty i env vars, bez web UI i API.
- Status: partial
- Najważniejsze pliki: [transcript/main.py](transcript/main.py), [voiceover/cli/run_voiceover.py](voiceover/cli/run_voiceover.py)
- Reuse: tak, jako punkt wejścia, ale nie jako pełny interfejs użytkownika.

## 4. Inputs, Outputs and Intermediate Artifacts

### Dane wejściowe
- temat: ustawiany w [transcript/config.py](transcript/config.py)
- manifest źródeł w [transcript/data/input/sources.jsonl](transcript/data/input/sources.jsonl)
- opcjonalne źródła raw w [transcript/data/input/raw](transcript/data/input/raw)
- konfiguracja LLM / TTS / output paths

### Dane pośrednie
- RAG notes i summary
- dossier narracyjny
- plan segmentów
- subsegments
- segments
- diagnostics generation
- raport QA

### Dane wyjściowe
- final transcript text
- QA report
- cleaned transcript
- chunk metadata
- final voiceover WAV

### Formaty plików
- JSON, JSONL, TXT, WAV, optional metadata

### Foldery outputowe
- [transcript/data/intermediate](transcript/data/intermediate)
- [transcript/data/output](transcript/data/output)
- [voiceover/outputs](voiceover/outputs)

### Najważniejsze pliki artefaktów
- [transcript/data/intermediate/rag_output.json](transcript/data/intermediate/rag_output.json)
- [transcript/data/intermediate/story_dossier.json](transcript/data/intermediate/story_dossier.json)
- [transcript/data/intermediate/narrative_plan.json](transcript/data/intermediate/narrative_plan.json)
- [transcript/data/intermediate/subsegments.json](transcript/data/intermediate/subsegments.json)
- [transcript/data/intermediate/segments.json](transcript/data/intermediate/segments.json)
- [transcript/data/intermediate/generation_diagnostics.json](transcript/data/intermediate/generation_diagnostics.json)
- [transcript/data/output/transcript_v1.txt](transcript/data/output/transcript_v1.txt)
- [transcript/data/output/qa_report.json](transcript/data/output/qa_report.json)
- [voiceover/outputs/chunks.json](voiceover/outputs/chunks.json)
- [voiceover/outputs/cleaned_transcript.txt](voiceover/outputs/cleaned_transcript.txt)
- [voiceover/outputs/final_voiceover.wav](voiceover/outputs/final_voiceover.wav)

### Przykładowy przepływ danych

1. Manifest źródeł → walidacja → pobranie raw documents
2. Raw docs → curated corpus → retrieval notes
3. Retrieval notes → story dossier → narrative plan
4. Plan → segments + subsegments → merge → transcript
5. Transcript → QA → approval gate / export
6. Transcript → chunking → TTS → final audio

### Typowe artefakty domenowe
- prompt
- script
- outline
- scenes
- audio
- subtitles
- video
- thumbnail
- metadata
- render config

W repo te artefakty są obecne częściowo: prompty są ukryte w kodzie LLM, script i outline są obecne, audio jest obecne, ale subtitles/video/thumbnail/render config nie są jeszcze zrealizowane jako pełny moduł.

## 5. Domain Model Candidates

### Project
- Opis: kontener dla jednego zadania lub kampanii contentowej.
- Pola: id, name, topic, status, owner, created_at, target_duration, brand_profile_id.
- Powiązania: ma wiele GenerationJob, ContentIdea, Asset, PublishingTarget.
- Pochodzenie: rekomendacja.

### ContentIdea
- Opis: pomysł lub temat przewodni dla contentu.
- Pola: id, title, brief, audience, angle, tone, status.
- Powiązania: należy do Project, prowadzi do Script.
- Pochodzenie: rekomendacja, choć temat jest już w konfiguracji.

### Script
- Opis: pełny i ustrukturyzowany skrypt narracyjny.
- Pola: id, project_id, title, text, word_count, status, version, qa_status.
- Powiązania: ma wiele Scene, Voiceover, Caption, Render.
- Pochodzenie: bezpośrednio wynika z kodu jako transcript.

### Scene
- Opis: pojedynczy segment/narracyjny blok treści.
- Pola: id, script_id, sequence, role, goal, target_word_count, text, visual_motifs.
- Powiązania: część Script, ma wiele Asset.
- Pochodzenie: bezpośrednio wynika z planu i segmentów.

### Voiceover
- Opis: wersja audio skryptu.
- Pola: id, script_id, voice, language, speed, audio_path, status.
- Powiązania: powiązany z Script i GenerationJob.
- Pochodzenie: bezpośrednio wynika z voiceover modułu.

### Caption
- Opis: napisy do video/audio.
- Pola: id, script_id, text, timestamp_start, timestamp_end, format.
- Powiązania: do Script i VideoRender.
- Pochodzenie: rekomendacja.

### Asset
- Opis: media, obraz, klip, grafika, stock asset, muzyka.
- Pola: id, type, source, path, metadata, license_status.
- Powiązania: do Scene, Project, VideoRender.
- Pochodzenie: rekomendacja.

### VideoRender
- Opis: konkretna wersja renderu końcowego.
- Pola: id, project_id, output_path, resolution, format, status, cost, created_at.
- Powiązania: ma wiele Asset, Caption, Voiceover, PublishingTarget.
- Pochodzenie: rekomendacja.

### Template
- Opis: szablon struktury narracji, stylu, promptów lub layoutu.
- Pola: id, name, kind, prompt_template_id, style_profile_id.
- Powiązania: używany przy tworzeniu wielu Scriptów.
- Pochodzenie: rekomendacja.

### BrandProfile
- Opis: profil marki lub stylu komunikacji.
- Pola: id, name, tone, visual_guidelines, preferred_voice, banned_terms.
- Powiązania: do Project, Template, Asset.
- Pochodzenie: rekomendacja.

### PublishingTarget
- Opis: kanał publikacji, np. YouTube, TikTok, LinkedIn, blog.
- Pola: id, platform, format, aspect_ratio, caption_required.
- Powiązania: do VideoRender, Project.
- Pochodzenie: rekomendacja.

### GenerationJob
- Opis: pojedyncze zadanie produkcyjne dla jednego etapu pipeline.
- Pola: id, project_id, stage, status, model, retries, logs_path, cost_estimate.
- Powiązania: do Project, WorkflowRun, Script.
- Pochodzenie: rekomendacja, ale bardzo sensowna dla przyszłej architektury.

### PromptTemplate
- Opis: szablon promptu dla agentów lub modeli.
- Pola: id, name, role, system_prompt, user_prompt, version.
- Pochodzenie: rekomendacja; w kodzie prompty są zwięzłe i hardcoded w wywołaniach LLM.

### WorkflowRun
- Opis: konkretne uruchomienie pipeline dla projektu.
- Pola: id, project_id, started_at, finished_at, status, artifacts_path.
- Powiązania: do GenerationJob, Script, Project.
- Pochodzenie: rekomendacja.

## 6. External Integrations

### LLM providers
- Do czego: generowanie treści, research, planowanie, QA.
- Gdzie: [transcript/utils/llm.py](transcript/utils/llm.py), [transcript/pipeline](transcript/pipeline).
- Hardcoded: częściowo; provider, model i API keys są konfigurowane przez env vars i config.
- Abstrakcja: powinna być opakowana w adapter Service/Provider abstraction.
- Ryzyka: koszty, rate limits, jakość odpowiedzi, vendor lock-in.

### OpenRouter
- Do czego: zdalne wywołania modeli przez API.
- Gdzie: [transcript/utils/llm.py](transcript/utils/llm.py).
- Hardcoded: częściowo.
- Ryzyka: koszt i zależność od zewnętrznego dostawcy.

### Ollama
- Do czego: lokalne modele LLM.
- Gdzie: [transcript/utils/llm.py](transcript/utils/llm.py).
- Hardcoded: częściowo.
- Ryzyka: wymaga lokalnego środowiska, nie zawsze stabilne.

### Transformers / local model runtime
- Do czego: lokalne uruchamianie modeli.
- Gdzie: [transcript/utils/llm.py](transcript/utils/llm.py).
- Hardcoded: tak w konfiguracji.
- Ryzyka: zależność od sprzętu, pamięci, CUDA, rozmiaru modelu.

### Kokoro / ONNX TTS
- Do czego: lokalna synteza mowy.
- Gdzie: [voiceover/models/kokoro_tts.py](voiceover/models/kokoro_tts.py).
- Hardcoded: częściowo, ale dobrze oddzielone przez adapter.
- Ryzyka: jakość głosu, brak gotowych modeli, zależność od lokalnych assets.

### pyttsx3
- Do czego: fallback lokalny dla TTS.
- Gdzie: [voiceover/models/kokoro_tts.py](voiceover/models/kokoro_tts.py).
- Hardcoded: tak w fallback logic.
- Ryzyka: jakość i brak pełnej kontroli nad głosem.

### Web scraping / urllib
- Do czego: pobieranie dokumentów źródłowych z manifestu.
- Gdzie: [transcript/pipeline/ingestion.py](transcript/pipeline/ingestion.py).
- Hardcoded: częściowo.
- Ryzyka: blokady, zmiany HTML, niejednolitość źródeł.

### Storage / filesystem
- Do czego: zapis artefaktów pośrednich i finalnych na dysku.
- Gdzie: [transcript/utils/io.py](transcript/utils/io.py), [voiceover/services](voiceover/services).
- Hardcoded: w pewnym stopniu, ale prosty i czytelny model lokalnego plikowania.
- Ryzyka: brak skalowalności i braku wersjonowania.

### Social / publishing platforms
- Do czego: nie są jeszcze używane.
- Gdzie: brak implementacji.
- Hardcoded: brak.
- Ryzyka: API, rate limits, policy compliance.

## 7. Technical Architecture

### Stack technologiczny
- Python 3, standard library + dataclasses + pathlib
- modeli LLM przez adaptery: local/ollama/openrouter
- TTS lokalny: Kokoro ONNX lub pyttsx3
- JSON/JSONL/TXT artefacts
- pytest/unittest tests

### Struktura folderów
- [transcript](transcript): pipeline narracyjny, ingestion, planner, writer, QA
- [voiceover](voiceover): TTS, chunking, audio assembly
- [tests](tests): testy jednostkowe i integracyjne
- [transcript/data](transcript/data): input/intermediate/output artefacts

### Główne moduły
- [transcript/main.py](transcript/main.py): orchestrator całego pipeline
- [transcript/pipeline/ingestion.py](transcript/pipeline/ingestion.py): sourcing and validation
- [transcript/pipeline/rag.py](transcript/pipeline/rag.py): retrieval and fact extraction
- [transcript/pipeline/dossier.py](transcript/pipeline/dossier.py): structured story facts
- [transcript/pipeline/planner.py](transcript/pipeline/planner.py): scene plan
- [transcript/pipeline/segment_writer.py](transcript/pipeline/segment_writer.py): script-generation logic
- [transcript/pipeline/editor.py](transcript/pipeline/editor.py): merge and polish
- [transcript/pipeline/qa.py](transcript/pipeline/qa.py): quality gate
- [voiceover/application/generate_voiceover.py](voiceover/application/generate_voiceover.py): voiceover orchestration

### Sposób uruchamiania
- CLI pipeline: [transcript/main.py](transcript/main.py)
- CLI voiceover: [voiceover/cli/run_voiceover.py](voiceover/cli/run_voiceover.py)
- lokalne środowisko przez env vars i config

### Konfiguracja
- [transcript/config.py](transcript/config.py) i [voiceover/config.py](voiceover/config.py)
- obsługa providerów LLM przez env vars

### Przechowywanie danych
- lokalne pliki na dysku, JSON/JSONL/TXT/WAV
- brak bazy danych, brak storage backend, brak wersjonowania obiektów

### Obsługa długich procesów
- pipeline jest sekwencyjny i oparty o pliki pośrednie
- brak kolejki zadań, workerów, retry schedulerów, job statusów

### Testy
- obecne testy jednostkowe i e2e w [tests](tests)
- testy są ważnym atutem, ale skupiają się głównie na pipeline tekstowym

### Logowanie
- prosty stdout logging, komunikaty o błędach i fallbackach

### Obsługa błędów
- fallback generation, walidacja źródeł, QA gate, wyjątki w TTS i audio concatenation
- nadal brak robustnego monitoring i error tracking

### Docker / CI / deployment
- brak widocznego Dockerfile, CI workflow i deploymentu
- repo bardziej jest narzędziem lokalnym niż produkcyjnie deployowanym systemem

### Ocena architektury
- Przeniesienie do większej aplikacji: możliwe, ale wymaga refaktoryzacji na warstwy
- Wydzielenie jako worker: sensowne
- Wystawienie jako API: możliwe, ale trzeba dodać auth, job management, storage
- Użycie jako biblioteka/moduł: dobre, szczególnie pipeline narracyjny i moduł TTS

## 8. UX and Product Flow Implications

### Ekrany / obszary aplikacji
- dashboard projektu
- ekran tworzenia idei / briefa
- ekran research i źródeł
- ekran planowania narracji / storyboardu
- ekran edycji skryptu i segmentów
- ekran preview / review
- ekran approval workflow
- ekran publikacji / eksportu

### Akcje użytkownika
- utworzenie projektu
- wpisanie tematu / briefa
- wybór stylu, tonu, platformy, długości
- zatwierdzanie źródeł lub ich modyfikacja
- edycja skryptu, segmentów i narracji
- uruchomienie generation
- akceptacja / odrzucenie draftu
- eksport lub publikacja

### Miejsca edycji
- właściwy skrypt, scene, hook, segmenty, prompty, style profile
- szczególnie ważne są manualne poprawki po każdej fazie

### Preview
- preview transcriptu, preview audio, preview timeline, preview scene cards

### Approval step
- wymagany po generacji draftu i po QA
- powinien być oddzielny checkpoint dla jakości, zgodności z brandem i prawami

### Ustawienia użytkownika
- target duration, tone, audience, platform, brand style, voice, language, content policy, source allowlist/blocklist

## 9. Reusable Components

### Ready to reuse
- pipeline orchestration concept z [transcript/main.py](transcript/main.py)
- modularna konfiguracja z [transcript/config.py](transcript/config.py)
- warstwa plików artefaktów z [transcript/utils/io.py](transcript/utils/io.py)
- struktura QA i workflow checkpoints
- adapter LLM z [transcript/utils/llm.py](transcript/utils/llm.py)

### Reuse after refactor
- RAG/research logic w [transcript/pipeline/rag.py](transcript/pipeline/rag.py)
- story dossier logic w [transcript/pipeline/dossier.py](transcript/pipeline/dossier.py)
- planner i segment writer w [transcript/pipeline/planner.py](transcript/pipeline/planner.py) i [transcript/pipeline/segment_writer.py](transcript/pipeline/segment_writer.py)
- TTS pipeline w [voiceover](voiceover)

### Inspiration only
- konkretne heurystyki narracyjne i prompty, bo są silnie dopasowane do jednego przypadku (Epstein)
- część filtrów jakości i markerów błędów, które są specjalistyczne i nieuniwersalne

### Do not reuse
- hardcoded topic-specific logic i dane z Epsteina
- testy i artefakty demo, które są mocno osadzone w jednym use case
- część fallbackowych mocków i tymczasowych mechanizmów

## 10. Gaps and Limitations

### Brakujące funkcje
- brak UI, dashboardu, editor UI
- brak auth i roles
- brak projektów, workspaceów i użytkowników
- brak historii uruchomień i wersji contentu
- brak kolejki zadań i workerów
- brak retry policy, backoff, dead-letter queue
- brak observability i telemetry
- brak kosztów / budgets / token usage tracking
- brak manualnej edycji po każdym etapie
- brak subtitles/captions
- brak renderowania video i eksportu do platform
- brak asset management
- brak publikacji i approval workflow

### Jakość outputu
- raport QA pokazuje problemy z powtórzeniami, timeline consistency, unsupported entities i fallback rate
- pipeline ma silne zależności od jakości promptów i danych wejściowych

### Skalowanie
- lokalne pliki i sekwencyjne uruchamianie nie wystarczą dla rosnącego ruchu
- brak cache, storage backend, job management, autoscaling

## 11. Requirements Candidates for Spec Kit

### Functional Requirements
- FR-001: System shall allow a user to create a project and define a content brief with topic, audience, tone, target duration, and publishing platform.
- FR-002: System shall ingest source materials from URLs, files, or a manifest and validate them before generation.
- FR-003: System shall produce a structured research dossier from ingested sources.
- FR-004: System shall generate a narrative outline and scene plan from the dossier.
- FR-005: System shall generate a draft script in segments and subsegments with configurable length and style.
- FR-006: System shall support manual editing of script, scenes, and voiceover text.
- FR-007: System shall provide a QA gate with quality metrics and blocking rules before export.
- FR-008: System shall generate voiceover audio from approved scripts.
- FR-009: System shall generate captions/subtitles for produced audio/video content.
- FR-010: System shall export content artifacts in standard formats (text, JSON, audio, video metadata).
- FR-011: System shall store generation history and artifact versions per project.
- FR-012: System shall support approval workflow with review and reject actions.
- FR-013: System shall support multiple publishing targets and format presets.
- FR-014: System shall expose generation jobs as asynchronous tasks with retry support.

### Non-Functional Requirements
- NFR-001: Performance: the system shall support generating a draft script within a user-acceptable SLA for short-form and mid-form content.
- NFR-002: Reliability: the system shall tolerate partial model failures and preserve intermediate artifacts.
- NFR-003: Security: the system shall support authentication, authorization, and secure secret management for API providers.
- NFR-004: Cost control: the system shall track model usage, token budgets, and per-project cost estimates.
- NFR-005: Observability: the system shall provide logs, metrics, and traceability for each generation run.
- NFR-006: Maintainability: the system shall separate provider, content, workflow, and persistence layers.
- NFR-007: Extensibility: the system shall support multiple LLM and TTS providers through abstractions.

### User Stories
- As a content creator, I want to start from a brief and generate a first draft script quickly so that I can iterate faster.
- As a marketer, I want to choose a platform and tone so that the generated content fits the channel.
- As a producer, I want to review and edit scenes before export so that I can keep quality control.
- As an admin, I want to monitor generation jobs and costs so that I can manage operations.
- As a brand manager, I want to enforce style and safety policies so that output stays consistent.

## 12. Risks and Open Questions

### Ryzyka techniczne
- zależność od modeli LLM i TTS
- nieprzewidywalność jakości i hallucinacji
- brak robustnej kolejki zadań oraz storage backend
- trudność z utrzymaniem spójności między etapami

### Ryzyka produktowe
- użytkownicy mogą oczekiwać gotowego contentu bez ręcznej korekty
- proces może być zbyt skomplikowany dla początkujących użytkowników
- brak premade workflow dla różnych typów contentu

### Ryzyka kosztowe
- duże koszty przy wielokrotnym generowaniu i wielu iteracjach
- nieprzewidywalne zużycie tokenów i dodatkowe kosztowe modele TTS / render

### Ryzyka jakościowe
- factual drift, powtórzenia, niezgodność z faktami, unsupported claims
- zbyt duże uzależnienie od promptów i źródeł

### Pytania otwarte do właściciela produktu
- Czy MVP ma obejmować tekst, audio, czy pełne video?
- Czy celem jest content edukacyjny, social media, marketing, czy documentary-like long form?
- Czy użytkownik ma być solo creator, czy zespołem z workflowem approval?
- Czy wymagane są źródła zewnętrzne, własne dokumenty, czy oba warianty?
- Czy system ma być lokalny, cloudowy, czy hybrydowy?

## 13. Recommended MVP Scope

### Co powinno wejść do MVP
- projekt / brief / topic
- research ingestion i walidacja źródeł
- generation skryptu z planem scen i segmentów
- manualna edycja skryptu
- QA gate
- eksport tekstu i podstawowego metadata
- prosty voiceover dla zaakceptowanego skryptu

### Co odłożyć
- pełne renderowanie video
- asset marketplace / stock search
- publishing automation do wielu platform
- advanced collaboration i roles
- pełna analiza kosztów i ROI

### Moduły krytyczne
- research / ingestion
- planner / script writer
- editor / QA
- artifact storage
- approval workflow

### Szybkie wygrane
- szybkie tworzenie pierwszego draftu skryptu
- transparentny workflow od źródeł do skryptu
- możliwość iteracji nad treścią bez odtwarzania całego pipeline
- prosty voiceover jako bonus

### Co wymaga dalszego researchu
- model provider strategy
- formaty contentu dla różnych platform
- UX dla manual edit i approval
- storage architecture i cost model

## 14. File References

- [README.MD](README.MD) — opis wysokopoziomowy repo i jego intencji.
- [transcript/main.py](transcript/main.py) — orchestrator całego pipeline.
- [transcript/config.py](transcript/config.py) — konfiguracja tematu, długości, modeli i providerów.
- [transcript/pipeline/ingestion.py](transcript/pipeline/ingestion.py) — pobieranie, walidacja i tworzenie korpusu curated.
- [transcript/pipeline/rag.py](transcript/pipeline/rag.py) — retrieval i research notes.
- [transcript/pipeline/dossier.py](transcript/pipeline/dossier.py) — ustrukturyzowany dossier narracyjny.
- [transcript/pipeline/planner.py](transcript/pipeline/planner.py) — planowanie segmentów i narracji.
- [transcript/pipeline/segment_writer.py](transcript/pipeline/segment_writer.py) — generowanie skryptu i subsegmentów.
- [transcript/pipeline/editor.py](transcript/pipeline/editor.py) — merge i polish transcriptu.
- [transcript/pipeline/qa.py](transcript/pipeline/qa.py) — QA i ocena jakości.
- [transcript/utils/llm.py](transcript/utils/llm.py) — adapter modeli LLM i fallbacki.
- [transcript/utils/io.py](transcript/utils/io.py) — lokalne zapisy artefaktów.
- [voiceover/application/generate_voiceover.py](voiceover/application/generate_voiceover.py) — orchestracja voiceover.
- [voiceover/models/kokoro_tts.py](voiceover/models/kokoro_tts.py) — adapter TTS lokalnego.
- [voiceover/services/chunker.py](voiceover/services/chunker.py) — dzielenie transcriptu na chunki.
- [voiceover/services/audio_concatenator.py](voiceover/services/audio_concatenator.py) — składanie audio do finalnego pliku WAV.
- [transcript/data/intermediate](transcript/data/intermediate) — artefakty pośrednie procesu.
- [transcript/data/output](transcript/data/output) — outputy końcowe pipeline.
- [voiceover/outputs](voiceover/outputs) — artyfakty voiceover.
- [tests](tests) — testy pokrywające pipeline i logiczne komponenty.

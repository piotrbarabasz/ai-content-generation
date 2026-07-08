# Repo Product & Technical Insights

## 1. Executive Summary

Repozytorium jest narzędziem do automatycznego tworzenia krótkich filmów wideo z narracją, napisami i efektami wizualnymi na bazie transcriptu i obrazu. Rozwiązuje problem przekształcania surowego tekstu i materiałów wejściowych w gotowy short video z rytmem narracyjnym, timingiem mowy i wizualną kompozycją. Obecnie obsługuje przede wszystkim content typu dokumentalno-reportażowego i true-crime, gdzie kluczowe są: scena, narracja, lektura i podział na krótkie odcinki. Potencjalnymi użytkownikami są twórcy contentu, producenci social media, zespoły marketingowe i eksperymentatorzy automatyzacji video. Repo wygląda bardziej jak prototyp i pipeline automatyzacji niż pełna aplikacja; ma silny komponent CLI i warstwę przetwarzania danych, ale brakuje warstwy UI, zarządzania projektami i operacyjnego środowiska. Jego wartość dla przyszłej aplikacji AI Content Studio polega na tym, że zawiera już funkcje end-to-end od wejścia tekstu do eksportu video, choć w formie mocno skryptowej i z wieloma hardcodami.

## 2. Current End-to-End Workflow

### Krok po kroku

1. Input / transcript
   - Wejście: plik tekstowy z transcriptem, np. [data/bryan_kohberger/short_1/transcript.txt](data/bryan_kohberger/short_1/transcript.txt)
   - Kod: [scene_segmentation/cli.py](scene_segmentation/cli.py), [scene_segmentation/planner.py](scene_segmentation/planner.py)
   - Wynik: plan scen i struktura narracyjna w [data/bryan_kohberger/short_1/scene_segmentation.json](data/bryan_kohberger/short_1/scene_segmentation.json)

2. Scene segmentation
   - Wejście: transcript
   - Kod: [scene_segmentation/planner.py](scene_segmentation/planner.py), [scene_segmentation/feature_layer.py](scene_segmentation/feature_layer.py), [scene_segmentation/decision_layer.py](scene_segmentation/decision_layer.py)
   - Wynik: lista scen z metadanymi: pacing, visual intensity, semantic role, source sentence ids

3. Preparation / speech timing
   - Wejście: plan scen + transcript + plik audio voiceover, np. [data/bryan_kohberger/short_1/voiceover_short_1.mp3](data/bryan_kohberger/short_1/voiceover_short_1.mp3)
   - Kod: [preparation_engine/cli.py](preparation_engine/cli.py), [preparation_engine/application/preparation_pipeline.py](preparation_engine/application/preparation_pipeline.py), [preparation_engine/domain/speech/transcription_service.py](preparation_engine/domain/speech/transcription_service.py)
   - Wynik: [data/bryan_kohberger/short_1/speech_timeline.json](data/bryan_kohberger/short_1/speech_timeline.json), [data/bryan_kohberger/short_1/scene_timeline.json](data/bryan_kohberger/short_1/scene_timeline.json), [data/bryan_kohberger/short_1/narrative_plan.json](data/bryan_kohberger/short_1/narrative_plan.json)

4. Image preparation
   - Wejście: folder z obrazami surowymi [data/bryan_kohberger/short_1/raw_img](data/bryan_kohberger/short_1/raw_img)
   - Kod: [image_prep/cli.py](image_prep/cli.py)
   - Wynik: przetworzone obrazy w [data/bryan_kohberger/short_1/scenes](data/bryan_kohberger/short_1/scenes)

5. Video assembly
   - Wejście: obrazy scen, kontrakty narracyjne i timingowe, plik audio
   - Kod: [video_base_engine/cli.py](video_base_engine/cli.py), [video_base_engine/assembler.py](video_base_engine/assembler.py), [video_base_engine/projection.py](video_base_engine/projection.py)
   - Wynik: podstawowe wideo [data/bryan_kohberger/short_1/base_short.mp4](data/bryan_kohberger/short_1/base_short.mp4)

6. Subtitle generation
   - Wejście: speech timeline + scene timeline + narrative plan + base video
   - Kod: [subtitle_engine/cli.py](subtitle_engine/cli.py), [subtitle_engine/core/engine.py](subtitle_engine/core/engine.py)
   - Wynik: [data/bryan_kohberger/short_1/subtitle_semantic_plan.json](data/bryan_kohberger/short_1/subtitle_semantic_plan.json), [data/bryan_kohberger/short_1/subtitle_render_plan.json](data/bryan_kohberger/short_1/subtitle_render_plan.json), [data/bryan_kohberger/short_1/subtitles.ass](data/bryan_kohberger/short_1/subtitles.ass), oraz finalne video z napisami

### Diagram przepływu

input → segmentation → preparation/timing → asset preparation → video assembly → subtitle generation → export

## 3. Existing Capabilities

### Idea generation
- Opis: repo nie zawiera dedykowanego modułu do generowania pomysłów contentu; jest to raczej pipeline do produkcji konkretnego shorta na podstawie już istniejącego tekstu.
- Status: missing but implied
- Najważniejsze pliki: brak dedykowanego modułu; wejściowe transcripty w [data](data)
- Reuse: partial; można wykorzystać jako wzorzec do przyszłego modułu ideation

### Script generation
- Opis: repo przyjmuje transcript jako wejście i nie generuje go samodzielnie; transcripty są źródłem narracji.
- Status: partial / input-driven
- Najważniejsze pliki: [data/**/transcript.txt](data)
- Reuse: partial

### Research
- Opis: brak modułu researchu; repo nie integruje zewnętrznych źródeł ani web researchu.
- Status: missing
- Najważniejsze pliki: brak
- Reuse: low

### Scene planning
- Opis: automatyczne segmentowanie transcriptu na sceny z atrybutami narracyjnymi, pacingiem, visual intensity i semantic role.
- Status: implemented
- Najważniejsze pliki: [scene_segmentation/planner.py](scene_segmentation/planner.py), [scene_segmentation/feature_layer.py](scene_segmentation/feature_layer.py), [scene_segmentation/decision_layer.py](scene_segmentation/decision_layer.py)
- Reuse: high

### Voiceover/TTS
- Opis: repo zakłada obecność gotowego pliku voiceover [data/**/voiceover_short_*.mp3](data), a następnie analizuje go z Whisperem.
- Status: partial
- Najważniejsze pliki: [preparation_engine/domain/speech/transcription_service.py](preparation_engine/domain/speech/transcription_service.py)
- Reuse: high for transcription and timing; low for TTS generation

### Subtitles/captions
- Opis: generowanie semantycznych napisów, render planu oraz pliku ASS; obsługa podświetleń słów i top overlay.
- Status: implemented
- Najważniejsze pliki: [subtitle_engine/core/orchestrator.py](subtitle_engine/core/orchestrator.py), [subtitle_engine/components/semantic_planning/planner.py](subtitle_engine/components/semantic_planning/planner.py), [subtitle_engine/components/render_projection/planner.py](subtitle_engine/components/render_projection/planner.py), [subtitle_engine/components/delivery/ass_builder.py](subtitle_engine/components/delivery/ass_builder.py)
- Reuse: high

### Asset selection
- Opis: wybór obrazów scenowych z folderu assets; przepływ wideo opiera się na mapowaniu scen do plików obrazów.
- Status: implemented / prototype
- Najważniejsze pliki: [video_base_engine/io_utils.py](video_base_engine/io_utils.py), [video_base_engine/projection.py](video_base_engine/projection.py)
- Reuse: medium

### Video rendering
- Opis: składanie klipów obrazów, dodanie efektów, audio, crossfades, eksport MP4.
- Status: implemented
- Najważniejsze pliki: [video_base_engine/assembler.py](video_base_engine/assembler.py), [video_base_engine/effects.py](video_base_engine/effects.py), [video_base_engine/motion.py](video_base_engine/motion.py)
- Reuse: high

### Export
- Opis: eksport do MP4/ASS i zapis artefaktów JSON.
- Status: implemented
- Najważniejsze pliki: [video_base_engine/cli.py](video_base_engine/cli.py), [subtitle_engine/cli.py](subtitle_engine/cli.py), [preparation_engine/cli.py](preparation_engine/cli.py)
- Reuse: high

### Publishing
- Opis: brak integracji z platformami social media ani publikacji automatycznej.
- Status: missing
- Najważniejsze pliki: brak
- Reuse: low

### Configuration
- Opis: istnieje konfiguracja dataclass dla renderingu, subtitle i segmentacji.
- Status: implemented
- Najważniejsze pliki: [video_base_engine/config.py](video_base_engine/config.py), [subtitle_engine/shared/config.py](subtitle_engine/shared/config.py), [scene_segmentation/config.py](scene_segmentation/config.py)
- Reuse: high

### Logging
- Opis: logowanie odbywa się głównie przez print i komunikaty w CLI; brak systemowego loggingu.
- Status: partial
- Najważniejsze pliki: [preparation_engine/cli.py](preparation_engine/cli.py), [subtitle_engine/cli.py](subtitle_engine/cli.py)
- Reuse: medium

### Retry/error handling
- Opis: w transkrypcji występuje prosta logika retry dla Whisper w przypadku przycięcia nagrania; inaczej brak robustnej obsługi błędów.
- Status: partial
- Najważniejsze pliki: [preparation_engine/domain/speech/transcription_service.py](preparation_engine/domain/speech/transcription_service.py)
- Reuse: medium

### UI/API/CLI
- Opis: repo jest zbudowane wokół CLI i skryptów uruchomieniowych, bez API i bez UI.
- Status: partial / prototype
- Najważniejsze pliki: [run_part1.py](run_part1.py), [run_part2.py](run_part2.py), [scene_segmentation/cli.py](scene_segmentation/cli.py), [subtitle_engine/cli.py](subtitle_engine/cli.py)
- Reuse: medium for automation layer, low for end-user application

## 4. Inputs, Outputs and Intermediate Artifacts

### Dane wejściowe
- transcript tekstowy: [data/**/transcript.txt](data)
- pliki audio: [data/**/voiceover_short_*.mp3](data)
- obrazy surowe: [data/**/raw_img](data)
- efekty audio/wideo: [data/effects](data/effects)
- konfiguracja lokalna i ścieżki absolutne w CLI

### Dane pośrednie
- plan scen: [data/**/scene_segmentation.json](data)
- plan narracyjny: [data/**/narrative_plan.json](data)
- timeline mowy: [data/**/speech_timeline.json](data)
- timeline scen: [data/**/scene_timeline.json](data)
- semantic plan napisów: [data/**/subtitle_semantic_plan.json](data)
- render plan napisów: [data/**/subtitle_render_plan.json](data)
- obrazy przetworzone: [data/**/scenes](data)

### Dane wyjściowe
- video bazowe: [data/**/base_short.mp4](data)
- video z napisami: [data/**/bryan_kohberger_part_1.mp4](data/bryan_kohberger/short_1/bryan_kohberger_part_1.mp4) lub odpowiedniki w folderach short
- plik ASS: [data/**/subtitles.ass](data)

### Formaty plików
- txt, json, mp3, png, mp4, ass

### Foldery outputowe
- [data](data)
- [data/**/short_x](data)
- [data/**/scenes](data)
- [data/effects](data/effects)

### Najważniejsze artefakty
- prompt: repo nie ma osobnego systemu promptów dla modeli; prompty są częściowo zakodowane w narzędziach AI i plikach wejściowych, np. [image_prep/layer_1_prompt.txt](image_prep/layer_1_prompt.txt), [image_prep/layer_2_prompt.txt](image_prep/layer_2_prompt.txt)
- script: transcript i plan narracyjny
- outline: scene segmentation + narrative plan
- scenes: scene segmentation JSON i timeline scen
- audio: voiceover mp3 i timeline mowy
- subtitles: ASS + semantic/render plan
- video: MP4
- thumbnail: brak w repo
- metadata: JSON z metadanymi dla timeline i planów
- render config: dataclassy w [video_base_engine/config.py](video_base_engine/config.py) i [subtitle_engine/shared/config.py](subtitle_engine/shared/config.py)

### Przykładowy przepływ danych

transcript.txt → scene_segmentation.json → narrative_plan.json + scene_timeline.json + speech_timeline.json → cropped scene images → base_short.mp4 → subtitles.ass + final video

## 5. Domain Model Candidates

### Project
- Opis: zbiór treści, ustawień i artefaktów dla jednego zadania lub serii shortsów.
- Pola: id, name, owner_id, created_at, status, template_id, target_platforms
- Powiązania: ma wiele ContentIdea, GenerationJob, WorkflowRun
- Pochodzenie: rekomendacja

### ContentIdea
- Opis: pomysł lub temat contentu, np. „true crime short o sprawie X”.
- Pola: id, title, topic, angle, tone, audience, status
- Powiązania: ma wiele Script, Project
- Pochodzenie: rekomendacja

### Script
- Opis: tekst narracyjny lub scenariusz wejściowy.
- Pola: id, text, language, source, word_count, duration_estimate
- Powiązania: ma wiele Scene, Voiceover, Caption
- Pochodzenie: bezpośrednio z transcriptu, ale jako encja jest rekomendacją

### Scene
- Opis: jednostka narracyjna wyodrębniona z transcriptu.
- Pola: id, order, text, pacing_hint, visual_intensity, semantic_role, source_sentence_ids
- Powiązania: należy do Script, ma Asset, Caption, Voiceover segment
- Pochodzenie: bezpośrednio z [data/**/scene_segmentation.json](data)

### Voiceover
- Opis: nagranie lektora lub TTS.
- Pola: id, audio_path, duration_s, language, provider, status
- Powiązania: ma wiele WordTiming, Scene
- Pochodzenie: bezpośrednio z [data/**/voiceover_short_*.mp3](data)

### Caption
- Opis: pojedynczy blok lub linia napisów.
- Pola: id, text, start_s, end_s, style, scene_id, highlight_words
- Powiązania: należy do Scene, Voiceover
- Pochodzenie: bezpośrednio z [data/**/subtitle_render_plan.json](data)

### Asset
- Opis: obraz lub klip wizualny przypisany do sceny.
- Pola: id, type, path, source, width, height, status
- Powiązania: należy do Scene, VideoRender
- Pochodzenie: bezpośrednio z [data/**/scenes](data)

### VideoRender
- Opis: wynik renderu finalnego lub wersji pośredniej.
- Pola: id, output_path, format, resolution, duration_s, status, cost_estimate
- Powiązania: ma wiele Asset, Caption, Voiceover, Project
- Pochodzenie: bezpośrednio z [data/**/base_short.mp4](data)

### Template
- Opis: szablon stylu, layoutu, brandu lub workflow.
- Pola: id, name, config, target_platform
- Powiązania: używany przez Project, WorkflowRun
- Pochodzenie: rekomendacja

### BrandProfile
- Opis: zestaw reguł marki: kolory, ton głosu, fonty, pacing, preferencje stylu.
- Pola: id, name, colors, fonts, voice_tone, visual_rules
- Powiązania: używany przez Project i Template
- Pochodzenie: rekomendacja

### PublishingTarget
- Opis: kanał publikacji, np. TikTok, Reels, YouTube Shorts.
- Pola: id, platform, aspect_ratio, max_duration, caption_rules
- Powiązania: ma wiele VideoRender
- Pochodzenie: rekomendacja

### GenerationJob
- Opis: zadanie przetwarzania pipeline’u dla jednego shorta lub wersji.
- Pola: id, project_id, status, created_at, started_at, finished_at, error
- Powiązania: ma wiele WorkflowRun, Artifact
- Pochodzenie: rekomendacja, ale bardzo naturalna dla tego repo

### PromptTemplate
- Opis: szablon promptu dla modeli AI lub narzędzi assetów.
- Pola: id, name, prompt_text, variables, version
- Powiązania: używany przez GenerationJob
- Pochodzenie: częściowo z [image_prep/layer_1_prompt.txt](image_prep/layer_1_prompt.txt) i [image_prep/layer_2_prompt.txt](image_prep/layer_2_prompt.txt)

### WorkflowRun
- Opis: przebieg pojedynczego uruchomienia pipeline’u.
- Pola: id, job_id, stage, timestamps, logs, artifacts
- Powiązania: ma wiele Artifact, GenerationJob
- Pochodzenie: rekomendacja

## 6. External Integrations

### faster-whisper
- Do czego: transkrypcja audio i ekstrakcja timestampów słów.
- Gdzie: [preparation_engine/domain/speech/transcription_service.py](preparation_engine/domain/speech/transcription_service.py)
- Hardcoded: częściowo; model size, language i device są ustawione w kodzie.
- Abstrakcja: powinna być opakowana przez adapter AI/TranscriptionProvider.
- Ryzyka: jakość transkrypcji, język, koszt lokalny / obciążenie CPU

### mutagen
- Do czego: odczyt długości audio.
- Gdzie: [preparation_engine/domain/speech/transcription_service.py](preparation_engine/domain/speech/transcription_service.py)
- Hardcoded: nie
- Abstrakcja: niepotrzebna
- Ryzyka: niskie

### moviepy
- Do czego: montaż wideo, audio, efekty, eksport MP4.
- Gdzie: [video_base_engine/assembler.py](video_base_engine/assembler.py), [video_base_engine/effects.py](video_base_engine/effects.py), [video_base_engine/motion.py](video_base_engine/motion.py)
- Hardcoded: częściowo
- Abstrakcja: powinna być opakowana przez VideoRendererAdapter.
- Ryzyka: zależność od ffmpeg, wolne renderowanie, platform-specific behavior

### ffmpeg / ffprobe
- Do czego: render napisów do ASS oraz burn into video.
- Gdzie: [subtitle_engine/cli.py](subtitle_engine/cli.py), [subtitle_engine/components/delivery/burn_executor.py](subtitle_engine/components/delivery/burn_executor.py)
- Hardcoded: tak, ścieżki są hardcoded.
- Abstrakcja: obowiązkowa.
- Ryzyka: zależność od lokalnej instalacji, różne wersje, trudniejsze CI/CD

### OpenCV (cv2)
- Do czego: przycinanie obrazów do formatu vertical 9:16.
- Gdzie: [image_prep/cli.py](image_prep/cli.py)
- Hardcoded: tak, parametry cropu i ścieżki.
- Abstrakcja: przydatna.
- Ryzyka: niskie

### Local filesystem / hardcoded paths
- Do czego: przechowywanie danych wejściowych i artefaktów.
- Gdzie: wszystkie CLI.
- Hardcoded: tak.
- Abstrakcja: obowiązkowa w nowej aplikacji.
- Ryzyka: brak skalowalności, brak wielo-użytkownikowości, problem z deploymentem

### Prompt files
- Do czego: zewnętrzne instrukcje dla workflow asset generation / prompt-driven processing.
- Gdzie: [image_prep/layer_1_prompt.txt](image_prep/layer_1_prompt.txt), [image_prep/layer_2_prompt.txt](image_prep/layer_2_prompt.txt)
- Hardcoded: tak.
- Abstrakcja: powinna być modelowana jako PromptTemplate.
- Ryzyka: słaba obsługa wersjonowania i testowania

## 7. Technical Architecture

### Stack technologiczny
- Python
- dataclasses i klasy biznesowe
- CLI scripts
- moviepy, faster-whisper, mutagen, cv2
- JSON jako format kontraktów pośrednich
- ASS dla napisów
- MP4 jako output

### Struktura folderów
- [scene_segmentation](scene_segmentation) – segmentacja narracyjna
- [preparation_engine](preparation_engine) – timing, speech alignment, kontrakty
- [subtitle_engine](subtitle_engine) – semantyczne i renderowe planowanie napisów
- [video_base_engine](video_base_engine) – montaż i eksport video
- [image_prep](image_prep) – przygotowanie obrazów
- [data](data) – dane wejściowe i artefakty output

### Główne moduły
- orchestracja pipeline’u przez [run_part1.py](run_part1.py) i [run_part2.py](run_part2.py)
- segmentacja w [scene_segmentation/planner.py](scene_segmentation/planner.py)
- timing i alignment w [preparation_engine/application/preparation_pipeline.py](preparation_engine/application/preparation_pipeline.py)
- render video w [video_base_engine/assembler.py](video_base_engine/assembler.py)
- render napisów w [subtitle_engine/core/orchestrator.py](subtitle_engine/core/orchestrator.py)

### Sposób uruchamiania
- przez skrypty Python i moduły CLI
- brak zautomatyzowanego deploymentu, brak Docker, brak CI

### Konfiguracja
- z dataclassów i lokalnych wartości domyślnych
- większość ścieżek jest hardcoded w CLI

### Przechowywanie danych
- lokalny filesystem, JSON, media files
- brak bazy danych, brak storage abstraction, brak metadanych użytkownika

### Długie procesy
- pipeline jest synchroniczny i uruchamiany sekwencyjnie
- brak kolejek, workerów, retry, job persistence

### Testy
- repo ma testy jednostkowe dla segmentacji i subtitle engine, np. [scene_segmentation/tests/test_refactor_segmentation.py](scene_segmentation/tests/test_refactor_segmentation.py), [subtitle_engine/tests/test_sentence_segmentation.py](subtitle_engine/tests/test_sentence_segmentation.py)
- testy są ograniczone i nie obejmują całego end-to-end workflow

### Logowanie
- print-based, brak structured logging

### Obsługa błędów
- częściowo obecna, ale często tylko w formie wyjątku lub komunikatu CLI

### Docker/CI/deployment
- brak widocznych artefaktów

### Ocena architektury
- do większej aplikacji: częściowo, ale wymaga refaktoryzacji i abstrachowania zależności
- do workerów: tak, po wydzieleniu etapów i wstrzyknięciu adapterów
- do API: tak, ale jako osobna warstwa na topie pipeline’u
- do biblioteki/modułu: tak, zwłaszcza dla segmentacji i subtitle engine

## 8. UX and Product Flow Implications

Przyszła aplikacja powinna mieć workflow z następującymi ekranami:

1. Start / New Project
   - użytkownik tworzy projekt, wybiera temat, format, platformę, język

2. Input & Source Material
   - użytkownik wgrywa transcript, audio, obraz, lub wpisuje prompt / temat

3. Ideation / Outline Review
   - użytkownik przegląda proponowane sceny i może je edytować

4. Preview / Timeline Editor
   - użytkownik widzi timeline, sceny, timing, podział na kadry, napisów i audio

5. Approval Step
   - użytkownik zatwierdza wersję, może skorygować pojedyncze sceny lub napisy

6. Generation / Render Queue
   - system uruchamia render; użytkownik obserwuje status i logi

7. Export / Publish
   - użytkownik eksportuje MP4, pobiera ASS, publikuje na platformy

### Akcje użytkownika
- utworzenie projektu
- wgranie treści źródłowych
- akceptacja lub edycja scen
- opcja preview
- uruchomienie renderu
- publikacja

### Miejsca edycji
- tekst sceny
- timing sceny
- prompty i szablony
- style napisów
- wybór assetów

### Preview
- preview storyboardu i preview timeline
- preview napisów w czasie
- preview renderu wstępnego

### Approval step
- konieczny między outline a renderem, zwłaszcza przy automatycznym generowaniu scen i napisów

### Ustawienia
- target platform, aspect ratio, duration, voiceover, style, brand profile, subtitle mode

## 9. Reusable Components

### Ready to reuse
- Segmentacja narracyjna i heurystyki scen: [scene_segmentation/planner.py](scene_segmentation/planner.py)
- Pipeline timingu i alignowania mowy: [preparation_engine/application/preparation_pipeline.py](preparation_engine/application/preparation_pipeline.py)
- Render napisów i planowanie semantyczne: [subtitle_engine](subtitle_engine)
- Montaż video: [video_base_engine/assembler.py](video_base_engine/assembler.py)

### Reuse after refactor
- Kontrakty JSON między etapami: wymagają uporządkowania i wersjonowania
- Konfiguracja: obecnie rozproszona i zbyt mocno hardcoded
- Obsługa assetów: wymaga katalogu assetów, metadanych, statusów i walidacji

### Inspiration only
- Obecne heurystyki jakościowe dla segmentacji i timingu
- Pomysł na semantyczne podświetlanie słów i dynamiczny overlay
- Podejście do narracyjnych atrybutów sceny: pacing, visual intensity, semantic role

### Do not reuse
- Absolutne ścieżki i lokalne konfiguracje
- CLI jako jedyny interfejs użytkownika
- Hardcoded efekty i parametry renderu
- Brakujące lub tymczasowe pliki z testowymi danymi

## 10. Gaps and Limitations

- brak UI i panelu użytkownika
- brak auth i zarządzania użytkownikami
- brak projektów, zbiorów i historii generacji
- brak kolejki zadań i workerów
- brak systemowego retry, timeoutów i backoffu
- brak observability: logów strukturalnych, metryk, tracingu
- brak kosztów/limitów modeli AI i budżetów
- brak manualnej edycji scen, napisów i timingu w interfejsie
- brak testów end-to-end i testów integracyjnych z pełnym pipeline’em
- brak deploymentu, CI/CD i konteneryzacji
- jakość outputu zależy od jakości wejścia i heurystyk, co może być niestabilne
- architektura nie jest jeszcze gotowa do skalowania wielo-użytkownikowego

## 11. Requirements Candidates for Spec Kit

### Functional Requirements
- FR-001: System shall allow a user to create a project and upload or paste source content such as transcript, audio, or text.
- FR-002: System shall generate a scene outline from source content and allow manual editing before rendering.
- FR-003: System shall generate or import voiceover audio and synchronize captions with speech timing.
- FR-004: System shall produce a short-form video with captions, scene transitions, and optional background effects.
- FR-005: System shall support preview of storyboard, captions, and render before final export.
- FR-006: System shall support approval and revision workflow for generated content.
- FR-007: System shall store generated artifacts and provide access to previous versions.
- FR-008: System shall allow configuration of branding, aspect ratio, tone, and platform-specific formatting.
- FR-009: System shall support export to video and subtitle formats such as MP4 and ASS.
- FR-010: System shall provide job status tracking and error visibility for long-running generation tasks.

### Non-Functional Requirements
- NFR-001: Performance – the system shall support generation of a short video within a reasonable time for a single job, with progress feedback.
- NFR-002: Reliability – the system shall handle failures gracefully with retries and clear error reporting.
- NFR-003: Security – the system shall protect user uploads, generated content, and API credentials.
- NFR-004: Cost control – the system shall allow configuration of model usage limits and budgets.
- NFR-005: Observability – the system shall expose logs, job status, and render diagnostics for each workflow run.

### User Stories
- As a content creator, I want to upload a transcript and generate a short video so that I can produce social content faster.
- As a marketer, I want to adjust scenes and captions before export so that the final output matches my brand.
- As an admin, I want to monitor jobs and errors so that generation workflows remain reliable.

## 12. Risks and Open Questions

### Ryzyka techniczne
- zależność od lokalnych ścieżek i hardcoded konfiguracji
- trudność z przeniesieniem na cloud i wielo-użytkownikowość
- złożoność pipeline’u i brak jednego modelu danych

### Ryzyka produktowe
- użytkownik może oczekiwać pełnej aplikacji, a repo jest tylko prototypem automatyzacji
- brak jasnego modelu edycji manualnej i akceptacji

### Ryzyka kosztowe
- transkrypcja i render video mogą być kosztowne i wolne
- brak budżetowania i policy limits

### Ryzyka jakościowe
- niska stabilność segmentacji i timingu bez ręcznego review
- różnice jakości między wejściami i platformami

### Pytania otwarte
- czy docelowym użytkownikiem są twórcy, marketerzy, agencje, czy właściciele brandów?
- czy aplikacja ma obsługiwać tylko jeden format (np. shorts) czy wiele formatów?
- czy priorytetem jest szybka automatyzacja czy wysoka kontrola manualna?
- czy voiceover ma być generowany lokalnie, przez TTS, czy przez zewnętrzne API?
- czy wymagane są workflowy collaborative i multi-user?

## 13. Recommended MVP Scope

### Co powinno wejść do MVP
- projekt i upload treści
- automatyczna segmentacja scen
- podstawowy preview storyboardu
- generowanie napisów i eksport MP4
- prosty approval step przed finalnym renderem

### Co odłożyć
- pełna publikacja social media
- zaawansowane asset generation
- collaborative editing
- multi-tenant deployment
- pełne analityki i brand management

### Moduły krytyczne
- scene segmentation
- speech timing / alignment
- subtitle generation
- video rendering
- job state and artifact storage

### Szybkie wygrane
- szybkie tworzenie shortów na podstawie transcriptu
- dobra automatyzacja dla prostych workflowów
- gotowy fundament pod przyszły product experience

### Co wymaga dalszego researchu
- czy TTS/voiceover ma być częścią MVP
- jak zbudować system promptów i templateów
- jak modelować workflow approval i manual editing
- jak ograniczyć koszty renderu i AI

## 14. File References

- [run_part1.py](run_part1.py) – uruchamia etapy segmentacji dla batcha
- [run_part2.py](run_part2.py) – uruchamia pełny pipeline prep + video + subtitles
- [scene_segmentation/cli.py](scene_segmentation/cli.py) – CLI do tworzenia planu scen
- [scene_segmentation/planner.py](scene_segmentation/planner.py) – główna logika segmentacji narracyjnej
- [preparation_engine/cli.py](preparation_engine/cli.py) – CLI do przygotowania timeline’ów i kontraktów
- [preparation_engine/application/preparation_pipeline.py](preparation_engine/application/preparation_pipeline.py) – orchestracja timingu, alignowania i kontraktów
- [preparation_engine/domain/speech/transcription_service.py](preparation_engine/domain/speech/transcription_service.py) – integracja z Whisperem
- [video_base_engine/cli.py](video_base_engine/cli.py) – CLI do renderu bazowego wideo
- [video_base_engine/assembler.py](video_base_engine/assembler.py) – montaż finalnego video
- [video_base_engine/projection.py](video_base_engine/projection.py) – mapowanie scen na timeline video
- [subtitle_engine/cli.py](subtitle_engine/cli.py) – CLI do generowania napisów
- [subtitle_engine/core/engine.py](subtitle_engine/core/engine.py) – facade dla subtitle engine
- [image_prep/cli.py](image_prep/cli.py) – przycinanie obrazów do formatu 9:16
- [data/bryan_kohberger/short_1/scene_segmentation.json](data/bryan_kohberger/short_1/scene_segmentation.json) – przykład planu scen
- [data/bryan_kohberger/short_1/narrative_plan.json](data/bryan_kohberger/short_1/narrative_plan.json) – przykład kontraktu narracyjnego
- [data/bryan_kohberger/short_1/scene_timeline.json](data/bryan_kohberger/short_1/scene_timeline.json) – przykład timeline scen
- [data/bryan_kohberger/short_1/speech_timeline.json](data/bryan_kohberger/short_1/speech_timeline.json) – przykład timeline mowy
- [data/bryan_kohberger/short_1/subtitle_render_plan.json](data/bryan_kohberger/short_1/subtitle_render_plan.json) – przykład planu renderu napisów
- [data/bryan_kohberger/short_1/subtitles.ass](data/bryan_kohberger/short_1/subtitles.ass) – przykład pliku ASS
- [data/bryan_kohberger/short_1/base_short.mp4](data/bryan_kohberger/short_1/base_short.mp4) – przykład outputu video

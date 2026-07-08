# Modular Pipeline Insights

## 1. Pipeline Stages Found in This Repository

### 1.1 Transcript Intake
- Opis: wejście tekstowe w postaci transcriptu, które jest punktem startowym dla dalszej produkcji shorta.
- Input: plik tekstowy z transcriptem, np. [data/bryan_kohberger/short_1/transcript.txt](data/bryan_kohberger/short_1/transcript.txt)
- Output: surowy tekst narracyjny do dalszego przetwarzania.
- Najważniejsze pliki: [scene_segmentation/cli.py](scene_segmentation/cli.py), [scene_segmentation/planner.py](scene_segmentation/planner.py)
- Czy jest wydzielony jako moduł: nie; jest częścią wejścia pipeline’u.
- Czy jest sklejony z innym etapem: tak, z segmentacją scen.
- Czy może być opcjonalny: tak, jeśli użytkownik dostarczy już gotowy scenariusz lub outline.

### 1.2 Scene Planning / Segmentation
- Opis: podział transcriptu na sceny z atrybutami narracyjnymi, takimi jak pacing, visual intensity, semantic role.
- Input: transcript
- Output: plan scen w formacie JSON, np. [data/bryan_kohberger/short_1/scene_segmentation.json](data/bryan_kohberger/short_1/scene_segmentation.json)
- Najważniejsze pliki: [scene_segmentation/planner.py](scene_segmentation/planner.py), [scene_segmentation/feature_layer.py](scene_segmentation/feature_layer.py), [scene_segmentation/decision_layer.py](scene_segmentation/decision_layer.py)
- Czy jest wydzielony jako moduł: tak, logicznie jako osobny moduł.
- Czy jest sklejony z innym etapem: częściowo; wejście jest z transcriptu, ale output jest używany przez późniejsze etapy.
- Czy może być opcjonalny: tak; można pominąć, jeśli użytkownik dostarczy własny outline lub storyboard.

### 1.3 Narrative Plan Construction
- Opis: tworzenie planu narracyjnego z scenami, z metadanymi typu pacing, visual intensity i semantic role.
- Input: lista scen z etapu segmentacji
- Output: [data/bryan_kohberger/short_1/narrative_plan.json](data/bryan_kohberger/short_1/narrative_plan.json)
- Najważniejsze pliki: [preparation_engine/application/preparation_pipeline.py](preparation_engine/application/preparation_pipeline.py), [preparation_engine/contracts/narrative_plan_contract_builder.py](preparation_engine/contracts/narrative_plan_contract_builder.py)
- Czy jest wydzielony jako moduł: częściowo; istnieje jako kontrakt i część pipeline’u, ale nie jako osobny CLI/module.
- Czy jest sklejony z innym etapem: tak, z preparation pipeline.
- Czy może być opcjonalny: tak; można zastąpić ręcznym outline.

### 1.4 Voiceover / Transcription Timing
- Opis: analiza pliku audio voiceover, transkrypcja i alignowanie słów do scen oraz timeline’u.
- Input: plik audio, transcript/scenes
- Output: [data/bryan_kohberger/short_1/speech_timeline.json](data/bryan_kohberger/short_1/speech_timeline.json), [data/bryan_kohberger/short_1/scene_timeline.json](data/bryan_kohberger/short_1/scene_timeline.json)
- Najważniejsze pliki: [preparation_engine/domain/speech/transcription_service.py](preparation_engine/domain/speech/transcription_service.py), [preparation_engine/domain/speech/speech_timeline_builder.py](preparation_engine/domain/speech/speech_timeline_builder.py), [preparation_engine/application/preparation_pipeline.py](preparation_engine/application/preparation_pipeline.py)
- Czy jest wydzielony jako moduł: tak, logicznie jako osobny moduł timing/speech.
- Czy jest sklejony z innym etapem: tak, z narracją i scene timeline.
- Czy może być opcjonalny: tak; można użyć własnego audio bez alignowania lub bez voiceover.

### 1.5 Asset Preparation
- Opis: przygotowanie obrazów do formatu vertical 9:16 i zapis ich w folderze scen.
- Input: folder [data/bryan_kohberger/short_1/raw_img](data/bryan_kohberger/short_1/raw_img)
- Output: obrazy w [data/bryan_kohberger/short_1/scenes](data/bryan_kohberger/short_1/scenes)
- Najważniejsze pliki: [image_prep/cli.py](image_prep/cli.py)
- Czy jest wydzielony jako moduł: tak, ale w formie prostego skryptu.
- Czy jest sklejony z innym etapem: tak, z renderingiem.
- Czy może być opcjonalny: tak; jeśli assety są już przygotowane lub używany jest inny provider obrazów.

### 1.6 Asset Selection / Projection
- Opis: mapowanie scen do konkretnych plików assetów i budowanie specyfikacji scen dla renderu.
- Input: narrative plan + scene timeline + speech timeline + assets
- Output: specyfikacje scen dla video renderera, obsługiwane przez [video_base_engine/projection.py](video_base_engine/projection.py)
- Najważniejsze pliki: [video_base_engine/projection.py](video_base_engine/projection.py), [video_base_engine/io_utils.py](video_base_engine/io_utils.py)
- Czy jest wydzielony jako moduł: częściowo; jest częścią video engine, ale może być osobnym modułem.
- Czy jest sklejony z innym etapem: tak, z renderingiem.
- Czy może być opcjonalny: tak; jeśli renderer dostaje gotowe klipy albo scene specs.

### 1.7 Video Rendering
- Opis: składanie scen w jedną timeline, dodanie efektów, audio i eksport do MP4.
- Input: scenes, assets, voiceover, timeline, efekty
- Output: [data/bryan_kohberger/short_1/base_short.mp4](data/bryan_kohberger/short_1/base_short.mp4)
- Najważniejsze pliki: [video_base_engine/assembler.py](video_base_engine/assembler.py), [video_base_engine/effects.py](video_base_engine/effects.py), [video_base_engine/motion.py](video_base_engine/motion.py), [video_base_engine/config.py](video_base_engine/config.py)
- Czy jest wydzielony jako moduł: tak.
- Czy jest sklejony z innym etapem: częściowo; audio i efekty są zintegrowane.
- Czy może być opcjonalny: tak; można renderować bez efektów lub bez audio.

### 1.8 Subtitle / Caption Planning
- Opis: planowanie semantycznych napisów, podziału na linie, podświetleń słów i visibility.
- Input: speech timeline + scene timeline + narrative plan + base video
- Output: [data/bryan_kohberger/short_1/subtitle_semantic_plan.json](data/bryan_kohberger/short_1/subtitle_semantic_plan.json)
- Najważniejsze pliki: [subtitle_engine/core/orchestrator.py](subtitle_engine/core/orchestrator.py), [subtitle_engine/components/semantic_planning/planner.py](subtitle_engine/components/semantic_planning/planner.py)
- Czy jest wydzielony jako moduł: tak.
- Czy jest sklejony z innym etapem: tak, z renderem napisów.
- Czy może być opcjonalny: tak; bardzo dobrze pasuje do toggle.

### 1.9 Subtitle Rendering / Delivery
- Opis: renderowanie napisów do pliku ASS i opcjonalne wburnowanie do wideo.
- Input: semantic plan + render plan + base video
- Output: [data/bryan_kohberger/short_1/subtitles.ass](data/bryan_kohberger/short_1/subtitles.ass), finalne video z napisami
- Najważniejsze pliki: [subtitle_engine/components/delivery/service.py](subtitle_engine/components/delivery/service.py), [subtitle_engine/components/delivery/ass_builder.py](subtitle_engine/components/delivery/ass_builder.py), [subtitle_engine/components/delivery/burn_executor.py](subtitle_engine/components/delivery/burn_executor.py)
- Czy jest wydzielony jako moduł: tak.
- Czy jest sklejony z innym etapem: tak, z prev/next semantycznym planowaniem.
- Czy może być opcjonalny: tak; bardzo dobrze pasuje do toggle.

### 1.10 Thumbnail Generation
- Opis: obecnie nie ma modułu thumbnail generation.
- Input: brak widocznego wejścia w repo.
- Output: brak artefaktu w repo.
- Najważniejsze pliki: brak widocznych plików.
- Czy jest wydzielony jako moduł: nie.
- Czy jest sklejony z innym etapem: nie.
- Czy może być opcjonalny: tak; jest naturalnym opcjonalnym modułem.

### 1.11 Publishing / Export
- Opis: repo eksportuje wideo i ASS, ale nie publikuje do platform social media.
- Input: finalne video / metadata
- Output: eksport plików oraz finalny MP4.
- Najważniejsze pliki: [subtitle_engine/cli.py](subtitle_engine/cli.py), [video_base_engine/cli.py](video_base_engine/cli.py)
- Czy jest wydzielony jako moduł: częściowo; export jest obecny, publishing nie.
- Czy jest sklejony z innym etapem: tak, z renderem i subtitle delivery.
- Czy może być opcjonalny: tak.

## 2. Module Candidates

### 2.1 BriefModule
- Opis: przyjmuje użytkownika, temat, cel, platformę i buduje brief contentu.
- Odpowiedzialność: zbiera wymagania wejściowe i ustala parametry workflowu.
- Input schema: {topic, goal, genre, platform, durationProfile, language, tone, audience}
- Output schema: {briefId, contentGoal, targetPlatform, contentGenre, durationProfile, stylePreferences}
- Config schema: {defaultLanguage, defaultTone, supportedGenres, supportedPlatforms}
- Dependencies: brak bezpośredniego odpowiednika w repo; rekomendacja.
- Czy wynika z kodu: rekomendacja.
- Czy powinien być wymagany, opcjonalny, czy warunkowy: wymagany dla pełnego workflowu.

### 2.2 ResearchModule
- Opis: zbiera informacje o temacie, źródłach i faktach.
- Odpowiedzialność: dostarcza materiału źródłowego i źródeł do dalszego pisania.
- Input schema: {brief, sources?}
- Output schema: {researchNotes, citations, facts}
- Config schema: {providers, maxSources, factCheckingEnabled}
- Dependencies: zewnętrzne API lub lokalne źródła; obecnie brak w repo.
- Czy wynika z kodu: rekomendacja.
- Czy powinien być wymagany, opcjonalny, czy warunkowy: opcjonalny lub warunkowy.

### 2.3 IdeaGenerationModule
- Opis: generuje lub wybiera pomysł contentu na bazie briefu i researchu.
- Odpowiedzialność: buduje koncepty / hooki / angle contentu.
- Input schema: {brief, research}
- Output schema: {ideas, selectedIdea, angle, hook}
- Config schema: {modelProvider, temperature, maxIdeas}
- Dependencies: LLM provider.
- Czy wynika z kodu: rekomendacja; repo nie ma tego modułu, ale ma wejście transcriptu i plan narracyjny.
- Czy powinien być wymagany, opcjonalny, czy warunkowy: opcjonalny.

### 2.4 ScriptGenerationModule
- Opis: tworzy lub przekształca tekst w skrypt, który później jest przetwarzany do storyboardu.
- Odpowiedzialność: generowanie treści narracyjnej.
- Input schema: {brief, research, idea, language}
- Output schema: {scriptText, scriptVersion, tone, wordCount}
- Config schema: {provider, maxTokens, styleGuidelines}
- Dependencies: LLM provider.
- Czy wynika z kodu: częściowo; repo przyjmuje transcript jako wejście, ale nie generuje skryptu automatycznie.
- Czy powinien być wymagany, opcjonalny, czy warunkowy: warunkowy; jeśli użytkownik dostarczy własny tekst, moduł może być pominięty.

### 2.5 OutlineModule
- Opis: buduje outline lub plan struktury contentu.
- Odpowiedzialność: pośrednia warstwa między skryptem a scena/shotami.
- Input schema: {scriptText, durationProfile}
- Output schema: {outlineItems, sections, expectedDuration}
- Config schema: {maxSections, pacingRules}
- Dependencies: może korzystać z logicznego podziału scen ze scen segmentation.
- Czy wynika z kodu: tak, częściowo przez [scene_segmentation/planner.py](scene_segmentation/planner.py).
- Czy powinien być wymagany, opcjonalny, czy warunkowy: opcjonalny; użytkownik może wprowadzić outline ręcznie.

### 2.6 ScenePlanningModule
- Opis: dzieli content na sceny i nadaje im atrybuty narracyjne.
- Odpowiedzialność: segmentuje treść na jednostki, które później są renderowane.
- Input schema: {scriptText, outline, genre, durationProfile}
- Output schema: {scenes: [{sceneId, text, pacingHint, visualIntensity, semanticRole}]}
- Config schema: {profile, maxScenes, minSceneWords, strictMode}
- Dependencies: [scene_segmentation/planner.py](scene_segmentation/planner.py)
- Czy wynika z kodu: tak, bezpośrednio.
- Czy powinien być wymagany, opcjonalny, czy warunkowy: wymagany dla workflowu video, ale może być pominięty dla manualnego storyboardu.

### 2.7 AssetSelectionModule
- Opis: dobiera obrazy/klipy do poszczególnych scen.
- Odpowiedzialność: wybór i mapowanie zasobów wizualnych.
- Input schema: {scenes, assetQuery, providerConfig}
- Output schema: {assetAssignments: [{sceneId, assetId, assetPath, confidence}]}
- Config schema: {provider, styleConstraints, aspectRatio}
- Dependencies: provider obrazów i [video_base_engine/projection.py](video_base_engine/projection.py)
- Czy wynika z kodu: tak, częściowo.
- Czy powinien być wymagany, opcjonalny, czy warunkowy: warunkowy; można użyć gotowych assetów lub pominąć.

### 2.8 VoiceoverModule
- Opis: generuje lub przyjmuje audio lektora.
- Odpowiedzialność: audio wejściowe do syncu i renderu.
- Input schema: {scriptText, voiceConfig, language, durationProfile}
- Output schema: {audioPath, durationSeconds, providerMetadata}
- Config schema: {provider, voice, speed, language, format}
- Dependencies: TTS provider, transcription provider.
- Czy wynika z kodu: częściowo; repo nie generuje lektora, ale ma timing wokół audio.
- Czy powinien być wymagany, opcjonalny, czy warunkowy: opcjonalny lub warunkowy.

### 2.9 CaptionsModule
- Opis: buduje napisy semantyczne i renderuje je do formatu ASS lub podobnego.
- Odpowiedzialność: synchronizacja napisów z audio i render.
- Input schema: {speechTimeline, sceneTimeline, narrativePlan, videoConfig}
- Output schema: {subtitlePlan, assFile, captionsMetadata}
- Config schema: {style, position, highlightMode, enabled}
- Dependencies: [subtitle_engine](subtitle_engine)
- Czy wynika z kodu: tak, bezpośrednio.
- Czy powinien być wymagany, opcjonalny, czy warunkowy: opcjonalny, ale bardzo naturalny dla shortów.

### 2.10 VideoRenderingModule
- Opis: montuje video z assetów, audio i napisów.
- Odpowiedzialność: finalny render MP4.
- Input schema: {sceneSpecs, audioPath, subtitlePlan, renderConfig}
- Output schema: {videoPath, durationSeconds, resolution, metadata}
- Config schema: {codec, fps, resolution, effectsEnabled}
- Dependencies: [video_base_engine/assembler.py](video_base_engine/assembler.py)
- Czy wynika z kodu: tak, bezpośrednio.
- Czy powinien być wymagany, opcjonalny, czy warunkowy: wymagany dla finalnego video.

### 2.11 ThumbnailModule
- Opis: generuje miniaturkę do platform social media.
- Odpowiedzialność: tworzenie obrazu preview/thumbnail.
- Input schema: {videoPath, scenes, brandConfig}
- Output schema: {thumbnailPath, metadata}
- Config schema: {provider, size, style}
- Dependencies: asset provider / image renderer.
- Czy wynika z kodu: rekomendacja.
- Czy powinien być wymagany, opcjonalny, czy warunkowy: opcjonalny.

### 2.12 ExportModule
- Opis: eksportuje wygenerowane artefakty do formatu docelowego.
- Odpowiedzialność: mp4, ass, json, metadata i archiwizacja.
- Input schema: {videoPath, captionsPath, artifacts}
- Output schema: {exportedFiles, manifest}
- Config schema: {formats, destination}
- Dependencies: filesystem/storage adapter.
- Czy wynika z kodu: tak, częściowo przez CLI i output files.
- Czy powinien być wymagany, opcjonalny, czy warunkowy: wymagany dla finalizacji.

### 2.13 PublishingModule
- Opis: publikacja na platformach social media.
- Odpowiedzialność: upload i publikacja finalnego contentu.
- Input schema: {videoPath, metadata, targetPlatform}
- Output schema: {publicationStatus, publishUrl}
- Config schema: {platformCredentials, schedule, privacy}
- Dependencies: zewnętrzne API platform.
- Czy wynika z kodu: rekomendacja.
- Czy powinien być wymagany, opcjonalny, czy warunkowy: opcjonalny.

### 2.14 MetadataModule
- Opis: zarządza metadanymi projektu, workflowu i artefaktów.
- Odpowiedzialność: zapis metadata, wersjonowanie i śledzenie.
- Input schema: {projectId, artifactId, stage, contentMeta}
- Output schema: {metadataRecord, manifest}
- Config schema: {storageBackend, retentionPolicy}
- Dependencies: storage adapter.
- Czy wynika z kodu: częściowo; repo ma już JSON z metadata, ale nie jako system.
- Czy powinien być wymagany, opcjonalny, czy warunkowy: wymagany dla aplikacji produkcyjnej.

## 3. Enable / Disable Behavior

### BriefModule
- Włączony: tworzy strukturę workflowu i parametry dla kolejnych etapów.
- Wyłączony: użytkownik musi ręcznie dostarczyć brief i parametry workflowu.
- Pipeline: może działać, ale z ograniczonymi danymi wejściowymi.
- Błędy: brak celu, brak głównego tematu, brak platformy.

### ResearchModule
- Włączony: dostarcza źródła i fakty.
- Wyłączony: system działa na podstawie dostarczonego briefu lub własnego tekstu.
- Ręczne dane: źródła, fakty, notatki lub gotowy script.
- Pipeline: może kontynuować bez researchu.
- Błędy: brak kontekstu, słaby prompt, niekompletne źródła.

### IdeaGenerationModule
- Włączony: generuje koncepty i hooki.
- Wyłączony: użytkownik dostarcza gotowy pomysł lub angle.
- Pipeline: może kontynuować.
- Błędy: brak pomysłu, brak sensownego hooka.

### ScriptGenerationModule
- Włączony: tworzy pełny skrypt.
- Wyłączony: użytkownik dostarcza własny transcript/skrypt.
- Pipeline: może kontynuować.
- Błędy: brak tekstu, zbyt krótki skrypt, zła długość.

### ScenePlanningModule
- Włączony: dzieli skrypt na sceny.
- Wyłączony: użytkownik dostarcza gotowy storyboard lub outline.
- Pipeline: może kontynuować, ale bez automatycznej segmentacji.
- Błędy: brak scen, niezgodność liczby scen z duration profile.

### AssetSelectionModule
- Włączony: dobiera obrazy/klipy.
- Wyłączony: użytkownik ręcznie przydziela assety lub system renderuje bez obrazów.
- Pipeline: może kontynuować, ale output będzie mniej kompletne wizualnie.
- Błędy: brak matchingu assetów, brak plików dla scen.

### VoiceoverModule
- Włączony: generuje lub korzysta z lektora.
- Wyłączony: użytkownik dostarcza własny plik audio lub system renderuje bez lektora.
- Pipeline: może kontynuować; render może działać z muzyką lub bez audio.
- Błędy: brak audio, zła synchronizacja, niezgodność długości.

### CaptionsModule
- Włączony: generuje napisy semantyczne i ASS.
- Wyłączony: render bez napisów lub z napisami ręcznymi.
- Pipeline: może kontynuować bez napisów.
- Błędy: brak transkrypcji, brak speech timeline, zła synchronizacja.

### VideoRenderingModule
- Włączony: tworzy finalny MP4.
- Wyłączony: pipeline kończy się na artefaktach pośrednich.
- Pipeline: nie może zakończyć się finalnym video bez tego modułu.
- Błędy: brak assetów, błędy ffmpeg/moviepy, niezgodny format.

### ThumbnailModule
- Włączony: generuje miniaturę.
- Wyłączony: brak thumbnail, ale pipeline może kontynuować.
- Pipeline: może kontynuować bez thumbnail.
- Błędy: brak preview, błędy renderu obrazu.

### ExportModule
- Włączony: zapisuje gotowe artefakty i manifest.
- Wyłączony: artefakty pozostają w storage lokalnym lub w stagingu.
- Pipeline: może kontynuować, ale brak finalnego eksportu.
- Błędy: błędy zapisu, brak uprawnień dyskowych.

### PublishingModule
- Włączony: publikuje wynik.
- Wyłączony: wynik jest tylko gotowy lokalnie.
- Pipeline: może kontynuować bez publikacji.
- Błędy: błędy autoryzacji, rate limits, nieobsłużona platforma.

## 4. Content Type and Genre Support

| Typ contentu | Obsługuje repo | Można obsłużyć po refaktorze | Potrzebne moduły | Wymagane struktury / prompty |
|---|---|---|---|---|
| short video | tak | tak | ScenePlanningModule, VoiceoverModule, CaptionsModule, VideoRenderingModule | scene plan, timeline, assets |
| long-form video | częściowo | tak | wszystkie moduły z większymi limitami durationProfile | dłuższy script, więcej scen, więcej assetów |
| news | częściowo | tak | BriefModule, ResearchModule, ScriptGenerationModule, ScenePlanningModule | factual brief, source list, timestamps |
| story | tak, przez narrację | tak | ScriptGenerationModule, ScenePlanningModule, VoiceoverModule, CaptionsModule | narrative outline, emotional tone |
| educational | częściowo | tak | ResearchModule, ScriptGenerationModule, ScenePlanningModule, CaptionsModule | structure with sections, examples |
| tutorial | częściowo | tak | ScriptGenerationModule, ScenePlanningModule, AssetSelectionModule, CaptionsModule | step-by-step structure |
| marketing | częściowo | tak | BriefModule, ScriptGenerationModule, AssetSelectionModule, ThumbnailModule | brand profile, CTA, promo hooks |
| documentary | tak, w sensie narracyjno-reportażowym | tak | ResearchModule, ScenePlanningModule, VoiceoverModule, CaptionsModule | factual structure, source references |
| commentary | częściowo | tak | ScriptGenerationModule, CaptionsModule | voice + commentary tone |
| listicle | częściowo | tak | OutlineModule, ScenePlanningModule, CaptionsModule | enumerated structure |

## 5. Duration Profiles

### 15–30 seconds
- Przewidywana długość skryptu: około 35–70 słów.
- Liczba scen: 3–6.
- Liczba assetów: 3–6.
- Voiceover: krótki, dynamiczny, wysoki pacing.
- Napisy: krótkie linie, szybkie tempo.
- Wpływ na render: niski koszt renderu, szybki preview.

### 60 seconds
- Przewidywana długość skryptu: około 100–140 słów.
- Liczba scen: 6–10.
- Liczba assetów: 6–10.
- Voiceover: średnia długość, bardziej złożona synchronizacja.
- Napisy: większy nacisk na czytelność i podział na linie.
- Wpływ na render: średni koszt i czas.

### 3–5 minutes
- Przewidywana długość skryptu: około 400–700 słów.
- Liczba scen: 10–20+.
- Liczba assetów: 10–20+.
- Voiceover: długi, wymaga robustnego timingu i możliwej segmentacji.
- Napisy: znacznie większa złożoność, podział na bloki.
- Wpływ na render: duży koszt renderu i większe obciążenie pamięci.

### 8–15 minutes
- Przewidywana długość skryptu: 1000+ słów.
- Liczba scen: 20+.
- Liczba assetów: 20+.
- Voiceover: bardzo duży workload, wymagany podział na chunki.
- Napisy: duże wymagania dla layoutu i stabilności.
- Wpływ na render: wysoki koszt, potrzeba chunking i job queue.

### Custom duration
- Przewidywana długość skryptu: zależna od użytkownika.
- Liczba scen: dynamiczna.
- Liczba assetów: dynamiczna.
- Voiceover: zależna od duration profile i providerów.
- Napisy: zależne od długości i layoutu.
- Wpływ na render: zależny od wariantu.

## 6. Provider Abstraction

### LLM provider
- Gdzie jest używany: obecnie nie ma widocznej integracji LLM w repo; rekomendacja dla future modules.
- Czy jest hardcoded: nie, brak implementacji.
- Abstrakcja: LLMProvider interface z methodami generateText, summarize, rewrite, classify.
- Alternatywne providery: OpenAI, Anthropic, Azure OpenAI, local models.

### TTS provider
- Gdzie jest używany: repo nie generuje TTS, ale zakłada istnienie plików voiceover i timingu na bazie audio.
- Czy jest hardcoded: nie, brak implementacji.
- Abstrakcja: TTSProvider z metodą synthesize.
- Alternatywne providery: ElevenLabs, Azure Speech, OpenAI TTS, Coqui, lokalne TTS.

### Image/video asset provider
- Gdzie jest używany: [image_prep/cli.py](image_prep/cli.py) i [video_base_engine/projection.py](video_base_engine/projection.py).
- Czy jest hardcoded: częściowo; przycinanie lokalnych obrazów jest hardcoded, ale logicznie jest to provider assetów.
- Abstrakcja: AssetProvider z metodami listAssets, getAsset, generateAsset.
- Alternatywne providery: local filesystem, Unsplash/Pexels, internal media library, Midjourney/Flux-like image generators.

### Renderer
- Gdzie jest używany: [video_base_engine/assembler.py](video_base_engine/assembler.py), [subtitle_engine/components/delivery/burn_executor.py](subtitle_engine/components/delivery/burn_executor.py).
- Czy jest hardcoded: częściowo; używa moviepy i ffmpeg.
- Abstrakcja: RendererAdapter z metodami renderVideo, renderCaptions, renderThumbnail.
- Alternatywne providery: moviepy, FFmpeg, cloud renderer, Remotion-like stack.

### Storage
- Gdzie jest używany: lokalne pliki JSON / MP4 / ASS w [data](data).
- Czy jest hardcoded: tak, ścieżki są lokalne i ręcznie definiowane.
- Abstrakcja: ArtifactStorage z metodami save, load, list, delete.
- Alternatywne providery: local filesystem, S3, Azure Blob Storage, GCS.

### Publishing platform
- Gdzie jest używany: brak obecnej integracji.
- Czy jest hardcoded: nie.
- Abstrakcja: PublishingAdapter.
- Alternatywne providery: YouTube, TikTok, Instagram, LinkedIn.

### Transcription / caption provider
- Gdzie jest używany: [preparation_engine/domain/speech/transcription_service.py](preparation_engine/domain/speech/transcription_service.py), [subtitle_engine](subtitle_engine).
- Czy jest hardcoded: tak częściowo, bo Whisper jest bezpośrednio używany.
- Abstrakcja: TranscriptionProvider, CaptionProvider.
- Alternatywne providery: Whisper, WhisperX, Azure Speech, Deepgram.

## 7. Workflow Configuration

Proponowana struktura konfiguracji workflow dla przyszłej aplikacji:

```json
{
  "contentGenre": "story",
  "videoFormat": "short",
  "durationProfile": "15-30s",
  "targetPlatform": "youtube_shorts",
  "language": "pl",
  "tone": "dramatic",
  "enabledModules": [
    "scenePlanning",
    "voiceover",
    "captions",
    "videoRendering"
  ],
  "disabledModules": ["thumbnail", "publishing"],
  "providerConfig": {
    "llm": {"provider": "openai", "model": "gpt-4o-mini"},
    "tts": {"provider": "azure", "voice": "pl-PL-MajaNeural"},
    "asset": {"provider": "local"},
    "renderer": {"provider": "moviepy"},
    "transcription": {"provider": "whisper"},
    "storage": {"provider": "local"}
  },
  "renderConfig": {
    "resolution": [720, 1280],
    "fps": 24,
    "codec": "libx264"
  },
  "captionConfig": {
    "enabled": true,
    "style": "default",
    "highlightWords": true
  },
  "voiceConfig": {
    "enabled": true,
    "source": "tts"
  },
  "assetConfig": {
    "aspectRatio": "9:16",
    "assetMode": "local"
  }
}
```

## 8. Reuse and Refactor Plan

### Ready to reuse
- [scene_segmentation/planner.py](scene_segmentation/planner.py) – gotowy do użycia jako ScenePlanningModule.
- [preparation_engine/application/preparation_pipeline.py](preparation_engine/application/preparation_pipeline.py) – logiczna baza pod speech timing i kontrakty.
- [subtitle_engine](subtitle_engine) – gotowy materiał do CaptionsModule.
- [video_base_engine/assembler.py](video_base_engine/assembler.py) – dobry fundament pod VideoRenderingModule.

### Reuse after refactor
- [preparation_engine/cli.py](preparation_engine/cli.py) – wymaga zamiany z CLI na service layer / orchestration layer.
- [video_base_engine/projection.py](video_base_engine/projection.py) – należy wydzielić jako komponent asset mapping / scene projection.
- [image_prep/cli.py](image_prep/cli.py) – wymaga abstrahowania pod AssetSelectionModule / preprocessing module.
- [subtitle_engine/core/engine.py](subtitle_engine/core/engine.py) – wymaga uproszczenia i ujednolicenia interfejsów.

### Inspiration only
- [scene_segmentation/heuristics.py](scene_segmentation/heuristics.py) – ciekawe heurystyki, ale wymagają uporządkowania i testów.
- [subtitle_engine/components/semantic_planning](subtitle_engine/components/semantic_planning) – dobre wzorce semantyczne, ale zbyt mocno zagnieżdżone.
- [video_base_engine/effects.py](video_base_engine/effects.py) – dobre pomysły efektów, ale wymagają konfiguracji i provider abstraction.

### Do not reuse
- [run_part1.py](run_part1.py) i [run_part2.py](run_part2.py) – obecnie są skryptami workflowowymi o twardo zakodowanych ścieżkach i danym zestawie shortów.
- lokalne ścieżki hardcoded w CLI – nie nadają się do wielo-użytkownikowej aplikacji.
- duża liczba ścieżek i zależności od lokalnego filesystemu.

## 9. Suggested Architecture for Unified App

Proponowana architektura dla zunifikowanej aplikacji AI Content Studio powinna opierać się na:

- Core workflow engine: orchestrator pipeline’u z etapami i zależnościami.
- Module registry: rejestr modułów z ich konfiguracją, zależnościami i stanem enabled/disabled.
- Module interface: wspólny interfejs dla każdego modułu, np. execute(input, context) -> output.
- Provider adapters: warstwa abstrakcji nad LLM, TTS, rendererem, storage i publishingiem.
- Job queue: obsługa długich zadań renderingu i generation w tle.
- Project storage: przechowywanie projektów, ustawień workflowu i historii wersji.
- Artifact storage: przechowywanie pośrednich i końcowych artefaktów.
- Manual approval steps: etap akceptacji planu scen, scriptu, preview i finalnego renderu.
- Preview steps: preview storyboardu, preview napisów i preview renderu.
- Retry/error handling: retry dla providerów, fallbacki, timeouty i logi błędów.

W praktyce oznacza to, że obecne moduły z repo powinny zostać przeniesione do warstwy domain/service, a CLI powinno zostać zastąpione przez API oraz UI. Najbardziej naturalny podział to:

1. Workflow Orchestrator
2. Module Layer
3. Provider Layer
4. Storage Layer
5. Job/Queue Layer
6. Approval & Preview Layer

## 10. Spec Kit Requirements Candidates

### Functional Requirements
- FR-001: System shall allow users to configure a content workflow before generation.
- FR-002: System shall allow users to enable or disable captions generation.
- FR-003: System shall allow users to select a duration profile for generated video content.
- FR-004: System shall allow users to select a content genre such as news, story, educational, tutorial, or marketing.
- FR-005: System shall allow users to substitute providers for LLM, TTS, renderer, and asset generation.
- FR-006: System shall allow users to edit intermediate outputs at multiple workflow stages.
- FR-007: System shall support preview of storyboard, captions, and render before final export.
- FR-008: System shall support optional thumbnail and publishing modules.
- FR-009: System shall persist workflow runs, intermediate artifacts, and final exports.
- FR-010: System shall support retry and failure handling for long-running jobs.

### Non-Functional Requirements
- NFR-001: Extensibility – the system shall support adding new modules and providers without rewriting core orchestration.
- NFR-002: Provider abstraction – provider implementations shall be swappable through a common interface.
- NFR-003: Maintainability – core logic shall be separated from provider-specific implementation.
- NFR-004: Observability – the system shall expose job status, logs, and diagnostics for each module.
- NFR-005: Retry – the system shall support retry policies for transient failures.
- NFR-006: Cost control – the system shall expose limits and budgets for provider usage.
- NFR-007: Long-running jobs – the system shall support asynchronous execution for rendering and generation.
- NFR-008: Artifact storage – the system shall store intermediate and final artifacts in a durable backend.
- NFR-009: Testability – modules shall be testable independently from the full pipeline.

### Acceptance Criteria

#### Konfiguracji workflow
- Given użytkownik tworzy nowy projekt,
- When ustawi contentGenre, durationProfile, enabledModules i providerConfig,
- Then system zapisze konfigurację i uruchomi odpowiedni pipeline.

#### Wyłączenia napisów
- Given użytkownik wyłączy CaptionsModule,
- When uruchomi workflow,
- Then system pominie generation napisów i wygeneruje video bez napisów.

#### Wyboru długości filmu
- Given użytkownik wybierze profile 15–30s lub 60s,
- When uruchomi workflow,
- Then system dostosuje liczbę scen, długość skryptu i wymagania renderu do tego profilu.

#### Wyboru rodzaju contentu
- Given użytkownik wybierze genre story lub marketing,
- When uruchomi workflow,
- Then system zastosuje odpowiednie prompty, structure i moduły.

#### Podmiany providera
- Given użytkownik wybierze innego providera TTS lub LLM,
- When uruchomi workflow,
- Then system użyje nowego providera bez zmiany core workflow engine.

#### Renderowania z pominiętym modułem
- Given użytkownik wyłączy AssetSelectionModule lub VoiceoverModule,
- When uruchomi workflow,
- Then system przejdzie do kolejnych etapów z odpowiednimi fallbackami lub ręcznym wejściem użytkownika.

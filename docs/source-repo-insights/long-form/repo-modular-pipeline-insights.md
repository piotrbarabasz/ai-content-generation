# Modular Pipeline Insights

## 1. Pipeline Stages Found in This Repository

Poniżej zidentyfikowano etapy obecnego pipeline’u. Warto zauważyć, że repo nie ma jeszcze formalnej architektury modułowej; etapy są obecne jako kolejne funkcje i kroki w jednym orchestratorze.

### 1. Topic / brief / workflow configuration
- Opis: definiowanie tematu, długości materiału i podstawowych parametrów runu.
- Input: temat, target duration, words per minute, provider config.
- Output: stan workflow z parametrami startowymi.
- Najważniejsze pliki: [transcript/config.py](transcript/config.py), [transcript/main.py](transcript/main.py)
- Jawnie wydzielony jako moduł: nie
- Sklejony z innym etapem: tak, z orchestratorem pipeline’u
- Opcjonalny: tak, ale w praktyce jest wymagany dla działania

### 2. Source ingestion and manifest validation
- Opis: walidacja listy źródeł oraz tworzenie zbioru dokumentów wejściowych.
- Input: manifest źródeł w [transcript/data/input/sources.jsonl](transcript/data/input/sources.jsonl)
- Output: lista valid/invalid źródeł, raw documents, curated corpus
- Najważniejsze pliki: [transcript/pipeline/ingestion.py](transcript/pipeline/ingestion.py)
- Jawnie wydzielony jako moduł: tak, ale w sensie funkcjonalnym, nie jako osobna klasa interfejsu
- Sklejony z innym etapem: częściowo
- Opcjonalny: tak, w przypadku gdy użytkownik wprowadza własne źródła lub już ma korpus

### 3. Raw fetch / corpus build
- Opis: pobranie dokumentów źródłowych i utworzenie lokalnego korpusu curated.
- Input: źródła z manifestu
- Output: pliki JSON/JSONL w [transcript/data/input/raw](transcript/data/input/raw) i [transcript/data/input/curated](transcript/data/input/curated)
- Najważniejsze pliki: [transcript/pipeline/ingestion.py](transcript/pipeline/ingestion.py), [transcript/utils/io.py](transcript/utils/io.py)
- Jawnie wydzielony jako moduł: nie
- Sklejony z innym etapem: tak, z ingestion
- Opcjonalny: tak

### 4. Research / retrieval (RAG)
- Opis: zbieranie i selekcja faktów oraz kontekstu do dalszej narracji.
- Input: temat, korpus danych
- Output: notatki RAG, summary, lista faktów
- Najważniejsze pliki: [transcript/pipeline/rag.py](transcript/pipeline/rag.py)
- Jawnie wydzielony jako moduł: częściowo
- Sklejony z innym etapem: tak, z dossier i planning
- Opcjonalny: tak, ale dla research-based content jest bardzo ważny

### 5. Story dossier / structured facts
- Opis: normalizacja wyników researchu do ustrukturyzowanego modelu faktów i relacji.
- Input: wyniki RAG
- Output: dossier z timeline, key people, key places, confirmed/disputed facts
- Najważniejsze pliki: [transcript/pipeline/dossier.py](transcript/pipeline/dossier.py)
- Jawnie wydzielony jako moduł: tak, logicznie
- Sklejony z innym etapem: częściowo
- Opcjonalny: tak, ale dla wielu workflowów bardzo przydatny

### 6. Narrative planning / outline
- Opis: tworzenie planu segmentów, subsegmentów i struktur narracyjnych.
- Input: dossier + target duration
- Output: plan narracji z target word count, role segments, visual motifs
- Najważniejsze pliki: [transcript/pipeline/planner.py](transcript/pipeline/planner.py)
- Jawnie wydzielony jako moduł: tak
- Sklejony z innym etapem: częściowo z script generation
- Opcjonalny: tak, w prostych workflowach można go pominąć

### 7. Scene planning / subsegment planning
- Opis: szczegółowe planowanie scen i bloków treści.
- Input: plan narracji
- Output: subsegments z required facts i transition goals
- Najważniejsze pliki: [transcript/pipeline/planner.py](transcript/pipeline/planner.py), [transcript/pipeline/segment_writer.py](transcript/pipeline/segment_writer.py)
- Jawnie wydzielony jako moduł: nie, jest częścią planner + writer
- Sklejony z innym etapem: tak
- Opcjonalny: tak

### 8. Script generation
- Opis: tworzenie draftu skryptu w segmentach/subsegmentach.
- Input: plan narracji + dossier + opcjonalnie research
- Output: segmenty i subsegmenty tekstowe
- Najważniejsze pliki: [transcript/pipeline/segment_writer.py](transcript/pipeline/segment_writer.py)
- Jawnie wydzielony jako moduł: tak, logicznie
- Sklejony z innym etapem: tak z editing i QA
- Opcjonalny: nie; dla aplikacji content generation jest kluczowy

### 9. Editing and merge
- Opis: łączenie segmentów w spójny transcript i usuwanie problematycznych fraz.
- Input: wygenerowane segmenty
- Output: final transcript
- Najważniejsze pliki: [transcript/pipeline/editor.py](transcript/pipeline/editor.py)
- Jawnie wydzielony jako moduł: tak
- Sklejony z innym etapem: tak z script generation
- Opcjonalny: tak, ale zwykle warto zostawić w workflow

### 10. QA / quality gate
- Opis: ocena jakości transkryptu i blokowanie publikacji przy problemach.
- Input: transcript + dossier + plan
- Output: raport QA i status publishability
- Najważniejsze pliki: [transcript/pipeline/qa.py](transcript/pipeline/qa.py)
- Jawnie wydzielony jako moduł: tak
- Sklejony z innym etapem: częściowo
- Opcjonalny: tak, ale w produkcji powinien być domyślnie włączony

### 11. Voiceover preparation
- Opis: przygotowanie transkryptu do syntezy mowy.
- Input: final transcript
- Output: cleaned transcript, chunk metadata
- Najważniejsze pliki: [voiceover/services/text_cleaner.py](voiceover/services/text_cleaner.py), [voiceover/services/transcript_loader.py](voiceover/services/transcript_loader.py), [voiceover/services/chunker.py](voiceover/services/chunker.py)
- Jawnie wydzielony jako moduł: częściowo
- Sklejony z innym etapem: tak
- Opcjonalny: tak

### 12. TTS synthesis
- Opis: renderowanie audio z treści na lokalnym lub zewnętrznym backendzie.
- Input: cleaned transcript chunks
- Output: pliki WAV per chunk i final voiceover
- Najważniejsze pliki: [voiceover/application/generate_voiceover.py](voiceover/application/generate_voiceover.py), [voiceover/models/kokoro_tts.py](voiceover/models/kokoro_tts.py)
- Jawnie wydzielony jako moduł: tak, logicznie
- Sklejony z innym etapem: częściowo z voiceover pipeline
- Opcjonalny: tak

### 13. Audio assembly / export
- Opis: łączenie fragmentów audio w jeden plik.
- Input: chunk audio files
- Output: final voiceover audio
- Najważniejsze pliki: [voiceover/services/audio_concatenator.py](voiceover/services/audio_concatenator.py)
- Jawnie wydzielony jako moduł: częściowo
- Sklejony z innym etapem: tak
- Opcjonalny: tak

### 14. Metadata / artifact export
- Opis: zapis artefaktów pośrednich i finalnych.
- Input: wyniki etapów
- Output: pliki JSON/TXT/WAV
- Najważniejsze pliki: [transcript/utils/io.py](transcript/utils/io.py), [voiceover/services/artifact_writer.py](voiceover/services/artifact_writer.py)
- Jawnie wydzielony jako moduł: nie
- Sklejony z innym etapem: tak
- Opcjonalny: nie; jest podstawą traceability i replay

## 2. Module Candidates

Poniżej propozycja modułów, które da się wydzielić z obecnego repo. Część z nich wynika bezpośrednio z kodu, część jest rekomendacją architektoniczną.

### BriefModule
- Opis: przyjmuje temat, genre, tone, duration i tworzy brief projektu.
- Odpowiedzialność: ustala cele contentu, audience, constraints i parametry workflow.
- Input schema: {topic, contentGenre, durationProfile, targetPlatform, language, tone, audience}
- Output schema: {briefId, topic, objective, constraints, successCriteria}
- Config schema: {contentGenre, durationProfile, targetPlatform, tone, language}
- Dependencies: config, optional project storage
- Wynika z kodu: rekomendacja
- Status: required

### ResearchModule
- Opis: odpowiada za sourcing, walidację źródeł, retrieval i tworzenie research context.
- Odpowiedzialność: dostarcza faktów i źródeł do dalszych etapów.
- Input schema: {brief, sourceManifest, sourceFiles}
- Output schema: {researchArtifacts, facts, citations, warnings}
- Config schema: {sourcePolicy, maxSources, allowWebFetch, providerConfig}
- Dependencies: Ingestion, RAG provider, storage
- Wynika z kodu: tak
- Status: conditional; required for factual or news-like content

### IdeaGenerationModule
- Opis: generuje warianty pomysłów/angle’ów contentu na podstawie briefu.
- Odpowiedzialność: wspiera wybór kierunku narracji.
- Input schema: {brief, tone, audience}
- Output schema: {ideas, selectedIdea, rationale}
- Config schema: {ideaCount, creativity, riskPolicy}
- Dependencies: LLM provider
- Wynika z kodu: rekomendacja
- Status: optional

### ScriptGenerationModule
- Opis: generuje skrypt na podstawie planu i researchu.
- Odpowiedzialność: tworzy drafty segmentów/subsegmentów i finalny skrypt.
- Input schema: {brief, research, outline, styleProfile}
- Output schema: {script, scenes, sceneText, metadata}
- Config schema: {voiceTone, targetWordCount, contentGenre, modelConfig}
- Dependencies: LLM provider, outline/planning data
- Wynika z kodu: tak
- Status: required

### OutlineModule
- Opis: buduje outline scen i narracyjnych bloków.
- Odpowiedzialność: rozdziela skrypt na logiczne sekcje.
- Input schema: {brief, research, durationProfile}
- Output schema: {outline, scenes, sceneGoals, sceneOrder}
- Config schema: {durationProfile, sceneCount, pacingStyle}
- Dependencies: planning rules + optional LLM
- Wynika z kodu: tak
- Status: conditional; may be skipped for very simple content

### ScenePlanningModule
- Opis: planuje konkretne sceny, subsceny, transitions i elementy wizualne.
- Odpowiedzialność: precyzuje strukturę całości.
- Input schema: {outline, brief, research}
- Output schema: {scenePlan, shotPlan, visualMotifs, transitions}
- Config schema: {sceneGranularity, visualStyle}
- Dependencies: outline module + asset hints
- Wynika z kodu: tak
- Status: optional/conditional

### AssetSelectionModule
- Opis: dobiera assety, grafiki, klipy i materiały wizualne.
- Odpowiedzialność: mapuje sceny na media.
- Input schema: {scenePlan, brief, assetPreferences}
- Output schema: {assetAssignments, assetList, missingAssets}
- Config schema: {assetProvider, style, licensingPolicy}
- Dependencies: asset provider
- Wynika z kodu: rekomendacja
- Status: optional/conditional

### VoiceoverModule
- Opis: przygotowuje tekst do TTS i generuje lektor.
- Odpowiedzialność: tworzy audio z approved skryptu.
- Input schema: {script, voiceConfig, language}
- Output schema: {audioFile, transcriptCleaned, chunkMetadata}
- Config schema: {provider, voice, speed, language, chunkingRules}
- Dependencies: TTS provider, text cleaner, chunker
- Wynika z kodu: tak
- Status: optional

### CaptionsModule
- Opis: generuje napisy/captions do audio lub video.
- Odpowiedzialność: zapewnia dostępność i formatowanie napisów.
- Input schema: {script, audioFile, timingProfile}
- Output schema: {captions, subtitleFile, srt/vtt}
- Config schema: {provider, style, language, placement}
- Dependencies: transcription/caption provider
- Wynika z kodu: rekomendacja
- Status: optional

### VideoRenderingModule
- Opis: renderuje finalne video na podstawie audio, assetów i napisów.
- Odpowiedzialność: kompozycja końcowego outputu.
- Input schema: {script, assets, audio, captions, renderConfig}
- Output schema: {videoFile, previewUrl, metadata}
- Config schema: {format, resolution, fps, codec, aspectRatio}
- Dependencies: renderer provider
- Wynika z kodu: rekomendacja
- Status: conditional; required only for video output

### ThumbnailModule
- Opis: generuje miniaturę dla finalnego video.
- Odpowiedzialność: tworzy thumbnail i warianty.
- Input schema: {videoPreview, brief, style}
- Output schema: {thumbnailFile, variants}
- Config schema: {size, style, brandRules}
- Dependencies: image provider or renderer
- Wynika z kodu: rekomendacja
- Status: optional

### ExportModule
- Opis: eksportuje artefakty do standardowych formatów.
- Odpowiedzialność: zapis wyników do plików/packów i metadata.
- Input schema: {script, audio, captions, video, metadata}
- Output schema: {exportBundle, manifest}
- Config schema: {formats, destinations}
- Dependencies: storage
- Wynika z kodu: tak
- Status: required

### PublishingModule
- Opis: publikuje gotowy materiał do platform.
- Odpowiedzialność: wysyła output do kanałów publikacji.
- Input schema: {exportBundle, targetPlatform, credentials}
- Output schema: {publishStatus, publishId, links}
- Config schema: {platform, channel, policy}
- Dependencies: publishing provider
- Wynika z kodu: rekomendacja
- Status: optional

### MetadataModule
- Opis: tworzy metadata, tagi, opis, SEO i struktury dla eksportu.
- Odpowiedzialność: wspiera discoverability i downstream processing.
- Input schema: {brief, script, assets, exportConfig}
- Output schema: {metadata, tags, title, description}
- Config schema: {seoPolicy, brandProfile, language}
- Dependencies: optional LLM or rules engine
- Wynika z kodu: rekomendacja
- Status: conditional

## 3. Enable / Disable Behavior

### BriefModule
- Włączony: tworzy brief projektu i ustawia workflow defaults.
- Wyłączony: użytkownik musi ręcznie dostarczyć temat i ograniczenia; pipeline może działać, ale bez jasno zdefiniowanego kontekstu.
- Ręczne dane: topic, audience, rules, duration.
- Pipeline dalej: tak, ale z większym ryzykiem braku spójności.
- Błędy: brak celu, brak ograniczeń, niejednoznaczny brief.

### ResearchModule
- Włączony: pobiera i filtruje źródła, buduje context.
- Wyłączony: pipeline może kontynuować z własnym promptem lub manualnym briefem, ale bez groundingu.
- Ręczne dane: własne notatki, źródła lub gotowy context.
- Pipeline dalej: tak, dla contentu kreatywnego.
- Błędy: brak źródeł, słaby grounding, wysokie ryzyko hallucination.

### IdeaGenerationModule
- Włączony: generuje warianty pomysłów i kąty narracyjne.
- Wyłączony: użytkownik wybiera ideę ręcznie lub pipeline przechodzi od razu do skryptu.
- Ręczne dane: selected angle lub prompt.
- Pipeline dalej: tak.
- Błędy: brak decyzji, zbyt szeroki zakres tematu.

### ScriptGenerationModule
- Włączony: generuje skrypt i sceny.
- Wyłączony: użytkownik może wprowadzić własny skrypt lub skorzystać z template’u.
- Ręczne dane: własny tekst lub outline.
- Pipeline dalej: tak, jeśli input jest dostarczony.
- Błędy: brak treści, niezgodność z briefem, zbyt krótki lub zbyt długi output.

### OutlineModule
- Włączony: daje strukturę narracji i plan scen.
- Wyłączony: pipeline może przejść do skryptu z prostym promptem lub bezpośrednio do single-pass generation.
- Ręczne dane: lista scen lub storyboard.
- Pipeline dalej: tak.
- Błędy: słaby pacing, brak spójności.

### ScenePlanningModule
- Włączony: przypisuje rolę, purpose, visual motifs i transitiony.
- Wyłączony: skrypt może być generowany bez precyzyjnej struktury, ale mniej przewidywalny.
- Ręczne dane: storyboard lub lista scene beats.
- Pipeline dalej: tak.
- Błędy: brak spójności między scenami, trudny montaż.

### AssetSelectionModule
- Włączony: dobiera assety i wypełnia plan wizualny.
- Wyłączony: render może działać bez assetów lub z domyślnymi placeholderami.
- Ręczne dane: własne pliki media lub brakujące assety.
- Pipeline dalej: tak, ale mniej atrakcyjnie wizualnie.
- Błędy: puste assety, brak licencji, mismatch stylistyczny.

### VoiceoverModule
- Włączony: generuje lektor i audio.
- Wyłączony: użytkownik może uploadować własny plik audio albo system wygeneruje video bez lektora.
- Ręczne dane: audio file, transcript bez voiceoveru.
- Pipeline dalej: tak.
- Błędy: brak audio, niezgodność timingu, zły język lub głos.

### CaptionsModule
- Włączony: generuje napisy i synchronizację.
- Wyłączony: render powinien pominąć napisy lub użyć prostych subtitles z fallbacku.
- Ręczne dane: własne napisy lub brak napisów.
- Pipeline dalej: tak.
- Błędy: brak synchronizacji, nieczytelne formatowanie.

### VideoRenderingModule
- Włączony: tworzy finalne video z audio, assetów i napisów.
- Wyłączony: pipeline kończy się na skrypcie/audio/metadata; brak finalnego video.
- Ręczne dane: gotowy video zewnętrzny lub brak renderu.
- Pipeline dalej: tak, ale bez końcowego outputu video.
- Błędy: brak kompatybilności formatów, problemy z timingiem, niekompatybilne assety.

### ThumbnailModule
- Włączony: tworzy miniaturę.
- Wyłączony: można użyć placeholdera lub wysłać bez miniature.
- Ręczne dane: własna miniatura.
- Pipeline dalej: tak.
- Błędy: brak thumbnailu, zły framing.

### ExportModule
- Włączony: zapisuje gotowe artefakty w standardowych formatach.
- Wyłączony: output pozostaje w systemie, ale brakuje eksportu dla użytkownika.
- Ręczne dane: brak.
- Pipeline dalej: tak, ale bez finalnego pakietu.
- Błędy: brak kompatybilności formatów, niepełny export.

### PublishingModule
- Włączony: publikuje do platform.
- Wyłączony: output jest tylko lokalnie dostępny.
- Ręczne dane: credentials, handle kanału, publish metadata.
- Pipeline dalej: tak.
- Błędy: brak autoryzacji, policy violation, rate limits.

## 4. Content Type and Genre Support

| Typ contentu | Czy repo obsługuje | Czy można obsłużyć po refaktorze | Potrzebne moduły | Wymagane struktury / prompty |
|---|---|---|---|---|
| Short video | częściowo | tak | Brief, ScriptGeneration, Voiceover, Captions, Render, Thumbnail | krótkie outline, niskie targetWordCount, szybkie hooki |
| Long-form video | tak, w formie prototypu | tak | Research, Outline, ScenePlanning, ScriptGeneration, QA, Voiceover, Render | długi outline, segmenty, storyline, timeline |
| News | tak, w formie research-driven storytelling | tak | Research, Outline, ScriptGeneration, QA | źródła, factual constraints, timeliness |
| Story | tak, w formie narrative storytelling | tak | Research, Outline, ScriptGeneration, QA | strong narrative beats, tension, conflict |
| Educational | częściowo | tak | Research, Outline, ScriptGeneration, Voiceover, Captions | lesson structure, examples, glossary |
| Tutorial | częściowo | tak | ScriptGeneration, ScenePlanning, AssetSelection, Voiceover, Captions, Render | step-by-step structure, visual instructions |
| Marketing | częściowo | tak | Brief, ScriptGeneration, AssetSelection, Thumbnail, Publish | CTA, brand tone, call to action |
| Documentary | tak, w sensie narracyjnego long-form | tak | Research, Dossier, Outline, ScriptGeneration, QA | factual grounding, documentary pacing |
| Commentary | częściowo | tak | ScriptGeneration, Voiceover, Captions | tonal style, opinion framing |
| Listicle | częściowo | tak | Outline, ScriptGeneration, AssetSelection | numbered beats, punchy structure |

Wniosek: obecne repo najlepiej wspiera long-form narrative oraz news/story-like content. Pozostałe typy wymagają dodatkowej konfiguracji i bardziej uniwersalnych modułów promptowych.

## 5. Duration Profiles

| Profil | Przewidywana długość skryptu | Liczba scen | Liczba assetów | Voiceover | Napisy | Wpływ na render |
|---|---:|---:|---:|---|---|---|
| 15–30 seconds | ~50–70 słów | 1–2 | 0–3 | krótki, szybki delivery | wymagane lub opcjonalne | bardzo prosty render |
| 60 seconds | ~120–140 słów | 3–4 | 3–6 | standardowy | zalecane | standardowy render |
| 3–5 minutes | ~420–700 słów | 4–8 | 6–12 | dłuższy, chunking | wymagane | większa złożoność timingu |
| 8–15 minutes | ~1120–2100 słów | 8–12+ | 10–20+ | wymaga chunkowania i QA | bardzo zalecane | wysokie wymagania montażowe |
| custom duration | zależne od inputu | zależne | zależne | zależne | zależne | zależne |

W obecnym repo najłatwiej obsłużyć profil 8–15 minut, ponieważ pipeline ma już target word count, segmenty i QA. Profily 15–30s i 60s wymagają osobnych promptów i zredukowanych strategii planowania.

## 6. Provider Abstraction

### LLM provider
- Gdzie używany: [transcript/utils/llm.py](transcript/utils/llm.py), [transcript/pipeline/rag.py](transcript/pipeline/rag.py), [transcript/pipeline/dossier.py](transcript/pipeline/dossier.py), [transcript/pipeline/planner.py](transcript/pipeline/planner.py), [transcript/pipeline/segment_writer.py](transcript/pipeline/segment_writer.py), [transcript/pipeline/editor.py](transcript/pipeline/editor.py), [transcript/pipeline/qa.py](transcript/pipeline/qa.py)
- Czy hardcoded: częściowo; provider jest konfigurowalny przez env vars, ale logika jest silnie osadzona w module
- Abstrakcja: LLMProvider / LLMService z metodami generateText, generateJson, stream
- Alternatywne providery: OpenRouter, Ollama, local Transformers, Azure OpenAI, Anthropic, Gemini

### TTS provider
- Gdzie używany: [voiceover/models/kokoro_tts.py](voiceover/models/kokoro_tts.py), [voiceover/application/generate_voiceover.py](voiceover/application/generate_voiceover.py)
- Czy hardcoded: częściowo; adapter jest już obecny, ale nadal jest mocno związany z lokalnym backendem
- Abstrakcja: TTSProvider z metodami synthesizeToFile i getVoices
- Alternatywne providery: ElevenLabs, Azure Speech, AWS Polly, OpenAI TTS, Coqui

### Image / video asset provider
- Gdzie używany: brak implementacji w repo
- Czy hardcoded: nie
- Abstrakcja: AssetProvider z metodami search, getAsset, licenseInfo
- Alternatywne providery: Pexels, Unsplash, Storyblocks, Shutterstock, internal asset library

### Renderer
- Gdzie używany: brak implementacji w repo
- Czy hardcoded: nie
- Abstrakcja: VideoRendererProvider z metodami renderVideo, renderThumbnail, composeTimeline
- Alternatywne providery: FFmpeg-based renderer, Remotion, Adobe, cloud rendering

### Storage
- Gdzie używany: [transcript/utils/io.py](transcript/utils/io.py), [voiceover/services/artifact_writer.py](voiceover/services/artifact_writer.py)
- Czy hardcoded: tak, obecnie pliki lokalne
- Abstrakcja: StorageProvider z metodami saveArtifact, readArtifact, listArtifacts, deleteArtifact
- Alternatywne providery: lokalny filesystem, S3-compatible storage, Azure Blob, GCS

### Publishing platform
- Gdzie używany: brak implementacji
- Czy hardcoded: nie
- Abstrakcja: PublishingProvider z metodami publishVideo, publishShortForm, updateMetadata
- Alternatywne providery: YouTube, TikTok, LinkedIn, Facebook, WordPress

### Transcription / captions provider
- Gdzie używany: brak implementacji
- Czy hardcoded: nie
- Abstrakcja: CaptionProvider / TranscriptionProvider z metodami transcribe, generateSubtitles, alignToAudio
- Alternatywne providery: Whisper, AssemblyAI, Deepgram, Azure Speech

## 7. Workflow Configuration

Proponowana struktura konfiguracji workflow dla przyszłej aplikacji powinna być oddzielona od kodu i wystawiona jako obiekt workflow definition. Przykład:

```json
{
  "contentGenre": "documentary",
  "videoFormat": "16:9",
  "durationProfile": "8-15 minutes",
  "targetPlatform": "youtube",
  "language": "en",
  "tone": "informative",
  "enabledModules": [
    "brief",
    "research",
    "outline",
    "scriptGeneration",
    "qa",
    "voiceover",
    "captions",
    "render"
  ],
  "disabledModules": ["thumbnail", "publishing"],
  "providerConfig": {
    "llm": {"provider": "openrouter", "model": "gpt-4o-mini"},
    "tts": {"provider": "kokoro", "voice": "af_bella"},
    "asset": {"provider": "internal"},
    "renderer": {"provider": "ffmpeg"},
    "storage": {"provider": "filesystem"}
  },
  "renderConfig": {
    "resolution": "1920x1080",
    "fps": 30,
    "codec": "h264"
  },
  "captionConfig": {
    "enabled": true,
    "format": "srt",
    "language": "en"
  },
  "voiceConfig": {
    "enabled": true,
    "speed": 0.9,
    "language": "en"
  },
  "assetConfig": {
    "enabled": true,
    "style": "cinematic",
    "licensingPolicy": "commercial"
  }
}
```

## 8. Reuse and Refactor Plan

### Ready to reuse
- [transcript/main.py](transcript/main.py) — obecna odpowiedzialność: orchestracja pipeline. Docelowy moduł: CoreWorkflowEngine. Wymagany refaktor: wydzielenie zadań do modułów, wstrzykiwanie zależności, stan workflow. Poziom ryzyka: medium.
- [transcript/utils/io.py](transcript/utils/io.py) — obecna odpowiedzialność: zapis artefaktów. Docelowy moduł: StorageProvider / ArtifactStore. Wymagany refaktor: abstrakcja na storage backend. Poziom ryzyka: low.
- [voiceover/services/chunker.py](voiceover/services/chunker.py) — obecna odpowiedzialność: chunking transcriptu. Docelowy moduł: VoiceoverModule. Wymagany refaktor: wejście/wyjście zgodne z module contract. Poziom ryzyka: low.
- [voiceover/services/audio_concatenator.py](voiceover/services/audio_concatenator.py) — obecna odpowiedzialność: łączenie audio. Docelowy moduł: AudioAssembly / Render podmodule. Wymagany refaktor: ustandaryzowanie interfejsu. Poziom ryzyka: low-medium.

### Reuse after refactor
- [transcript/pipeline/ingestion.py](transcript/pipeline/ingestion.py) — obecna odpowiedzialność: sourcing, walidacja i curated corpus. Docelowy moduł: ResearchModule. Wymagany refaktor: oddzielenie od lokalnych plików, zdefiniowanie input/output schemas. Poziom ryzyka: medium.
- [transcript/pipeline/rag.py](transcript/pipeline/rag.py) — obecna odpowiedzialność: retrieval i research notes. Docelowy moduł: ResearchModule. Wymagany refaktor: przepisanie na provider-agnostic retrieval engine. Poziom ryzyka: high.
- [transcript/pipeline/dossier.py](transcript/pipeline/dossier.py) — obecna odpowiedzialność: strukturyzacja faktów. Docelowy moduł: ResearchModule / MetadataModule. Wymagany refaktor: formalne schematy danych. Poziom ryzyka: medium.
- [transcript/pipeline/planner.py](transcript/pipeline/planner.py) — obecna odpowiedzialność: outline + scene planning. Docelowy moduł: OutlineModule / ScenePlanningModule. Wymagany refaktor: konfiguracja typu contentu i długości. Poziom ryzyka: medium.
- [transcript/pipeline/segment_writer.py](transcript/pipeline/segment_writer.py) — obecna odpowiedzialność: generowanie skryptu. Docelowy moduł: ScriptGenerationModule. Wymagany refaktor: provider abstraction, template system, better validations. Poziom ryzyka: high.
- [transcript/pipeline/editor.py](transcript/pipeline/editor.py) — obecna odpowiedzialność: merge i polish. Docelowy moduł: ScriptGenerationModule / PostProcessingModule. Wymagany refaktor: oddzielenie reguł od generatora. Poziom ryzyka: medium.
- [transcript/pipeline/qa.py](transcript/pipeline/qa.py) — obecna odpowiedzialność: QA. Docelowy moduł: ValidationModule. Wymagany refaktor: policy-based scoring. Poziom ryzyka: medium.
- [voiceover/application/generate_voiceover.py](voiceover/application/generate_voiceover.py) — obecna odpowiedzialność: orchestration voiceover. Docelowy moduł: VoiceoverModule. Wymagany refaktor: wejście/wyjście zgodne z module contract. Poziom ryzyka: medium.
- [voiceover/models/kokoro_tts.py](voiceover/models/kokoro_tts.py) — obecna odpowiedzialność: TTS backend. Docelowy moduł: VoiceoverModule / TTSProvider. Wymagany refaktor: oddzielenie provider adaptera. Poziom ryzyka: medium.

### Inspiration only
- [README.MD](README.MD) — opisuje ideę produktu, ale nie jest jeszcze architekturą systemu.
- część heurystyk narracyjnych i markerów w [transcript/pipeline](transcript/pipeline) — są wartościowe jako domenowe reguły, ale nie powinny być reuse’owane bez przemyślenia.

### Do not reuse
- hardcoded topic-specific logic związany z konkretnym case’em (np. Epstein) w [transcript/pipeline](transcript/pipeline)
- demo artefakty i testy z jednego use case’u jako pełny model produkcyjny
- tymczasowe fallbacki LLM i mocki, które nie są wystarczająco stabilne do produkcji

## 9. Suggested Architecture for Unified App

Na podstawie obecnego repo proponuje się następującą architekturę.

### Core workflow engine
- silnik uruchamiający workflow zgodnie z konfiguracją użytkownika
- rozpoznaje, które moduły są enabled/disabled
- buduje DAG lub pipeline execution plan

### Module registry
- rejestr modułów: ResearchModule, ScriptGenerationModule, VoiceoverModule itd.
- każdy moduł implementuje wspólny interfejs

### Module interface
- każda jednostka przyjmuje context i zwraca ModuleResult
- moduł ma własny stan, input/output schemas, retry policy, artifact outputs

### Provider adapters
- LLMProvider, TTSProvider, AssetProvider, RendererProvider, CaptionProvider, PublishingProvider, StorageProvider
- adaptery są wymienne i konfigurowalne

### Job queue
- rekomendacja: oddzielić długie operacje (research, generation, render) do kolejki zadań
- każdy job powinien mieć status, retry policy, logs i artifact references

### Project storage
- projekt, workflow config, user state, generation history
- rekomendacja: relacyjna baza danych lub dokumentowa baza z wersjonowaniem

### Artifact storage
- pliki intermedialne i finalne powinny być przechowywane poza lokalnym filesystemem w bardziej skalowalnym store
- każdy artifact powinien mieć ID, typ, wersję i link do joba

### Manual approval steps
- po research, po script draft, po voiceover, po render
- approval step powinien być pierwszym checkpointem jakości i brand safety

### Preview steps
- preview skryptu, preview audio, preview storyboard, preview render
- umożliwia szybką iterację bez pełnego rerendera

### Retry and error handling
- każdy moduł powinien mieć retry policy, backoff, error classification i checkpoint artifact
- błędy w module nie powinny zatrzymywać całego workflow bez sensownej opcji fallbacku

## 10. Spec Kit Requirements Candidates

### Functional Requirements
- FR-001: System shall allow users to configure a content workflow before generation.
- FR-002: System shall allow users to enable or disable individual pipeline modules such as research, script generation, voiceover, captions, asset selection, rendering, thumbnail, and publishing.
- FR-003: System shall allow users to select a duration profile for generated content.
- FR-004: System shall allow users to select a content genre such as news, story, educational, tutorial, or marketing.
- FR-005: System shall allow users to select a provider for LLM, TTS, asset sourcing, rendering, and storage.
- FR-006: System shall support manual editing of intermediate outputs at each workflow stage.
- FR-007: System shall persist intermediate and final artifacts for each workflow run.
- FR-008: System shall support approval checkpoints before publishing or export.
- FR-009: System shall support partial workflow execution when optional modules are disabled.
- FR-010: System shall generate exportable outputs for text, audio, captions, and video where applicable.

### Non-Functional Requirements
- NFR-001: Extensibility — the system shall support adding new modules and providers without rewriting the core workflow engine.
- NFR-002: Provider abstraction — the system shall isolate provider-specific logic behind stable interfaces.
- NFR-003: Maintainability — module contracts and schemas shall be explicit and versioned.
- NFR-004: Observability — the system shall expose logs, status, and artifact references for each job.
- NFR-005: Retry — the system shall support configurable retries and failover for long-running modules.
- NFR-006: Cost control — the system shall track usage and cost estimates per module and provider.
- NFR-007: Long-running jobs — the system shall support asynchronous execution for generation and rendering.
- NFR-008: Artifact storage — the system shall store intermediate artifacts in a durable, queryable location.
- NFR-009: Testability — each module shall be testable independently with deterministic inputs and fixtures.

### Acceptance Criteria

#### Konfiguracja workflow
- Given użytkownik tworzy nowy projekt
- When ustawia genre, duration profile, enabled modules i provider config
- Then system zapisuje workflow definition i uruchamia zgodny pipeline

#### Wyłączenie napisów
- Given użytkownik wyłącza CaptionsModule
- When pipeline osiąga etap renderu
- Then system pomija generowanie napisów i renderuje video bez napisów

#### Wybór długości filmu
- Given użytkownik wybiera profil 60 seconds
- When system generuje outline i skrypt
- Then output ma odpowiednią liczbę scen, target word count i plan assetów

#### Wybór rodzaju contentu
- Given użytkownik wybiera contentGenre = educational
- When system uruchamia workflow
- Then moduły i prompty są dostosowane do edukacyjnej struktury treści

#### Podmiana providera
- Given użytkownik wybiera innego providera TTS lub LLM
- When workflow uruchamia odpowiedni moduł
- Then system używa nowego providera bez zmiany interfejsu modułu

#### Renderowanie z pominiętym modułem
- Given użytkownik wyłącza VoiceoverModule
- When pipeline dochodzi do renderu
- Then system renderuje video bez audio lub używa własnego pliku audio dostarczonego ręcznie

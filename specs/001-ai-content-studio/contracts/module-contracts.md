# Module Contracts

## Contract Shape
Each module MUST declare:
- input schema
- output schema
- config schema
- dependencies
- enabled/disabled behavior
- retry policy
- artifact outputs
- error behavior

## Core Module Contracts

### BriefModule
- Input: ContentBrief
- Output: ContentBrief with enriched objective, audience and constraints
- Config: preset, language, tone, duration profile
- Dependencies: none
- Retry: 1 retry for transient provider failures
- Artifacts: brief.json

### ResearchModule
- Input: ContentBrief, source manifest
- Output: research notes, source summary, dossier context
- Config: allow_research, max_sources, provider
- Dependencies: StorageProvider, LLMProvider
- Retry: 1 retry
- Artifacts: research.json

### DossierModule
- Input: research output
- Output: structured dossier facts and timeline
- Config: style profile
- Dependencies: ResearchModule output
- Retry: 1 retry
- Artifacts: dossier.json

### OutlineModule
- Input: brief, dossier
- Output: narrative outline and scene outline
- Config: duration profile, scene_count
- Dependencies: DossierModule
- Retry: 1 retry
- Artifacts: outline.json

### ScriptGenerationModule
- Input: brief, outline, research context
- Output: Script and NarrativeSegment list
- Config: provider, style profile, word target
- Dependencies: OutlineModule
- Retry: 2 retries
- Artifacts: script.txt, narrative_segments.json

### PostProcessingModule
- Input: script draft
- Output: cleaned script and normalized segments
- Config: cleanup rules
- Dependencies: ScriptGenerationModule
- Retry: 1 retry
- Artifacts: post_processed_script.txt

### QAModule
- Input: script, outline, dossier
- Output: QA report and approval state
- Config: thresholds
- Dependencies: PostProcessingModule
- Retry: 1 retry
- Artifacts: qa_report.json

### ScenePlanningModule
- Input: script, outline
- Output: RenderScene list and scene plan
- Config: platform, aspect ratio
- Dependencies: ScriptGenerationModule
- Retry: 1 retry
- Artifacts: scene_plan.json

### VoiceoverModule
- Input: script, voice settings
- Output: Voiceover and SpeechTimeline
- Config: TTS provider, voice profile, language
- Dependencies: ScriptGenerationModule
- Retry: 2 retries
- Artifacts: voiceover.wav, speech_timeline.json

### CaptionsModule
- Input: voiceover or script, scene plan
- Output: CaptionTrack
- Config: provider, style, language
- Dependencies: VoiceoverModule or ScriptGenerationModule
- Retry: 1 retry
- Artifacts: captions.ass or captions.json

### VideoRenderingModule
- Input: scene plan, voiceover, captions, assets
- Output: VideoRender
- Config: resolution, fps, codec
- Dependencies: ScenePlanningModule, VoiceoverModule
- Retry: 1 retry
- Artifacts: render.mp4

### ExportModule
- Input: workflow artifacts and metadata
- Output: ExportBundle
- Config: output dir, bundle format
- Dependencies: all completed artifacts
- Retry: 1 retry
- Artifacts: manifest.json, export bundle directory

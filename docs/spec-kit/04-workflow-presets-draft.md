# 04 Workflow Presets Draft

## Preset Model

Workflow presets should represent common combinations of modules, durations and review steps. They should be configurable but should not force a single fixed pipeline.

## 1. Short News
- default modules: BriefModule, ResearchModule, OutlineModule, ScenePlanningModule, CaptionsModule, VideoRenderingModule, ExportModule
- required modules: BriefModule, ScenePlanningModule, ExportModule
- optional modules: ResearchModule, VoiceoverModule, ThumbnailModule, MetadataModule
- duration profile: 15–60 seconds
- inputs: topic, source facts, target platform
- outputs: short video, captions, export bundle
- manual review steps: verify factual accuracy, check hook strength and review subtitle readability

## 2. Short Story
- default modules: BriefModule, ScriptGenerationModule, ScenePlanningModule, VoiceoverModule, CaptionsModule, VideoRenderingModule, ExportModule
- required modules: BriefModule, ScriptGenerationModule, ExportModule
- optional modules: ResearchModule, QAModule, ThumbnailModule
- duration profile: 15–60 seconds
- inputs: story premise, tone, language, optional reference assets
- outputs: short video, voiceover, captions, export bundle
- manual review steps: review pacing, emotional tone and scene transition quality

## 3. Long-form Documentary
- default modules: BriefModule, ResearchModule, DossierModule, OutlineModule, ScriptGenerationModule, PostProcessingModule, QAModule, VoiceoverModule, ExportModule, MetadataModule
- required modules: BriefModule, ResearchModule, ScriptGenerationModule, QAModule, ExportModule
- optional modules: ScenePlanningModule, AssetPlanningModule, AssetSelectionModule, CaptionsModule, VideoRenderingModule
- duration profile: 3–15 minutes
- inputs: topic, source list, target platform, tone
- outputs: long-form script, QA report, voiceover, export bundle
- manual review steps: review factual grounding, narrative flow, script length and voiceover pacing

## 4. Educational Explainer
- default modules: BriefModule, ResearchModule, OutlineModule, ScriptGenerationModule, PostProcessingModule, QAModule, VoiceoverModule, CaptionsModule, ExportModule, MetadataModule
- required modules: BriefModule, ResearchModule, OutlineModule, ExportModule
- optional modules: ScenePlanningModule, VideoRenderingModule, ThumbnailModule
- duration profile: 60 seconds to 5 minutes
- inputs: topic, learning objective, audience, tone
- outputs: script, voiceover, captions, export bundle, optional video
- manual review steps: review structure, clarity, examples and pacing for explanation quality

## 5. Marketing Video
- default modules: BriefModule, ScriptGenerationModule, ScenePlanningModule, AssetPlanningModule, AssetSelectionModule, VoiceoverModule, CaptionsModule, VideoRenderingModule, ThumbnailModule, ExportModule, MetadataModule
- required modules: BriefModule, ScriptGenerationModule, VideoRenderingModule, ExportModule
- optional modules: QAModule, PublishingModule
- duration profile: 15–90 seconds
- inputs: product or campaign brief, brand profile, platform, CTA
- outputs: marketing video, thumbnail, metadata, export bundle
- manual review steps: review brand tone, CTA clarity, visual consistency and compliance

## 6. Audio-only Story
- default modules: BriefModule, ScriptGenerationModule, PostProcessingModule, QAModule, VoiceoverModule, ExportModule, MetadataModule
- required modules: BriefModule, ScriptGenerationModule, VoiceoverModule, ExportModule
- optional modules: ResearchModule, DossierModule, OutlineModule
- duration profile: 1–10 minutes
- inputs: story premise, tone, language, narrator style
- outputs: audio file, cleaned transcript, export bundle
- manual review steps: review script quality, delivery pacing and audio clarity

## 7. Script-only Article
- default modules: BriefModule, ResearchModule, OutlineModule, ScriptGenerationModule, PostProcessingModule, QAModule, ExportModule, MetadataModule
- required modules: BriefModule, ScriptGenerationModule, ExportModule
- optional modules: DossierModule, VoiceoverModule
- duration profile: custom
- inputs: topic, sources, target audience, desired format
- outputs: script, metadata, export bundle
- manual review steps: review factual accuracy, structure, readability and editorial quality

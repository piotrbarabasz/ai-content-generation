# 03 Module Contracts Draft

## Module Architecture

The module architecture should be contract-based. Each module should be independently enableable, disableable and retryable, and each should produce explicit artifacts so that later modules can run against a stable interface.

## Module Contracts

### BriefModule
- id: brief
- name: BriefModule
- input schema: { topic, objective?, genre?, duration_profile?, target_platform?, language?, tone?, audience? }
- output schema: { brief_id, topic, objective, genre, duration_profile, target_platform, language, tone, audience, constraints }
- config schema: { default_language, default_tone, supported_genres, supported_platforms }
- dependencies: none
- enabled behavior: creates a structured brief and establishes workflow defaults
- disabled behavior: workflow continues only if the brief is supplied manually
- retry policy: one retry for missing or incomplete brief fields; otherwise escalate to manual review
- artifact outputs: ContentBrief, workflow snapshot
- error behavior: stop workflow and request missing required fields

### ResearchModule
- id: research
- name: ResearchModule
- input schema: { brief, sources?, source_manifest? }
- output schema: { research_notes, facts, citations, warnings }
- config schema: { providers, max_sources, allow_web_fetch, fact_checking_enabled }
- dependencies: provider integration, storage access
- enabled behavior: gathers source context and factual grounding
- disabled behavior: workflow continues using the brief and any supplied manual sources
- retry policy: retry on transient fetch or parsing errors; preserve partial results on failure
- artifact outputs: research notes, source manifest, fact list
- error behavior: continue with partial findings and mark confidence level

### DossierModule
- id: dossier
- name: DossierModule
- input schema: { research, brief }
- output schema: { dossier, key_people, key_places, confirmed_facts, disputed_facts, timeline }
- config schema: { detail_level, include_confidence, citation_required }
- dependencies: ResearchModule
- enabled behavior: normalizes research into a structured story dossier
- disabled behavior: workflow uses the research notes directly without dossier structuring
- retry policy: one retry on parsing or normalization errors
- artifact outputs: dossier JSON, structured facts
- error behavior: emit warnings and continue with reduced structure if facts are incomplete

### OutlineModule
- id: outline
- name: OutlineModule
- input schema: { brief, dossier?, script_text?, duration_profile }
- output schema: { outline, sections, scene_order, target_word_count }
- config schema: { max_sections, pacing_rules, duration_profile }
- dependencies: BriefModule, optional DossierModule
- enabled behavior: creates an outline or narrative structure for later script generation
- disabled behavior: workflow proceeds directly to script generation or manual structure input
- retry policy: one retry if the outline is too short or inconsistent
- artifact outputs: outline JSON, section plan
- error behavior: require manual review if the outline is too weak or incoherent

### ScriptGenerationModule
- id: script_generation
- name: ScriptGenerationModule
- input schema: { brief, outline, research?, dossier? }
- output schema: { script_text, script_version, language, word_count, tone }
- config schema: { provider, max_tokens, style_guidelines, target_word_count }
- dependencies: OutlineModule, optional ResearchModule
- enabled behavior: generates or refines the script text
- disabled behavior: workflow uses a supplied script or transcript
- retry policy: retry with a simplified prompt on generation failures
- artifact outputs: script text, metadata, revision history
- error behavior: stop and request manual script input if generation fails repeatedly

### PostProcessingModule
- id: post_processing
- name: PostProcessingModule
- input schema: { script_text, brief }
- output schema: { cleaned_script, revised_script, notes }
- config schema: { style_rules, language_rules, length_rules }
- dependencies: ScriptGenerationModule
- enabled behavior: cleans and adjusts the script for downstream use
- disabled behavior: raw script is passed through without refinement
- retry policy: one retry on formatting or normalization issues
- artifact outputs: cleaned transcript, revision notes
- error behavior: preserve the original script and flag the post-processing issue

### QAModule
- id: qa
- name: QAModule
- input schema: { script, brief, dossier?, outline }
- output schema: { qa_report, pass_fail, issues, recommendations }
- config schema: { strictness, fact_checking_enabled, required_checks }
- dependencies: ScriptGenerationModule, optional DossierModule
- enabled behavior: performs content quality review and blocks publish-ready output when needed
- disabled behavior: workflow proceeds without a formal QA gate
- retry policy: one retry with a reduced scope if the first pass fails due to transient issues
- artifact outputs: QA report, issue list
- error behavior: mark the output as requiring manual review

### ScenePlanningModule
- id: scene_planning
- name: ScenePlanningModule
- input schema: { script, brief, outline? }
- output schema: { scenes, narrative_segments, pacing_hints, semantic_roles }
- config schema: { max_scenes, pacing_profile, duration_profile }
- dependencies: ScriptGenerationModule, OutlineModule
- enabled behavior: splits the narrative into scenes or beats suitable for production
- disabled behavior: workflow uses the supplied outline or manual storyboard instead
- retry policy: one retry if the scene count or pacing is invalid
- artifact outputs: scene plan JSON, narrative segment map
- error behavior: return a minimal scene plan and flag manual review

### AssetPlanningModule
- id: asset_planning
- name: AssetPlanningModule
- input schema: { scenes, brief, target_platform }
- output schema: { asset_plan, visual_requirements, scene_asset_requests }
- config schema: { aspect_ratio, style_constraints, asset_policy }
- dependencies: ScenePlanningModule
- enabled behavior: defines what visual assets each scene needs
- disabled behavior: workflow proceeds without an explicit asset plan
- retry policy: one retry if the asset plan is incomplete
- artifact outputs: asset plan JSON
- error behavior: continue with placeholder assignments and flag the gap

### AssetSelectionModule
- id: asset_selection
- name: AssetSelectionModule
- input schema: { asset_plan, scenes, provider_config }
- output schema: { asset_assignments, selected_assets, missing_assets }
- config schema: { asset_provider, style_constraints, licensing_policy }
- dependencies: AssetPlanningModule
- enabled behavior: assigns concrete assets to scenes or requests new assets
- disabled behavior: workflow uses manual asset mapping or placeholder visuals
- retry policy: retry on provider errors; preserve partial mapping
- artifact outputs: asset assignment manifest
- error behavior: continue with missing assets flagged for review

### VoiceoverModule
- id: voiceover
- name: VoiceoverModule
- input schema: { script, brief, language, provider_config? }
- output schema: { voiceover_path, duration_seconds, provider_metadata }
- config schema: { provider, voice, speed, language, format }
- dependencies: ScriptGenerationModule, optional PostProcessingModule
- enabled behavior: generates or prepares a narrator audio track
- disabled behavior: workflow proceeds without voiceover, using silence or external audio
- retry policy: retry on synthesis or file generation errors
- artifact outputs: audio file, chunk metadata, cleaned transcript
- error behavior: mark audio as unavailable and continue if the workflow allows it

### SpeechTimingModule
- id: speech_timing
- name: SpeechTimingModule
- input schema: { script, voiceover, scenes? }
- output schema: { speech_timeline, scene_timeline, alignments }
- config schema: { alignment_method, precision, min_word_gap }
- dependencies: VoiceoverModule, ScenePlanningModule
- enabled behavior: aligns spoken words or phrases to scenes and timeline beats
- disabled behavior: workflow uses static timing or manual timing input
- retry policy: one retry on alignment or transcription failure
- artifact outputs: speech timeline JSON, scene timeline JSON
- error behavior: continue with coarse timing and flag it for manual review

### CaptionsModule
- id: captions
- name: CaptionsModule
- input schema: { speech_timeline, scenes, brief }
- output schema: { caption_plan, subtitle_file, caption_metadata }
- config schema: { style, position, highlight_mode, enabled }
- dependencies: SpeechTimingModule, optional ScenePlanningModule
- enabled behavior: generates synchronized captions or subtitles
- disabled behavior: workflow renders without captions or uses manual subtitles
- retry policy: one retry on timing or rendering failure
- artifact outputs: ASS or SRT output, caption plan
- error behavior: omit captions and flag the module as skipped

### VideoRenderingModule
- id: video_rendering
- name: VideoRenderingModule
- input schema: { scenes, assets, voiceover?, captions?, brief }
- output schema: { video_path, duration_seconds, resolution, metadata }
- config schema: { codec, fps, resolution, effects_enabled }
- dependencies: ScenePlanningModule, AssetSelectionModule, optional VoiceoverModule, optional CaptionsModule
- enabled behavior: assembles the final video sequence
- disabled behavior: workflow ends at the artifact or audio stage
- retry policy: retry on renderer or ffmpeg-related issues; preserve intermediate assets
- artifact outputs: final video, preview file, render metadata
- error behavior: stop the workflow if video output is required and no fallback path exists

### ThumbnailModule
- id: thumbnail
- name: ThumbnailModule
- input schema: { video, brief, scenes }
- output schema: { thumbnail_path, variants }
- config schema: { size, style, brand_rules }
- dependencies: VideoRenderingModule
- enabled behavior: generates a thumbnail for the output asset
- disabled behavior: workflow continues without a thumbnail
- retry policy: one retry on image generation or rendering issues
- artifact outputs: thumbnail image, manifest entry
- error behavior: continue without thumbnail and mark it as optional

### ExportModule
- id: export
- name: ExportModule
- input schema: { script, voiceover?, captions?, video?, metadata, brief }
- output schema: { export_bundle, manifest, exported_files }
- config schema: { formats, destination, compression }
- dependencies: optional VoiceoverModule, optional CaptionsModule, optional VideoRenderingModule, MetadataModule
- enabled behavior: packages the final artifacts into an export bundle
- disabled behavior: outputs remain in staging storage without a packaged export
- retry policy: retry on filesystem or packaging failures
- artifact outputs: export bundle, manifest, archive files
- error behavior: preserve partial outputs and report the export failure clearly

### PublishingModule
- id: publishing
- name: PublishingModule
- input schema: { export_bundle, publishing_target, credentials }
- output schema: { publication_status, publish_url, publish_id }
- config schema: { platform, schedule, privacy, channel }
- dependencies: ExportModule
- enabled behavior: publishes the exported content to a selected platform
- disabled behavior: content remains local and is not published automatically
- retry policy: retry on rate limits or authentication issues
- artifact outputs: publish record, platform response metadata
- error behavior: leave the bundle unpublished and surface the failure for manual handling

### MetadataModule
- id: metadata
- name: MetadataModule
- input schema: { brief, script, assets?, video?, export_bundle? }
- output schema: { metadata, title, description, tags }
- config schema: { seo_policy, brand_profile, language }
- dependencies: optional BriefModule, optional ScriptGenerationModule, optional ExportModule
- enabled behavior: generates structured metadata for downstream discovery and packaging
- disabled behavior: workflow continues without metadata enrichment
- retry policy: one retry on generation or formatting issues
- artifact outputs: metadata JSON, SEO fields, export manifest metadata
- error behavior: emit minimal metadata from available fields and continue

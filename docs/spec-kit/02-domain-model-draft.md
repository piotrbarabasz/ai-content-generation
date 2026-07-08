# 02 Domain Model Draft

## Overview

The initial domain model should reflect a workflow engine that manages projects, content briefs, module execution, generated artifacts and output delivery. The model should remain simple enough for MVP while covering both short and long-form production.

## Entity Definitions

### User
- description: A person or account that creates and manages content projects.
- key fields: id, name, email, locale, created_at
- relations: owns one or many Workspaces and Projects
- MVP status: required

### Workspace
- description: A collection of projects and shared assets for one user or team.
- key fields: id, name, owner_id, created_at, settings
- relations: contains many Projects; may contain shared BrandProfile and ProviderConfig
- MVP status: required

### Project
- description: A single content production effort with a goal, inputs, workflow configuration and generated artifacts.
- key fields: id, workspace_id, name, status, created_at, content_type, target_platform
- relations: has one ContentBrief, one WorkflowConfig, many WorkflowRuns and GenerationJobs
- MVP status: required

### ContentBrief
- description: A structured brief that captures the content objective, audience, constraints and desired output shape.
- key fields: id, project_id, topic, objective, genre, duration_profile, target_platform, language, tone, audience
- relations: belongs to one Project; feeds WorkflowConfig and downstream modules
- MVP status: required

### WorkflowConfig
- description: The selected module set, provider choices and execution policy for a workflow.
- key fields: id, project_id, enabled_modules, disabled_modules, providers, retry_policy, review_required
- relations: belongs to one Project; drives one or many WorkflowRuns
- MVP status: required

### WorkflowRun
- description: One execution of the workflow for a given project and configuration.
- key fields: id, project_id, workflow_config_id, status, started_at, finished_at, error_summary
- relations: contains many GenerationJobs and Artifacts
- MVP status: required

### GenerationJob
- description: A granular processing task executed by one module or one module stage.
- key fields: id, workflow_run_id, module_id, status, attempt_count, started_at, finished_at, error
- relations: belongs to one WorkflowRun; produces one or many Artifacts
- MVP status: required

### Artifact
- description: An output or intermediate file created by a module, such as a JSON plan, transcript, audio file or render.
- key fields: id, generation_job_id, artifact_type, storage_path, checksum, metadata
- relations: belongs to one GenerationJob; may be referenced by later modules
- MVP status: required

### Script
- description: The main narrative text or transcript produced or approved for the workflow.
- key fields: id, project_id, version, text, language, word_count, status
- relations: belongs to one Project; may be used by NarrativeSegment, Voiceover, CaptionTrack and ExportBundle
- MVP status: required

### NarrativeSegment
- description: A logical story or script unit, such as a beat, section or paragraph-level narrative block.
- key fields: id, script_id, sequence, title, text, purpose, tone
- relations: belongs to one Script; may map to one or many RenderScene objects
- MVP status: required

### RenderScene
- description: A timeline or rendering unit used by the visual pipeline, such as a shot or scene block.
- key fields: id, workflow_run_id, sequence, start_time, end_time, duration_seconds, layout
- relations: belongs to one WorkflowRun; may reference one or many SceneAsset and CaptionTrack entries
- MVP status: required

### SceneAsset
- description: An image, clip, stock asset or generated visual assigned to a render scene.
- key fields: id, render_scene_id, asset_type, source, storage_path, confidence, status
- relations: belongs to one RenderScene; may be used by VideoRender
- MVP status: required

### Voiceover
- description: The spoken narration generated or supplied for the content.
- key fields: id, project_id, provider, language, audio_path, duration_seconds, status
- relations: belongs to one Project; may be referenced by SpeechTimeline and VideoRender
- MVP status: required

### SpeechTimeline
- description: The timing map between spoken words or phrases and the narrative or render structure.
- key fields: id, voiceover_id, entries, duration_seconds, source_format
- relations: belongs to one Voiceover; supports CaptionTrack and RenderScene alignment
- MVP status: required for video workflows, optional for script-only workflows

### CaptionTrack
- description: A synchronized caption or subtitle track for the content.
- key fields: id, project_id, format, language, storage_path, status
- relations: belongs to one Project; may align to SpeechTimeline and RenderScene
- MVP status: optional for MVP, but strongly recommended for video workflows

### VideoRender
- description: The rendered video asset produced from scenes, assets, audio and optional captions.
- key fields: id, project_id, output_path, format, resolution, duration_seconds, status
- relations: belongs to one Project; may use SceneAsset, Voiceover and CaptionTrack
- MVP status: required for video outputs, optional for audio/script-only outputs

### ExportBundle
- description: The packaged output that contains exported files and metadata for delivery.
- key fields: id, project_id, format, files, manifest_path, status
- relations: belongs to one Project; may include VideoRender, Script, Voiceover and metadata
- MVP status: required

### PromptTemplate
- description: A reusable prompt definition for a module or provider.
- key fields: id, name, version, template_text, variables
- relations: used by GenerationJobs and module configurations
- MVP status: optional

### BrandProfile
- description: Style and brand rules such as tone, visual preferences and voice guidelines.
- key fields: id, workspace_id, name, tone, visual_rules, voice_rules
- relations: belongs to one Workspace; may be used by Projects and modules
- MVP status: optional

### ProviderConfig
- description: Configuration for a specific provider, such as an LLM, TTS, renderer or storage backend.
- key fields: id, workspace_id, provider_type, provider_name, model, credentials_ref, enabled
- relations: belongs to one Workspace; referenced by WorkflowConfig
- MVP status: required for real execution, optional for local prototypes

### PublishingTarget
- description: An output destination such as a platform, channel or export target.
- key fields: id, project_id, platform, format, aspect_ratio, caption_required
- relations: belongs to one Project; may receive an ExportBundle or VideoRender
- MVP status: later

## Key Modeling Note

NarrativeSegment and RenderScene should remain separate concepts:
- NarrativeSegment is a logical story or script unit.
- RenderScene is a timeline or rendering unit.

This separation is important because the same narrative beat can be rendered as one scene or split into several render scenes depending on pacing, timing and visual composition.

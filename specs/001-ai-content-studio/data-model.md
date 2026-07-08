# Data Model: AI Content Studio MVP

## Core Entities

### Project
- Fields: id, workspace_id, name, content_type, genre, target_platform, language, tone, created_at, updated_at, status
- Relationships: belongs to a workspace; has many workflow configs and workflow runs
- Validation: name and content type are required; target platform must be one of the supported values

### ContentBrief
- Fields: id, project_id, topic, objective, audience, constraints, duration_profile, success_criteria, created_at
- Relationships: belongs to a project; used as input to workflow execution

### WorkflowConfig
- Fields: id, project_id, workflow_preset, content_type, content_genre, duration_profile, target_platform, language, tone, enabled_modules, disabled_modules, provider_config, render_config, caption_config, voice_config, asset_config, approval_policy, export_config, created_at
- Relationships: belongs to a project; one workflow config can produce many workflow runs
- Validation: workflow_preset must be short_video or long_form_script_voiceover; content_type must be short_video, long_form_video, audio_only or script_only; content_genre must be news, story, documentary, educational, tutorial, marketing, commentary or listicle; duration_profile must be 15_30s, 60s, 3_5min, 8_15min or custom; target_platform must be tiktok, youtube_shorts, youtube, instagram, linkedin or generic_export; a module cannot appear in both enabled_modules and disabled_modules
- MVP presets: short_video uses content_type short_video; long_form_script_voiceover may use content_type long_form_video with videoRendering disabled by default

### WorkflowRun
- Fields: id, workflow_config_id, status, current_stage, started_at, completed_at, error_message, artifact_ids, approval_checkpoint_ids
- Relationships: belongs to a workflow config; has many generation jobs, artifacts and approval checkpoints
- State transitions: pending -> validating -> running -> waiting_for_approval -> running -> completed/failed/skipped
- Validation: workflow cannot leave waiting_for_approval until blocking approval checkpoints are approved or skipped according to approval_policy

### GenerationJob
- Fields: id, workflow_run_id, module_name, status, attempt, retry_count, started_at, completed_at, output_artifact_ids, usage_metadata, error_message
- Relationships: belongs to a workflow run; may produce one or more artifacts
- Validation: module name must be registered in the module registry

### Artifact
- Fields: id, workflow_run_id, module_name, artifact_type, storage_key, metadata, created_at
- Relationships: belongs to a workflow run; referenced by export bundle
- Validation: artifact_type, workflow_run_id and storage_key are required

### Script
- Fields: id, workflow_run_id, text, version, language, word_count, approved_at
- Relationships: belongs to a workflow run; may be used to create voiceover and export contents

### NarrativeSegment
- Fields: id, workflow_run_id, order, title, text, role, duration_estimate
- Relationships: belongs to a workflow run; used by long-form and short workflows

### RenderScene
- Fields: id, workflow_run_id, order, scene_plan_id, timing_hint, visual_intensity
- Relationships: belongs to a workflow run; distinct from narrative segments

### SceneAsset
- Fields: id, render_scene_id, asset_type, source_ref, status
- Relationships: belongs to a render scene

### Voiceover
- Fields: id, workflow_run_id, text_reference_id, provider, audio_storage_key, duration_seconds, approved_at
- Relationships: belongs to a workflow run; may be linked to a script or narrative segment

### SpeechTimeline
- Fields: id, workflow_run_id, voiceover_id, word_timings, duration_seconds
- Relationships: belongs to a workflow run and voiceover

### CaptionTrack
- Fields: id, workflow_run_id, provider, caption_storage_key, approved_at
- Relationships: belongs to a workflow run

### VideoRender
- Fields: id, workflow_run_id, render_storage_key, duration_seconds, format, approved_at
- Relationships: belongs to a workflow run

### ExportBundle
- Fields: id, workflow_run_id, manifest_path, required_files, included_artifacts, missing_optional_artifacts, approval_summary, provider_summary, created_at, status
- Relationships: belongs to a workflow run; references artifacts and manifests
- Required files: manifest.json, workflow_config.json, workflow_run.json
- Conditional files or references: script.txt, script.json, narrative_segments.json, render_scenes.json, captions.srt or captions.json, voiceover.wav or voiceover artifact reference, video.mp4 or video artifact reference, qa_report.json, research.json and dossier.json
- Manifest fields: schemaVersion, exportId, projectId, workflowRunId, workflowPreset, contentType, contentGenre, durationProfile, createdAt, includedArtifacts, missingOptionalArtifacts, moduleResults, approvalSummary, providerSummary and artifactReferences

### ProviderConfig
- Fields: id, workflow_config_id, provider_type, provider_name, enabled, settings
- Relationships: belongs to a workflow config
- Validation: provider_type must be LLMProvider, TTSProvider, TranscriptionProvider, CaptionProvider, AssetProvider, VideoRendererProvider, StorageProvider or PublishingProvider; provider_name must resolve through ProviderRegistry when needed by an enabled module

### ProviderRegistry
- Fields: registered_providers, provider_type, provider_name, capabilities
- Relationships: used by workflow validation and module execution context
- Validation: resolves providers by type and name; fails fast when an enabled required module has no registered provider

### ApprovalCheckpoint
- Fields: id, workflow_run_id, checkpoint_type, artifact_id, status, required, created_at, resolved_at
- Relationships: belongs to a workflow run and may reference the artifact under review
- Status values: not_required, pending, approved, rejected, changes_requested, skipped
- Validation: resume is allowed only when blocking checkpoints are approved or skipped according to approval_policy

### ApprovalDecision
- Fields: id, checkpoint_id, decision, reviewer_id, comment, created_at, revised_artifact_id
- Relationships: belongs to an approval checkpoint
- Validation: rejection and changes_requested preserve the reviewed artifact and record a decision

### UsageMetadata
- Fields: provider_name, input_tokens, output_tokens, estimated_cost, duration_ms
- Relationships: optionally attached to ModuleResult and GenerationJob
- Validation: usage metadata is optional; workflow execution must not fail when it is absent

### PromptTemplate
- Fields: id, module_name, template_name, content, version
- Relationships: reusable input for modules

### BrandProfile
- Fields: id, project_id, voice, style, color_palette, tone_rules
- Relationships: belongs to a project

## Relationships Summary
- A project owns one or more workflow configs and runs.
- A workflow config owns provider configuration, module toggles, approval policy and export config.
- ProviderRegistry validates providers required by enabled modules before a workflow run starts.
- A workflow run produces generation jobs, artifacts, approval checkpoints and export bundles.
- Narrative and rendering artifacts remain distinct, connected but not collapsed into one model.

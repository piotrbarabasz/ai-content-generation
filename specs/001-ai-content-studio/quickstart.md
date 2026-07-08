# Quickstart: AI Content Studio MVP

## Prerequisites
- Python 3.11+
- Local filesystem access for artifact storage
- Mock providers enabled by default

## First Vertical Slice
1. Create a project with content type short_video.
2. Configure the workflow preset Short Video (`short_video`).
3. Validate WorkflowConfig enum values, enabledModules/disabledModules conflicts and providerConfig before the run starts.
4. Run the workflow with enabled modules for brief, scene planning, optional voiceover, optional captions, video rendering and export.
5. Verify that workflow status progresses through pending, validating, running and completed or waiting_for_approval.
6. Verify scene plan approval pauses before rendering when configured.
7. Confirm that artifacts are stored in the configured artifact store and that an export bundle is produced.
8. Confirm the export bundle includes manifest.json, workflow_config.json, workflow_run.json and conditional scene, captions, voiceover and video artifact references.

## Second Vertical Slice
1. Create a project with content type long_form_video.
2. Configure the workflow preset Long-form Script + Voiceover (`long_form_script_voiceover`).
3. Use workflowPreset `long_form_script_voiceover` with contentType `long_form_video` and videoRendering disabled by default.
4. Enable outline, script generation, post-processing, QA and export; optionally enable research, dossier and voiceover.
5. Start from a topic and run with mock providers.
6. Verify the workflow produces outline, script, post-processed script, QA report and export bundle.
7. Given research is enabled, verify research and dossier artifacts are persisted.
8. Given voiceover is disabled, verify export still completes without a voiceover artifact.
9. Verify approval checkpoints for script and final export.
10. Confirm that the export bundle includes script, QA report, optional research/dossier references and manifest data.

## Approval Validation
1. Query GET /workflow-runs/{runId}/approvals.
2. Approve a pending checkpoint with POST /workflow-runs/{runId}/approvals/{checkpointId}/approve.
3. Reject a pending checkpoint with POST /workflow-runs/{runId}/approvals/{checkpointId}/reject and verify downstream modules do not execute.
4. Request changes with POST /workflow-runs/{runId}/approvals/{checkpointId}/request-changes and verify a decision record is created.
5. Attempt POST /workflow-runs/{runId}/resume before approval and verify resume is blocked.
6. Approve or policy-skip the checkpoint and verify resume can continue.

## Validation Checks
- The workflow engine can be invoked through a minimal API endpoint.
- ProviderRegistry validates required providers for enabled modules before execution.
- Module outputs are persisted and traceable via artifact metadata.
- Mock providers can run without real external credentials.
- Usage metadata is optional and missing usage metadata does not fail execution.
- The MVP does not require publishing, analytics, billing or collaboration features.

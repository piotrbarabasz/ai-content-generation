from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from app.domain.artifact import Artifact
from app.domain.caption_track import CaptionTrack
from app.domain.export_bundle import ExportBundle
from app.domain.generation_job import GenerationJob
from app.domain.narrative_segment import NarrativeSegment
from app.domain.render_scene import RenderScene
from app.domain.script import Script
from app.domain.video_render import VideoRender
from app.domain.voiceover import Voiceover
from app.domain.workflow_run import WorkflowRun
from app.domain.base import DomainValidationError


def test_workflow_run_captures_status_and_artifact_references() -> None:
    started_at = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
    completed_at = datetime(2026, 7, 23, 10, 5, tzinfo=UTC)

    workflow_run = WorkflowRun.create(
        workflow_config_id="workflow_config_1",
        status="running",
        current_stage="scriptGeneration",
        started_at=started_at,
        completed_at=completed_at,
        artifact_ids=["artifact_1", "artifact_2"],
        approval_checkpoint_ids=["approval_1"],
    )

    payload = asdict(workflow_run)
    encoded = json.dumps(payload, default=str)

    assert workflow_run.workflow_config_id == "workflow_config_1"
    assert workflow_run.status == "running"
    assert workflow_run.current_stage == "scriptGeneration"
    assert workflow_run.artifact_ids == ["artifact_1", "artifact_2"]
    assert workflow_run.approval_checkpoint_ids == ["approval_1"]
    assert payload["started_at"] == started_at
    assert payload["completed_at"] == completed_at
    assert "\"status\": \"running\"" in encoded
    assert "\"artifact_ids\": [\"artifact_1\", \"artifact_2\"]" in encoded


def test_workflow_run_rejects_invalid_status_and_missing_workflow_config_id() -> None:
    with pytest.raises(DomainValidationError, match="WorkflowRun workflow_config_id is required"):
        WorkflowRun.create(workflow_config_id=" ", status="pending")

    with pytest.raises(DomainValidationError, match="Invalid WorkflowRun status"):
        WorkflowRun.create(workflow_config_id="workflow_config_1", status="unknown")


def test_generation_job_captures_retry_state_and_output_artifacts() -> None:
    started_at = datetime(2026, 7, 23, 10, 10, tzinfo=UTC)

    generation_job = GenerationJob.create(
        workflow_run_id="workflow_run_1",
        module_name="scriptGeneration",
        status="completed",
        attempt=2,
        retry_count=1,
        started_at=started_at,
        output_artifact_ids=["artifact_3"],
        usage_metadata={"providerName": "mock", "inputTokens": 10},
    )

    payload = asdict(generation_job)
    encoded = json.dumps(payload, default=str)

    assert generation_job.workflow_run_id == "workflow_run_1"
    assert generation_job.module_name == "scriptGeneration"
    assert generation_job.status == "completed"
    assert generation_job.attempt == 2
    assert generation_job.retry_count == 1
    assert generation_job.output_artifact_ids == ["artifact_3"]
    assert generation_job.usage_metadata == {"providerName": "mock", "inputTokens": 10}
    assert payload["started_at"] == started_at
    assert "\"output_artifact_ids\": [\"artifact_3\"]" in encoded


def test_generation_job_rejects_invalid_attempt_and_module_name() -> None:
    with pytest.raises(DomainValidationError, match="GenerationJob module_name is required"):
        GenerationJob.create(workflow_run_id="workflow_run_1", module_name=" ")

    with pytest.raises(DomainValidationError, match="GenerationJob attempt must be greater than zero"):
        GenerationJob.create(workflow_run_id="workflow_run_1", module_name="brief", attempt=0)


def test_artifact_and_script_models_validate_required_fields_and_serialize() -> None:
    artifact = Artifact.create(
        workflow_run_id="workflow_run_1",
        module_name="export",
        artifact_type="manifest",
        storage_key="artifacts/workflow_run_1/manifest.json",
        metadata={"kind": "manifest"},
    )
    script = Script.create(
        workflow_run_id="workflow_run_1",
        text="A concise campaign script.",
        version=2,
        language="en",
        word_count=5,
    )

    artifact_payload = asdict(artifact)
    script_payload = asdict(script)

    assert artifact.workflow_run_id == "workflow_run_1"
    assert artifact.storage_key.endswith("manifest.json")
    assert artifact_payload["metadata"] == {"kind": "manifest"}
    assert script.version == 2
    assert script.word_count == 5
    assert script_payload["text"] == "A concise campaign script."


def test_artifact_and_script_models_reject_missing_required_fields() -> None:
    with pytest.raises(DomainValidationError, match="Artifact storage_key is required"):
        Artifact.create(
            workflow_run_id="workflow_run_1",
            module_name="export",
            artifact_type="manifest",
            storage_key=" ",
        )

    with pytest.raises(DomainValidationError, match="Script text is required"):
        Script.create(workflow_run_id="workflow_run_1", text=" ")


def test_narrative_segment_and_render_scene_remain_distinct() -> None:
    narrative_segment = NarrativeSegment.create(
        workflow_run_id="workflow_run_1",
        order=1,
        title="Opening",
        text="The hook opens with a bold statement.",
        role="narration",
        duration_estimate=4.5,
    )
    render_scene = RenderScene.create(
        workflow_run_id="workflow_run_1",
        order=1,
        scene_plan_id="scene_plan_1",
        timing_hint="0:00-0:05",
        visual_intensity="medium",
    )

    narrative_payload = asdict(narrative_segment)
    render_payload = asdict(render_scene)

    assert type(narrative_segment) is NarrativeSegment
    assert type(render_scene) is RenderScene
    assert narrative_payload["role"] == "narration"
    assert narrative_payload["duration_estimate"] == 4.5
    assert render_payload["scene_plan_id"] == "scene_plan_1"
    assert render_payload["timing_hint"] == "0:00-0:05"
    assert "scene_plan_id" not in narrative_payload
    assert "role" not in render_payload


def test_voiceover_caption_video_render_and_export_bundle_serialize() -> None:
    voiceover = Voiceover.create(
        workflow_run_id="workflow_run_1",
        text_reference_id="script_1",
        provider="mock",
        audio_storage_key="artifacts/workflow_run_1/voiceover.wav",
        duration_seconds=12.5,
    )
    caption_track = CaptionTrack.create(
        workflow_run_id="workflow_run_1",
        provider="mock",
        caption_storage_key="artifacts/workflow_run_1/captions.json",
    )
    video_render = VideoRender.create(
        workflow_run_id="workflow_run_1",
        render_storage_key="artifacts/workflow_run_1/render.mp4",
        duration_seconds=12.5,
        format="mp4",
    )
    export_bundle = ExportBundle.create(
        workflow_run_id="workflow_run_1",
        manifest_path="artifacts/workflow_run_1/manifest.json",
        required_files=["manifest.json", "workflow_config.json", "workflow_run.json"],
        included_artifacts=["script.txt", "voiceover.wav"],
        missing_optional_artifacts=["captions.json"],
        approval_summary={"script": "approved"},
        provider_summary={"llm": "mock"},
        status="created",
    )

    voiceover_payload = asdict(voiceover)
    caption_payload = asdict(caption_track)
    video_payload = asdict(video_render)
    export_payload = asdict(export_bundle)
    encoded = json.dumps(export_payload, default=str)

    assert voiceover.provider == "mock"
    assert caption_track.caption_storage_key.endswith("captions.json")
    assert video_render.format == "mp4"
    assert export_bundle.status == "created"
    assert export_bundle.required_files == [
        "manifest.json",
        "workflow_config.json",
        "workflow_run.json",
    ]
    assert voiceover_payload["audio_storage_key"].endswith("voiceover.wav")
    assert caption_payload["provider"] == "mock"
    assert video_payload["render_storage_key"].endswith("render.mp4")
    assert export_payload["included_artifacts"] == ["script.txt", "voiceover.wav"]
    assert "\"missing_optional_artifacts\": [\"captions.json\"]" in encoded

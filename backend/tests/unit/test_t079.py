from __future__ import annotations

import pytest

from app.domain.base import DomainValidationError
from app.domain.export_config import ExportConfig
from app.domain.platform_handoff import YouTubeHandoffBuilder
from app.modules.export import ExportModule
from app.storage.local_store import LocalArtifactStore
from app.workflow.execution import ModuleExecutionContext, ModuleResult


CHECKSUM = "a" * 64


def _artifact(name: str) -> dict[str, object]:
    return {
        "name": name,
        "storage_key": f"run/module/id-{name}",
        "checksum": CHECKSUM,
        "size_bytes": 42,
    }


def _manifest(*, approved: bool = True) -> dict[str, object]:
    return {
        "exportId": "export_1",
        "approvalSummary": {"export": "approved" if approved else "pending"},
        "artifactReferences": {
            "render.mp4": _artifact("render.mp4"),
            "voiceover.wav": _artifact("voiceover.wav"),
            "captions.json": _artifact("captions.json"),
            "captions.en.srt": _artifact("captions.en.srt"),
        },
    }


def _config() -> ExportConfig:
    return ExportConfig.create(
        localization_strategy="platform_auto_dub",
        localization_targets=["pl"],
        manual_acceptance_required=True,
        custom_audio_fallback_enabled=True,
        source_language="en",
    )


def test_youtube_handoff_is_deterministic_checksummed_and_explicit() -> None:
    builder = YouTubeHandoffBuilder()
    metadata = {
        "title": "An English source video",
        "description": "A deterministic description.",
        "tags": ["science", "history"],
        "madeForKids": False,
    }
    first = builder.build(
        _manifest(), source_language="en", metadata=metadata, export_config=_config()
    ).to_payload()
    second = builder.build(
        _manifest(), source_language="en", metadata=metadata, export_config=_config()
    ).to_payload()
    assert first == second
    assert first["platform"] == "youtube"
    assert first["sourceLanguage"] == "en"
    assert first["approved"] is True
    assert first["artifacts"]["video"]["checksum"] == CHECKSUM
    assert first["artifacts"]["captions"]["srt"]["name"] == "captions.en.srt"
    assert first["artifacts"]["thumbnail"] == {
        "available": False,
        "name": "thumbnail.png",
        "reason": "not_produced",
    }
    assert first["localization"]["localizationTargets"] == ["pl"]


def test_youtube_handoff_requires_video_and_rejects_private_paths_or_secrets() -> None:
    builder = YouTubeHandoffBuilder()
    manifest = _manifest()
    manifest["artifactReferences"].pop("render.mp4")
    with pytest.raises(DomainValidationError, match="requires an actual"):
        builder.build(
            manifest,
            source_language="en",
            metadata={"title": "Title", "description": ""},
            export_config=_config(),
        )

    bad = _manifest()
    bad["artifactReferences"]["render.mp4"]["storage_key"] = "D:\\private\\video.mp4"
    with pytest.raises(DomainValidationError, match="relative"):
        builder.build(
            bad,
            source_language="en",
            metadata={"title": "Title", "description": ""},
            export_config=_config(),
        )

    with pytest.raises(DomainValidationError, match="sensitive field"):
        builder.build(
            _manifest(),
            source_language="en",
            metadata={"title": "Title", "description": "", "apiToken": "never"},
            export_config=_config(),
        )


def test_export_module_persists_platform_handoff_through_injected_adapter(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    video = store.save_artifact(
        "render.mp4",
        b"video",
        metadata={"workflow_run_id": "run_1", "module_name": "videoRendering"},
    )
    captions = store.save_artifact(
        "captions.json",
        "{}",
        metadata={"workflow_run_id": "run_1", "module_name": "captions"},
    )
    srt = store.save_artifact(
        "captions.en.srt",
        "1\r\n00:00:00,000 --> 00:00:01,000\r\nHello.\r\n",
        metadata={"workflow_run_id": "run_1", "module_name": "captions"},
    )
    module_results = {
        "videoRendering": ModuleResult(
            module_name="videoRendering",
            status="completed",
            output_artifact_ids=("render.mp4",),
            output={"artifact": video.to_payload()},
        ),
        "captions": ModuleResult(
            module_name="captions",
            status="completed",
            output_artifact_ids=("captions.json", "captions.en.srt"),
            output={
                "artifact": captions.to_payload(),
                "srt_artifact": srt.to_payload(),
            },
        ),
    }
    result = ExportModule(
        artifact_store=store,
        platform_handoff_builder=YouTubeHandoffBuilder(),
    ).execute(
        ModuleExecutionContext(
            workflow_run_id="run_1",
            workflow_config_id="config_1",
            module_name="export",
            enabled_modules=("videoRendering", "captions", "export"),
            module_results=module_results,
            inputs={
                "project_id": "project_1",
                "workflow_preset": "long_form_script_voiceover",
                "content_type": "long_form_video",
                "content_genre": "documentary",
                "duration_profile": "8_15min",
                "source_language": "en",
                "workflow_config": {
                    "language": "en",
                    "exportConfig": _config().to_payload(),
                },
                "approval_summary": {"export": "approved"},
                "publishing_metadata": {
                    "title": "English source",
                    "description": "Ready for review.",
                    "tags": ["demo"],
                },
            },
        )
    )
    handoff = result.output["platform_handoff"]
    assert handoff["artifacts"]["video"]["checksum"] == video.checksum
    assert handoff["artifacts"]["captions"]["srt"]["checksum"] == srt.checksum
    assert "platform_handoff.json" in result.output["manifest"]["artifactReferences"]
    assert {"manifest.json", "platform_handoff.json"} <= {
        artifact.name for artifact in store.list_artifacts()
    }

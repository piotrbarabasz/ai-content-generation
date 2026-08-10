from __future__ import annotations

import pytest

from app.domain.base import DomainValidationError
from app.domain.caption_track import serialize_srt, validate_caption_segments
from app.domain.enums import ProviderType
from app.modules.captions import CaptionsModule
from app.providers.mock_transcription import MockTranscriptionProvider
from app.storage.local_store import LocalArtifactStore
from app.workflow.execution import ModuleExecutionContext


def test_caption_validation_and_stable_unicode_srt() -> None:
    values = [
        {"start_ms": 0, "end_ms": 1_250, "text": "Hello, Dr. Chen."},
        {"start_ms": 1_250, "end_ms": 3_600_001, "text": "Café — 87%."},
    ]
    segments = validate_caption_segments(values)
    first = serialize_srt(segments)
    assert first == serialize_srt(segments)
    assert first.startswith("1\r\n00:00:00,000 --> 00:00:01,250\r\n")
    assert "2\r\n00:00:01,250 --> 01:00:00,001\r\nCafé — 87%.\r\n" in first
    assert first.encode("utf-8").decode("utf-8") == first


@pytest.mark.parametrize(
    "values,match",
    [
        ([], "cannot be empty"),
        ([{"start_ms": -1, "end_ms": 1, "text": "x"}], "negative"),
        ([{"start_ms": 2, "end_ms": 2, "text": "x"}], "before end"),
        ([{"start_ms": 0.5, "end_ms": 2, "text": "x"}], "integer"),
        ([{"start_ms": 0, "end_ms": 1, "text": ""}], "text"),
        ([
            {"start_ms": 0, "end_ms": 2, "text": "a"},
            {"start_ms": 1, "end_ms": 3, "text": "b"},
        ], "overlap"),
    ],
)
def test_caption_validation_rejects_malformed_timing(values, match: str) -> None:
    with pytest.raises(DomainValidationError, match=match):
        validate_caption_segments(values)


class StructuredCaptionProvider:
    provider_type = ProviderType.CAPTION
    provider_name = "structured"

    def generate_captions(self, audio_ref: str, transcript_ref: str):
        return {
            "captions_json": [
                {"start_ms": 0, "end_ms": 1000, "text": "English source."},
                {"start_ms": 1000, "end_ms": 2000, "text": "Second line."},
            ],
            "captions_srt": "untrusted provider string",
        }


def test_captions_module_persists_json_and_separate_english_srt(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    module = CaptionsModule(
        caption_provider=StructuredCaptionProvider(),
        transcription_provider=MockTranscriptionProvider(),
        artifact_store=store,
    )
    result = module.execute(
        ModuleExecutionContext(
            workflow_run_id="run_1",
            workflow_config_id="config_1",
            module_name="captions",
            inputs={
                "audio_ref": "artifact://voiceover.wav",
                "transcript": "English source. Second line.",
                "language": "en",
            },
        )
    )
    names = {manifest.name for manifest in store.list_artifacts()}
    assert {"captions.json", "captions.en.srt"} <= names
    assert result.output["srt_artifact"]["name"] == "captions.en.srt"
    srt = store.read_artifact(result.output["srt_artifact"]["storage_key"])
    assert b"untrusted provider string" not in srt
    assert srt.endswith(b"\r\n")
    assert result.output["caption_track"]["language"] == "en"

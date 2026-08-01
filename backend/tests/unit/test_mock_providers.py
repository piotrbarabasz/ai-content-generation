from __future__ import annotations

import io
import wave

from app.providers.interfaces import (
    AssetProvider,
    CaptionProvider,
    LLMProvider,
    StorageProvider,
    TTSProvider,
    TranscriptionProvider,
    VideoRendererProvider,
)
from app.providers.mock_assets import MockAssetProvider
from app.providers.mock_captions import MockCaptionProvider
from app.providers.mock_llm import MockLLMProvider
from app.providers.mock_storage import MockStorageProvider
from app.providers.mock_tts import MockTTSProvider
from app.providers.mock_transcription import MockTranscriptionProvider
from app.providers.mock_video_renderer import MockVideoRendererProvider
from app.providers.tts_result import TTSSynthesisResult


def _assert_valid_wav(audio_bytes: bytes) -> tuple[int, int, int]:
    with wave.open(io.BytesIO(audio_bytes), "rb") as reader:
        return reader.getnchannels(), reader.getsampwidth(), reader.getframerate()


def test_mock_providers_implement_their_protocols() -> None:
    assert isinstance(MockLLMProvider(), LLMProvider)
    assert isinstance(MockTTSProvider(), TTSProvider)
    assert isinstance(MockTranscriptionProvider(), TranscriptionProvider)
    assert isinstance(MockCaptionProvider(), CaptionProvider)
    assert isinstance(MockAssetProvider(), AssetProvider)
    assert isinstance(MockVideoRendererProvider(), VideoRendererProvider)
    assert isinstance(MockStorageProvider(), StorageProvider)


def test_mock_providers_return_deterministic_outputs_for_the_same_inputs() -> None:
    llm = MockLLMProvider("mock")
    tts = MockTTSProvider("mock")
    transcription = MockTranscriptionProvider("mock")
    captions = MockCaptionProvider("mock")
    assets = MockAssetProvider("mock")
    renderer = MockVideoRendererProvider("mock")

    llm_text_a = llm.generate_text("Write an intro", {"tone": "friendly"})
    llm_text_b = llm.generate_text("Write an intro", {"tone": "friendly"})
    llm_structured_a = llm.generate_structured(
        "Summarize the project",
        {"type": "object", "properties": {"summary": {"type": "string"}}},
    )
    llm_structured_b = llm.generate_structured(
        "Summarize the project",
        {"type": "object", "properties": {"summary": {"type": "string"}}},
    )

    tts_audio_a = tts.synthesize("Hello world", {"voice": "narrator"})
    tts_audio_b = tts.synthesize("Hello world", {"voice": "narrator"})
    source_ref = tts_audio_a.metadata["source_ref"]
    transcript_a = transcription.transcribe(source_ref)
    transcript_b = transcription.transcribe(source_ref)
    captions_a = captions.generate_captions(
        source_ref,
        transcript_a["transcript_ref"],
    )
    captions_b = captions.generate_captions(
        source_ref,
        transcript_a["transcript_ref"],
    )
    assets_a = assets.find_assets("urban skyline")
    assets_b = assets.find_assets("urban skyline")
    prepared_a = assets.prepare_asset(assets_a[0]["asset_ref"])
    prepared_b = assets.prepare_asset(assets_a[0]["asset_ref"])
    render_a = renderer.render(
        {"title": "Intro", "scenes": [{"scene": 1}, {"scene": 2}]},
        audio_ref=source_ref,
        captions_ref=captions_a["captions_json"][0]["text"],
    )
    render_b = renderer.render(
        {"title": "Intro", "scenes": [{"scene": 1}, {"scene": 2}]},
        audio_ref=source_ref,
        captions_ref=captions_a["captions_json"][0]["text"],
    )

    assert llm_text_a == llm_text_b
    assert llm_text_a.startswith("mock-llm:write-an-intro:")
    assert llm_structured_a == llm_structured_b
    assert isinstance(tts_audio_a, TTSSynthesisResult)
    assert tts_audio_a == tts_audio_b
    assert tts_audio_a.provider_name == "mock"
    assert tts_audio_a.audio_format == "wav"
    assert tts_audio_a.metadata["source_ref"].startswith("mock://tts/")
    assert tts_audio_a.audio_bytes == tts_audio_b.audio_bytes
    assert tts_audio_a.audio_bytes.startswith(b"RIFF")
    assert _assert_valid_wav(tts_audio_a.audio_bytes) == (1, 2, tts_audio_a.sample_rate)
    assert transcript_a == transcript_b
    assert captions_a == captions_b
    assert assets_a == assets_b
    assert assets_a[0]["asset_ref"] == "mock://asset/urban-skyline/primary"
    assert prepared_a == prepared_b
    assert render_a == render_b
    assert render_a["scene_count"] == 2
    assert render_a["status"] == "completed"


def test_mock_storage_provider_is_deterministic_and_listable() -> None:
    storage = MockStorageProvider("mock")

    first = storage.save_artifact(
        "script.txt",
        "Hello from the mock provider.",
        metadata={
            "workflow_run_id": "workflow_run_7",
            "module_name": "scriptGeneration",
            "artifact_type": "script",
            "kind": "draft",
        },
    )
    second = storage.save_artifact(
        "script.txt",
        "Hello from the mock provider.",
        metadata={
            "workflow_run_id": "workflow_run_7",
            "module_name": "scriptGeneration",
            "artifact_type": "script",
            "kind": "draft",
        },
    )

    assert first.artifact_id == second.artifact_id
    assert first.storage_key == second.storage_key
    assert first.metadata == {"kind": "draft"}
    assert storage.read_artifact(first.storage_key) == b"Hello from the mock provider."
    assert storage.list_artifacts(prefix="workflow_run_7/scriptGeneration") == (second,)

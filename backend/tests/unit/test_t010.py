from __future__ import annotations

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


def test_mock_providers_implement_their_contracts() -> None:
    assert isinstance(MockLLMProvider(), LLMProvider)
    assert isinstance(MockTTSProvider(), TTSProvider)
    assert isinstance(MockCaptionProvider(), CaptionProvider)
    assert isinstance(MockTranscriptionProvider(), TranscriptionProvider)
    assert isinstance(MockAssetProvider(), AssetProvider)
    assert isinstance(MockVideoRendererProvider(), VideoRendererProvider)
    assert isinstance(MockStorageProvider(), StorageProvider)


def test_mock_llm_provider_is_deterministic_and_structure_aware() -> None:
    provider = MockLLMProvider()

    text_a = provider.generate_text("Write a short intro", {"tone": "friendly"})
    text_b = provider.generate_text("Write a short intro", {"tone": "friendly"})
    structured = provider.generate_structured(
        "Summarize",
        {"type": "object", "properties": {"summary": {"type": "string"}}},
    )

    assert text_a == text_b
    assert text_a.startswith("mock-llm:write-a-short-intro:")
    assert structured == {
        "provider": "mock",
        "prompt": "Summarize",
        "schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
        "response": structured["response"],
    }
    assert structured["response"].startswith("mock-llm-structured:")


def test_mock_tts_transcription_caption_and_renderer_outputs_are_stable() -> None:
    tts = MockTTSProvider()
    transcription = MockTranscriptionProvider()
    captions = MockCaptionProvider()
    renderer = MockVideoRendererProvider()

    synth_1 = tts.synthesize("Hello world", {"voice": "narrator"})
    synth_2 = tts.synthesize("Hello world", {"voice": "narrator"})
    transcript = transcription.transcribe(synth_1["audio_ref"])
    caption_payload = captions.generate_captions(
        synth_1["audio_ref"],
        transcript["transcript_ref"],
    )
    render_payload = renderer.render(
        {"title": "Intro", "scenes": [{"scene": 1}, {"scene": 2}]},
        audio_ref=synth_1["audio_ref"],
        captions_ref=caption_payload["captions_json"][0]["text"],
    )

    assert synth_1 == synth_2
    assert synth_1["audio_ref"].startswith("mock://tts/")
    assert transcript["transcript"].startswith("Transcript for")
    assert caption_payload["captions_srt"].startswith("1\n00:00:00,000 --> 00:00:02,000\n")
    assert render_payload == {
        "provider": "mock",
        "video_ref": render_payload["video_ref"],
        "status": "completed",
        "scene_count": 2,
        "scene_plan_label": "intro",
        "audio_ref": synth_1["audio_ref"],
        "captions_ref": caption_payload["captions_json"][0]["text"],
    }


def test_mock_asset_and_storage_providers_are_deterministic() -> None:
    assets = MockAssetProvider()
    storage = MockStorageProvider()

    found_a = assets.find_assets("urban skyline")
    found_b = assets.find_assets("urban skyline")
    prepared = assets.prepare_asset(found_a[0]["asset_ref"])

    manifest = storage.save_artifact(
        "script.txt",
        "Hello from the mock provider.",
        metadata={
            "workflow_run_id": "workflow_run_1",
            "module_name": "scriptGeneration",
            "artifact_type": "script",
            "kind": "draft",
        },
    )
    stored = storage.read_artifact(manifest.storage_key)
    manifests = storage.list_artifacts(prefix=manifest.storage_key.rsplit("/", 1)[0])

    assert found_a == found_b
    assert found_a[0]["asset_ref"] == "mock://asset/urban-skyline/primary"
    assert prepared["prepared_asset_ref"].startswith("mock://asset/urban-skyline/primary#prepared-")
    assert manifest.name == "script.txt"
    assert manifest.artifact_type == "script"
    assert manifest.workflow_run_id == "workflow_run_1"
    assert manifest.module_name == "scriptGeneration"
    assert manifest.metadata == {"kind": "draft"}
    assert stored == b"Hello from the mock provider."
    assert manifests == (manifest,)

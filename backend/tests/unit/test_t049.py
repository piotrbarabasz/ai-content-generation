from __future__ import annotations

import pytest

from app.providers import TTSSynthesisResult


def test_tts_synthesis_result_constructs_and_normalizes_payload() -> None:
    metadata = {
        "source_ref": "narration_01",
        "voice": {"name": "narrator", "variant": "calm"},
    }

    result = TTSSynthesisResult(
        audio_bytes=bytearray(b"RIFF\x00\x00\x00\x00WAVE"),
        sample_rate=24_000,
        duration_seconds=1.25,
        audio_format=".WAV",
        provider_name="xtts_v2",
        metadata=metadata,
    )

    payload = result.to_payload()

    assert result.audio_bytes == b"RIFF\x00\x00\x00\x00WAVE"
    assert result.sample_rate == 24_000
    assert result.duration_seconds == 1.25
    assert result.audio_format == "wav"
    assert result.provider_name == "xtts_v2"
    assert result.metadata == metadata
    assert result.metadata is not metadata
    assert payload == {
        "audio_bytes": b"RIFF\x00\x00\x00\x00WAVE",
        "sample_rate": 24_000,
        "duration_seconds": 1.25,
        "audio_format": "wav",
        "provider_name": "xtts_v2",
        "metadata": metadata,
    }
    assert payload["metadata"] is not metadata


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"audio_bytes": b"", "sample_rate": 24_000, "duration_seconds": 1.0, "audio_format": "wav", "provider_name": "mock"}, "audio_bytes cannot be empty"),
        ({"audio_bytes": b"data", "sample_rate": 0, "duration_seconds": 1.0, "audio_format": "wav", "provider_name": "mock"}, "sample_rate must be positive"),
        ({"audio_bytes": b"data", "sample_rate": 24_000, "duration_seconds": -0.1, "audio_format": "wav", "provider_name": "mock"}, "duration_seconds cannot be negative"),
        ({"audio_bytes": b"data", "sample_rate": 24_000, "duration_seconds": 1.0, "audio_format": "mp4", "provider_name": "mock"}, "audio_format must be one of"),
        ({"audio_bytes": b"data", "sample_rate": 24_000, "duration_seconds": 1.0, "audio_format": None, "provider_name": "mock"}, "audio_format must be a string"),
        ({"audio_bytes": b"data", "sample_rate": 24_000, "duration_seconds": 1.0, "audio_format": "wav", "provider_name": " "}, "provider_name is required"),
        ({"audio_bytes": b"data", "sample_rate": 24_000, "duration_seconds": 1.0, "audio_format": "wav", "provider_name": None}, "provider_name must be a string"),
    ],
)
def test_tts_synthesis_result_rejects_invalid_values(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        TTSSynthesisResult(metadata={}, **kwargs)


def test_tts_synthesis_result_protects_metadata_from_mutation() -> None:
    metadata = {"source_ref": "narration_02", "voice": {"name": "narrator"}}
    result = TTSSynthesisResult(
        audio_bytes=b"RIFFdataWAVE",
        sample_rate=22_050,
        duration_seconds=2.0,
        audio_format="wave",
        provider_name="mock",
        metadata=metadata,
    )

    metadata["source_ref"] = "changed"
    metadata["voice"]["name"] = "mutated"

    payload = result.to_payload()
    payload["metadata"]["source_ref"] = "payload-change"
    payload["metadata"]["voice"]["name"] = "payload-mutation"

    assert result.metadata["source_ref"] == "narration_02"
    assert result.metadata["voice"]["name"] == "narrator"
    assert payload["metadata"]["source_ref"] == "payload-change"
    assert payload["metadata"]["voice"]["name"] == "payload-mutation"
    with pytest.raises(TypeError):
        result.metadata["source_ref"] = "blocked"  # type: ignore[index]

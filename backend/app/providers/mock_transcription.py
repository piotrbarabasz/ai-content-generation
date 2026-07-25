"""Deterministic mock transcription provider."""

from __future__ import annotations

from app.domain.enums import ProviderType

from .interfaces import TranscriptionProvider, _stable_signature, _slugify


class MockTranscriptionProvider(TranscriptionProvider):
    provider_type = ProviderType.TRANSCRIPTION

    def __init__(self, provider_name: str = "mock") -> None:
        self.provider_name = provider_name

    def transcribe(self, audio_ref: str) -> dict[str, object]:
        signature = _stable_signature(
            {
                "provider": self.provider_name,
                "audio_ref": audio_ref,
            }
        )
        transcript = f"Transcript for {_slugify(audio_ref)}"
        return {
            "provider": self.provider_name,
            "audio_ref": audio_ref,
            "transcript_ref": f"mock://transcript/{signature[:12]}.json",
            "transcript": transcript,
            "segments": [
                {
                    "start_ms": 0,
                    "end_ms": 1500,
                    "text": transcript,
                }
            ],
        }

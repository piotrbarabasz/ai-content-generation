"""Deterministic mock caption provider."""

from __future__ import annotations

from app.domain.enums import ProviderType

from .interfaces import CaptionProvider, _stable_signature, _slugify


class MockCaptionProvider(CaptionProvider):
    provider_type = ProviderType.CAPTION

    def __init__(self, provider_name: str = "mock") -> None:
        self.provider_name = provider_name

    def generate_captions(self, audio_ref: str, transcript_ref: str) -> dict[str, object]:
        signature = _stable_signature(
            {
                "provider": self.provider_name,
                "audio_ref": audio_ref,
                "transcript_ref": transcript_ref,
            }
        )
        caption_text = f"Captions for {_slugify(audio_ref)}"
        return {
            "provider": self.provider_name,
            "audio_ref": audio_ref,
            "transcript_ref": transcript_ref,
            "captions_srt": (
                "1\n"
                "00:00:00,000 --> 00:00:02,000\n"
                f"{caption_text}\n\n"
                f"2\n00:00:02,000 --> 00:00:04,000\nmock-caption:{signature[:10]}"
            ),
            "captions_json": [
                {
                    "start_ms": 0,
                    "end_ms": 2000,
                    "text": caption_text,
                },
                {
                    "start_ms": 2000,
                    "end_ms": 4000,
                    "text": f"mock-caption:{signature[:10]}",
                },
            ],
        }

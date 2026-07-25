"""Deterministic mock TTS provider."""

from __future__ import annotations

import hashlib

from app.domain.enums import ProviderType
from app.domain.types import JsonDict

from .interfaces import TTSProvider, _coerce_json_dict, _stable_signature, _slugify


class MockTTSProvider(TTSProvider):
    provider_type = ProviderType.TTS

    def __init__(self, provider_name: str = "mock") -> None:
        self.provider_name = provider_name

    def synthesize(self, text: str, voice_config: JsonDict | None = None) -> JsonDict:
        normalized_voice_config = _coerce_json_dict(voice_config)
        signature = _stable_signature(
            {
                "provider": self.provider_name,
                "text": text,
                "voice_config": normalized_voice_config,
            }
        )
        audio_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
        duration_ms = max(250, len(text.strip()) * 35)
        return {
            "provider": self.provider_name,
            "audio_ref": f"mock://tts/{audio_id}.wav",
            "format": "wav",
            "text": text,
            "voice_config": normalized_voice_config,
            "duration_ms": duration_ms,
            "label": _slugify(text),
        }

"""Deterministic mock TTS provider that emits valid WAV bytes."""

from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import wave

from app.domain.enums import ProviderType
from app.domain.types import JsonDict

from .interfaces import TTSProvider, _coerce_json_dict, _slugify, _stable_signature
from .tts_capabilities import (
    TTSCapabilities,
    request_uses_reference_audio,
    request_uses_speaking_rate,
    resolve_language_id,
    resolve_voice_mode,
)
from .tts_result import TTSSynthesisResult


def _duration_seconds(text: str, voice_config: JsonDict) -> float:
    normalized_text = " ".join(text.split())
    word_count = len(normalized_text.split())
    base_duration = 0.45 + (word_count * 0.18)
    voice_signature = _stable_signature(voice_config)
    offset = (int(hashlib.sha256(voice_signature.encode("utf-8")).hexdigest()[:2], 16) % 15) / 100.0
    return round(max(0.45, base_duration + offset), 3)


def _build_pcm_wav_bytes(*, signature: str, duration_seconds: float, sample_rate: int) -> bytes:
    frame_count = max(int(round(duration_seconds * sample_rate)), sample_rate // 10)
    digest = hashlib.sha256(signature.encode("utf-8")).digest()
    frequency = 180 + (digest[0] % 220)
    amplitude = 12_000 + (digest[1] % 8_000)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        frames = bytearray()
        for frame_index in range(frame_count):
            sample_time = frame_index / sample_rate
            envelope = 0.9 - (0.2 * (frame_index / frame_count))
            sample = int(
                amplitude
                * envelope
                * math.sin(2.0 * math.pi * frequency * sample_time)
            )
            frames.extend(struct.pack("<h", sample))
        wav_file.writeframes(bytes(frames))

    return buffer.getvalue()


class MockTTSProvider(TTSProvider):
    provider_type = ProviderType.TTS

    def __init__(self, provider_name: str = "mock") -> None:
        self.provider_name = provider_name

    def capabilities(self) -> TTSCapabilities:
        return TTSCapabilities(
            provider_name=self.provider_name,
            supported_languages=("*",),
            voice_modes=("mock",),
            reference_audio_required=False,
            speaking_rate_supported=False,
            usage_policy="production",
        )

    def effective_synthesis_identity(
        self,
        voice_config: JsonDict | None = None,
    ) -> JsonDict:
        """Return the stable effective configuration used by the mock generator."""

        normalized_voice_config = _coerce_json_dict(voice_config)
        voice_mode = resolve_voice_mode(normalized_voice_config, default_voice_mode="mock")
        self.capabilities().validate_request(
            language_id=resolve_language_id(normalized_voice_config),
            voice_mode=voice_mode,
            reference_audio_present=request_uses_reference_audio(normalized_voice_config),
            speaking_rate_requested=request_uses_speaking_rate(normalized_voice_config),
        )
        # Round-trip through JSON so callers can persist the identity without
        # depending on the particular values passed by an in-process caller.
        effective_config = json.loads(_stable_signature(normalized_voice_config))
        return {
            "provider": self.provider_name,
            "model_variant": "mock",
            "device": "cpu",
            "language_id": effective_config.get("language_id", effective_config.get("language")),
            "generation_settings": {
                key: effective_config.get(key)
                for key in (
                    "exaggeration",
                    "cfg_weight",
                    "temperature",
                    "repetition_penalty",
                    "min_p",
                    "top_p",
                )
            },
            "voice": {
                "mode": voice_mode,
                "config": effective_config,
            },
        }

    def synthesize(
        self,
        text: str,
        voice_config: JsonDict | None = None,
    ) -> TTSSynthesisResult:
        normalized_voice_config = _coerce_json_dict(voice_config)
        voice_mode = resolve_voice_mode(normalized_voice_config, default_voice_mode="mock")
        self.capabilities().validate_request(
            language_id=resolve_language_id(normalized_voice_config),
            voice_mode=voice_mode,
            reference_audio_present=request_uses_reference_audio(normalized_voice_config),
            speaking_rate_requested=request_uses_speaking_rate(normalized_voice_config),
        )
        signature = _stable_signature(
            {
                "provider": self.provider_name,
                "text": text,
                "voice_config": normalized_voice_config,
            }
        )
        audio_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
        source_ref = f"mock://tts/{audio_id}.wav"
        duration_seconds = _duration_seconds(text, normalized_voice_config)
        sample_rate = 24_000
        audio_bytes = _build_pcm_wav_bytes(
            signature=signature,
            duration_seconds=duration_seconds,
            sample_rate=sample_rate,
        )
        return TTSSynthesisResult(
            audio_bytes=audio_bytes,
            sample_rate=sample_rate,
            duration_seconds=duration_seconds,
            audio_format="wav",
            provider_name=self.provider_name,
            metadata={
                "source_ref": source_ref,
                "text": text,
                "voice_config": normalized_voice_config,
                "label": _slugify(text),
                "word_count": len(" ".join(text.split()).split()),
            },
        )

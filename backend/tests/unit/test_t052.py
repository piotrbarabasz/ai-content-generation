import io
import sys
import wave
from pathlib import Path

import pytest

from app.domain.enums import ProviderType
from app.providers.chatterbox_v3 import (
    ChatterboxAudioPromptError,
    ChatterboxAudioValidationError,
    ChatterboxGenerationError,
    ChatterboxModelLoadError,
    ChatterboxV3Provider,
)
from app.providers.interfaces import TTSProvider
from app.providers.tts_result import TTSSynthesisResult


def _wav(sample_rate: int = 24_000, frames: int = 10) -> bytes:
    result = io.BytesIO()
    with wave.open(result, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\0\0" * frames)
    return result.getvalue()


class FakeBackend:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    def generate(self, text: str, **kwargs: object) -> bytes:
        self.calls.append((text, kwargs))
        return self.payload


def test_adapter_is_lazy_and_uses_v3_polish_builtin_voice() -> None:
    calls: list[str] = []
    backend = FakeBackend(_wav())
    provider = ChatterboxV3Provider(
        model_loader=lambda device: calls.append(device) or backend
    )

    assert isinstance(provider, TTSProvider)
    assert provider.provider_type is ProviderType.TTS
    assert provider.provider_name == "chatterbox_v3"
    assert calls == []
    first = provider.synthesize("Cześć świecie")
    second = provider.synthesize("Ponownie")

    assert isinstance(first, TTSSynthesisResult)
    assert first.sample_rate == 24_000
    assert first.metadata["model_variant"] == "v3"
    assert first.metadata["voice"] == "builtin"
    assert calls == ["cpu"]
    assert backend.calls[0][1] == {"language_id": "pl"}
    assert second.audio_bytes == _wav()
    assert "chatterbox" not in sys.modules


def test_optional_prompt_is_forwarded_when_present() -> None:
    reference = Path(__file__).resolve()
    backend = FakeBackend(_wav())
    provider = ChatterboxV3Provider(audio_prompt_path=reference, model_loader=lambda _: backend)

    provider.synthesize("tekst", {"language_id": "en"})

    assert backend.calls[0][1] == {"language_id": "en", "audio_prompt_path": str(reference)}


def test_missing_optional_prompt_is_redacted_and_model_errors_are_typed() -> None:
    missing = Path(__file__).resolve().parent / "private" / "voice.wav"
    provider = ChatterboxV3Provider(audio_prompt_path=missing, model_loader=lambda _: FakeBackend(_wav()))

    with pytest.raises(ChatterboxAudioPromptError) as error:
        provider.synthesize("tekst")
    assert str(missing) not in str(error.value)

    provider = ChatterboxV3Provider(model_loader=lambda _: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ChatterboxModelLoadError):
        provider.synthesize("tekst")


@pytest.mark.parametrize("payload", [b"", b"not wav", _wav(22_050)])
def test_invalid_backend_wav_is_rejected(payload: bytes) -> None:
    provider = ChatterboxV3Provider(model_loader=lambda _: FakeBackend(payload))

    with pytest.raises(ChatterboxAudioValidationError):
        provider.synthesize("tekst")

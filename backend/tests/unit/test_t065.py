from __future__ import annotations

import io
import sys
import types
import wave
from pathlib import Path

import pytest

from app.providers.chatterbox_v3 import (
    ChatterboxAudioPromptError,
    ChatterboxCompatibilityError,
    ChatterboxV3Provider,
    _load_runtime_backend,
)
from app.tts.assembly import inspect_pcm_wav


def _wav_bytes(*, sample_rate: int = 24_000, channels: int = 1, sample_width: int = 2, frames: int = 8) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(sample_rate)
        output.writeframes(b"\0" * (frames * channels * sample_width))
    return buffer.getvalue()


def _install_fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    accepts_t3_model: bool = True,
    generated_bytes: bytes | None = None,
) -> dict[str, object]:
    captured: dict[str, object] = {
        "from_pretrained_calls": [],
        "generate_calls": [],
        "save_calls": [],
    }

    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: True)
    fake_torch.device = lambda value: value

    fake_torchaudio = types.ModuleType("torchaudio")

    def _save(fileobj: object, audio: object, sample_rate: int, **kwargs: object) -> None:
        captured["save_calls"].append(
            {"fileobj": fileobj, "audio": audio, "sample_rate": sample_rate, "kwargs": kwargs}
        )
        payload = generated_bytes or _wav_bytes(sample_rate=sample_rate)
        assert isinstance(fileobj, io.BytesIO)
        fileobj.write(payload)

    fake_torchaudio.save = _save

    fake_chatterbox = types.ModuleType("chatterbox")
    fake_chatterbox.__path__ = []  # type: ignore[attr-defined]
    fake_mtl_tts = types.ModuleType("chatterbox.mtl_tts")

    class RecordingBackend:
        def generate(self, text: str, **kwargs: object) -> object:
            captured["generate_calls"].append((text, kwargs))
            return object()

    class FakeChatterboxMultilingualTTS:
        if accepts_t3_model:

            @classmethod
            def from_pretrained(cls, device: object, t3_model: str) -> RecordingBackend:
                captured["from_pretrained_calls"].append({"device": device, "t3_model": t3_model})
                return RecordingBackend()

        else:

            @classmethod
            def from_pretrained(cls, device: object) -> RecordingBackend:
                captured["from_pretrained_calls"].append({"device": device})
                return RecordingBackend()

    fake_mtl_tts.ChatterboxMultilingualTTS = FakeChatterboxMultilingualTTS
    fake_chatterbox.mtl_tts = fake_mtl_tts  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "torchaudio", fake_torchaudio)
    monkeypatch.setitem(sys.modules, "chatterbox", fake_chatterbox)
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", fake_mtl_tts)
    return captured


def test_runtime_loader_rejects_incompatible_t3_model_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_runtime(monkeypatch, accepts_t3_model=False)

    with pytest.raises(ChatterboxCompatibilityError, match="t3_model"):
        _load_runtime_backend("cpu")

    assert captured["from_pretrained_calls"] == []


def test_provider_saves_pcm16_wav_and_forwards_v3_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install_fake_runtime(monkeypatch)
    provider = ChatterboxV3Provider(device="cuda:0", model_loader=_load_runtime_backend)

    result = provider.synthesize("Tekst do syntezy", {"language_id": "pl"})

    assert captured["from_pretrained_calls"] == [{"device": "cuda:0", "t3_model": "v3"}]
    assert captured["generate_calls"] == [("Tekst do syntezy", {"language_id": "pl"})]
    assert len(captured["save_calls"]) == 1
    save_call = captured["save_calls"][0]
    assert save_call["sample_rate"] == 24_000
    assert save_call["kwargs"] == {"format": "wav", "encoding": "PCM_S", "bits_per_sample": 16}
    parameters, _ = inspect_pcm_wav(result.audio_bytes)
    assert parameters.channels == 1
    assert parameters.sample_width == 2
    assert parameters.sample_rate == 24_000
    assert parameters.compression_type == "NONE"
    assert result.metadata["language_id"] == "pl"
    assert result.metadata["voice"] == "builtin"


def test_provider_redacts_prompt_read_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    prompt_path = Path(__file__).resolve()
    provider = ChatterboxV3Provider(
        audio_prompt_path=prompt_path,
        model_loader=lambda _: object(),
    )

    def _deny_open(*_: object, **__: object) -> object:
        raise OSError(f"permission denied: {prompt_path}")

    monkeypatch.setattr("builtins.open", _deny_open)

    with pytest.raises(ChatterboxAudioPromptError) as error:
        provider.effective_synthesis_identity()

    assert str(prompt_path) not in str(error.value)

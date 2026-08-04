from __future__ import annotations

import io
import json
import sys
import types
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.piper_tts import (
    PiperAudioValidationError,
    PiperConfigurationError,
    PiperDependencyError,
    PiperGenerationError,
    PiperTTSProvider,
)
from app.providers.tts_factory import build_tts_provider
from app.providers.tts_result import TTSSynthesisResult


def _wav(*, sample_rate: int = 24_000, channels: int = 1, sample_width: int = 2, frames: int = 12) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(sample_rate)
        output.writeframes(b"\0" * (frames * channels * sample_width))
    return buffer.getvalue()


def _config(name: str = "piper", settings: dict[str, object] | None = None) -> ProviderConfig:
    return ProviderConfig.create(
        workflow_config_id="workflow",
        provider_type=ProviderType.TTS,
        provider_name=name,
        settings=settings,
    )


class RecordingBackend:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    def synthesize(self, text: str, **kwargs: object) -> bytes:
        self.calls.append((text, dict(kwargs)))
        return self.payload


def test_factory_resolves_piper_without_loading_the_optional_runtime() -> None:
    with TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp_dir:
        model_path = Path(temp_dir) / "pl_PL-gosia-medium.onnx"
        model_path.write_bytes(b"piper-model")
        sys.modules.pop("piper", None)
        sys.modules.pop("piper.voice", None)

        provider = build_tts_provider(_config(settings={"model_path": model_path}))

        assert isinstance(provider, PiperTTSProvider)
        assert provider.model_path == model_path
        assert provider._backend is None
        assert "piper" not in sys.modules


def test_default_loader_imports_piper_only_on_first_synthesis(monkeypatch: pytest.MonkeyPatch) -> None:
    load_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    fake_voice_module = types.ModuleType("piper.voice")

    class FakePiperVoice:
        @classmethod
        def load(cls, *args: object, **kwargs: object) -> RecordingBackend:
            load_calls.append((args, dict(kwargs)))
            return RecordingBackend(_wav())

    fake_voice_module.PiperVoice = FakePiperVoice  # type: ignore[attr-defined]
    fake_piper_module = types.ModuleType("piper")
    fake_piper_module.__path__ = []  # type: ignore[attr-defined]
    fake_piper_module.voice = fake_voice_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "piper", fake_piper_module)
    monkeypatch.setitem(sys.modules, "piper.voice", fake_voice_module)

    provider = PiperTTSProvider(model_key="pl_PL-gosia-medium")
    assert provider._backend is None
    assert load_calls == []

    result = provider.synthesize("Cześć świacie")

    assert isinstance(result, TTSSynthesisResult)
    assert result.sample_rate == 24_000
    assert result.duration_seconds > 0
    assert result.metadata["voice"]["mode"] == "catalog"
    assert load_calls == [((), {"model_key": "pl_PL-gosia-medium", "device": "cpu"})]


def test_model_path_identity_redacts_absolute_paths_and_forwards_runtime_reference() -> None:
    with TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp_dir:
        model_path = Path(temp_dir) / "voices" / "pl_PL-darkman-medium.onnx"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(b"model-bytes")
        captured: dict[str, object] = {}
        backend = RecordingBackend(_wav())

        provider = PiperTTSProvider(
            model_path=model_path,
            model_loader=lambda reference: (captured.__setitem__("reference", reference) or backend),
        )

        identity = provider.effective_synthesis_identity()
        result = provider.synthesize("tekst", {"language_id": "pl"})

        assert isinstance(result, TTSSynthesisResult)
        assert str(model_path) not in json.dumps(identity, sort_keys=True)
        assert identity["voice"]["mode"] == "local_path"
        assert identity["voice"]["model"]["kind"] == "local_path"
        assert identity["voice"]["model"]["model_name"] == model_path.name
        assert "model_checksum" in identity["voice"]["model"]
        assert captured["reference"] == {
            "model_path": str(model_path),
            "device": "cpu",
            "language_id": "pl",
            "model_identity": {
                "kind": "local_path",
                "model_name": model_path.name,
                "model_checksum": identity["voice"]["model"]["model_checksum"],
            },
        }
        assert backend.calls == [("tekst", {"language_id": "pl"})]
        assert result.metadata["voice"]["model"]["model_name"] == model_path.name


def test_provider_returns_valid_pcm_wav_result_and_truthful_audio_metrics() -> None:
    backend = RecordingBackend(_wav(sample_rate=22_050, frames=30))
    provider = PiperTTSProvider(model_key="pl_PL-gosia-medium", model_loader=lambda _: backend)

    result = provider.synthesize("tekst")

    assert isinstance(result, TTSSynthesisResult)
    assert result.provider_name == "piper"
    assert result.audio_format == "wav"
    assert result.sample_rate == 22_050
    assert result.duration_seconds == pytest.approx(30 / 22_050, rel=1e-6)
    assert result.metadata["device"] == "cpu"
    assert result.metadata["voice"]["mode"] == "catalog"
    assert backend.calls == [("tekst", {"language_id": "pl"})]


def test_provider_reports_missing_runtime_and_invalid_configuration_and_audio() -> None:
    with pytest.raises(PiperConfigurationError, match="exactly one of model_key or model_path"):
        PiperTTSProvider()

    provider = PiperTTSProvider(model_key="pl_PL-gosia-medium")

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.delitem(sys.modules, "piper", raising=False)
        monkeypatch.delitem(sys.modules, "piper.voice", raising=False)
        with pytest.raises(PiperDependencyError, match="piper-tts"):
            provider.synthesize("tekst")
    finally:
        monkeypatch.undo()

    invalid_backend = RecordingBackend(b"not wav")
    provider = PiperTTSProvider(model_key="pl_PL-gosia-medium", model_loader=lambda _: invalid_backend)
    with pytest.raises(PiperAudioValidationError):
        provider.synthesize("tekst")


def test_provider_rejects_blank_text_before_loading_runtime() -> None:
    provider = PiperTTSProvider(model_key="pl_PL-gosia-medium", model_loader=lambda _: RecordingBackend(_wav()))

    with pytest.raises(PiperGenerationError, match="non-empty synthesis text"):
        provider.synthesize("   ")

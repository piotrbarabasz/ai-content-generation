import json
import io
import sys
import wave
from pathlib import Path

import pytest

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.chatterbox_v3 import ChatterboxAudioPromptError, ChatterboxV3Provider
from app.providers.interfaces import TTSProvider
from app.providers.mock_tts import MockTTSProvider
from app.providers.tts_factory import build_tts_provider
from app.providers.tts_capabilities import TTSCapabilityError


def _wav() -> bytes:
    result = io.BytesIO()
    with wave.open(result, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(b"\0\0" * 10)
    return result.getvalue()


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(self, text: str, **kwargs: object) -> bytes:
        self.calls.append(kwargs)
        return _wav()


def test_mock_exposes_a_stable_json_compatible_identity() -> None:
    provider = MockTTSProvider()

    identity = provider.effective_synthesis_identity({"language_id": "pl", "voice": "narrator"})

    assert isinstance(provider, TTSProvider)
    assert identity == provider.effective_synthesis_identity(
        {"voice": "narrator", "language_id": "pl"}
    )
    assert identity["provider"] == "mock"
    assert identity["voice"]["config"]["voice"] == "narrator"


def test_chatterbox_identity_uses_effective_defaults_and_request_overrides() -> None:
    provider = ChatterboxV3Provider(
        device="cuda:0",
        language_id="pl",
        exaggeration=0.1,
        cfg_weight=0.2,
        temperature=0.3,
        repetition_penalty=0.4,
        min_p=0.5,
        top_p=0.6,
    )

    identity = provider.effective_synthesis_identity({"language_id": "en", "top_p": 0.9})

    assert identity == {
        "provider": "chatterbox_v3",
        "model_variant": "v3",
        "device": "cuda:0",
        "language_id": "en",
        "generation_settings": {
            "exaggeration": 0.1,
            "cfg_weight": 0.2,
            "temperature": 0.3,
            "repetition_penalty": 0.4,
            "min_p": 0.5,
            "top_p": 0.9,
        },
        "voice": {"mode": "builtin"},
    }


def test_chatterbox_reference_identity_is_content_based_and_redacts_missing_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    reference = Path(__file__)
    provider = ChatterboxV3Provider(audio_prompt_path=reference)
    contents = iter((io.BytesIO(b"first"), io.BytesIO(b"second")))
    monkeypatch.setattr("app.providers.chatterbox_v3.open", lambda *_: next(contents), raising=False)

    first = provider.effective_synthesis_identity()
    second = provider.effective_synthesis_identity()

    assert first["voice"]["mode"] == "reference"
    assert first["voice"]["content_checksum"] != second["voice"]["content_checksum"]
    assert str(reference) not in str(first)

    missing = Path("private-reference.wav")
    provider = ChatterboxV3Provider(audio_prompt_path=missing)
    with pytest.raises(ChatterboxAudioPromptError) as error:
        provider.effective_synthesis_identity()
    assert str(missing) not in str(error.value)


def test_factory_forwards_settings_and_identity_is_lazy() -> None:
    sys.modules.pop("torch", None)
    sys.modules.pop("torchaudio", None)
    sys.modules.pop("chatterbox", None)
    config = ProviderConfig.create(
        workflow_config_id="workflow",
        provider_type=ProviderType.TTS,
        provider_name="chatterbox_v3",
        settings={
            "device": "cuda:0",
            "language_id": "pl",
            "exaggeration": 0.1,
            "cfg_weight": 0.2,
            "temperature": 0.3,
            "repetition_penalty": 0.4,
            "min_p": 0.5,
            "top_p": 0.6,
        },
    )

    provider = build_tts_provider(config)
    identity = provider.effective_synthesis_identity({"temperature": 0.8})

    assert identity["generation_settings"]["temperature"] == 0.8
    assert identity["generation_settings"]["top_p"] == 0.6
    assert provider._backend is None
    assert "torch" not in sys.modules
    assert "torchaudio" not in sys.modules
    assert "chatterbox" not in sys.modules


def test_synthesis_uses_the_same_effective_generation_settings() -> None:
    backend = FakeBackend()
    provider = ChatterboxV3Provider(temperature=0.3, top_p=0.6, model_loader=lambda _: backend)

    provider.synthesize("tekst", {"temperature": 0.8})

    assert backend.calls == [{"temperature": 0.8, "top_p": 0.6, "language_id": "pl"}]


def test_capabilities_are_deterministic_and_json_compatible() -> None:
    mock_capabilities = MockTTSProvider().capabilities().to_payload()
    chatterbox_capabilities = ChatterboxV3Provider().capabilities().to_payload()

    assert json.dumps(mock_capabilities, sort_keys=True)
    assert json.dumps(chatterbox_capabilities, sort_keys=True)
    assert mock_capabilities == {
        "provider_name": "mock",
        "supported_languages": ["*"],
        "voice_modes": ["mock"],
        "reference_audio_required": False,
        "speaking_rate_supported": False,
        "usage_policy": "production",
    }
    assert chatterbox_capabilities == {
        "provider_name": "chatterbox_v3",
        "supported_languages": ["en", "pl"],
        "voice_modes": ["builtin", "reference"],
        "reference_audio_required": False,
        "speaking_rate_supported": False,
        "usage_policy": "production",
    }


def test_chatterbox_rejects_unsupported_capabilities_before_backend_loading() -> None:
    provider = ChatterboxV3Provider(
        model_loader=lambda _: (_ for _ in ()).throw(AssertionError("backend should not load"))
    )

    with pytest.raises(TTSCapabilityError, match="language_id 'fr'"):
        provider.synthesize("tekst", {"language_id": "fr"})

    with pytest.raises(TTSCapabilityError, match="voice mode 'mock'"):
        provider.effective_synthesis_identity({"voice_mode": "mock"})

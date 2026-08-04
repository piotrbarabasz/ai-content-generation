from __future__ import annotations

import json
import sys

import pytest

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.chatterbox_v3 import ChatterboxV3Provider
from app.providers.mock_tts import MockTTSProvider
from app.providers.tts_capabilities import TTSCapabilityError
from app.providers.tts_factory import TTSFactoryError, build_tts_provider
from app.providers.tts_settings import TTSSettings


def _config(name: str = "mock", settings: dict[str, object] | None = None) -> ProviderConfig:
    return ProviderConfig.create(
        workflow_config_id="workflow",
        provider_type=ProviderType.TTS,
        provider_name=name,
        settings=settings,
    )


def test_capability_metadata_is_json_compatible_and_deterministic() -> None:
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


def test_capability_inspection_does_not_load_optional_runtimes() -> None:
    sys.modules.pop("torch", None)
    sys.modules.pop("torchaudio", None)
    sys.modules.pop("chatterbox", None)
    sys.modules.pop("chatterbox.mtl_tts", None)

    provider = ChatterboxV3Provider(
        model_loader=lambda _: (_ for _ in ()).throw(AssertionError("runtime should not load"))
    )
    capabilities = provider.capabilities()

    assert capabilities.provider_name == "chatterbox_v3"
    assert capabilities.voice_modes == ("builtin", "reference")
    assert provider._backend is None
    assert "torch" not in sys.modules
    assert "torchaudio" not in sys.modules
    assert "chatterbox" not in sys.modules


def test_settings_validate_usage_policy_separately_from_generation_settings() -> None:
    settings = TTSSettings.from_mapping(
        {
            "usage_policy": "evaluation_only",
            "language_id": "pl",
            "temperature": 0.7,
        },
        provider="chatterbox_v3",
    )

    assert settings.usage_policy == "evaluation_only"
    assert settings.language_id == "pl"
    assert settings.temperature == 0.7


def test_factory_rejects_unsupported_capabilities_with_provider_neutral_errors() -> None:
    with pytest.raises(TTSFactoryError, match="language_id 'fr'"):
        build_tts_provider(_config("chatterbox_v3", {"language_id": "fr"}))

    with pytest.raises(TTSFactoryError, match="voice mode 'reference'"):
        build_tts_provider(
            _config("mock", {"audio_prompt_path": "private-reference.wav"})
        )


def test_factory_allows_evaluation_only_deployment_for_production_provider() -> None:
    provider = build_tts_provider(_config("chatterbox_v3", {"usage_policy": "evaluation_only"}))

    assert provider.capabilities().usage_policy == "production"
    assert provider.provider_name == "chatterbox_v3"


def test_capability_validation_rejects_unsupported_request_voice_modes() -> None:
    provider = MockTTSProvider()

    with pytest.raises(TTSCapabilityError, match="voice mode 'reference'"):
        provider.synthesize("tekst", {"voice_mode": "reference"})

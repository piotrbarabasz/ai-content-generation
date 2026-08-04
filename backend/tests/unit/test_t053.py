import inspect
import sys
from tempfile import TemporaryDirectory
from pathlib import Path

import pytest

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.chatterbox_v3 import ChatterboxV3Provider
from app.providers.mock_tts import MockTTSProvider
from app.providers.piper_tts import PiperTTSProvider
from app.providers.registry import ProviderRegistry
from app.providers.tts_factory import TTSFactoryError, build_tts_provider
from app.providers.tts_settings import TTSSettings, TTSSettingsError


def _config(name: str = "mock", settings: dict[str, object] | None = None) -> ProviderConfig:
    return ProviderConfig.create(
        workflow_config_id="workflow",
        provider_type=ProviderType.TTS,
        provider_name=name,
        settings=settings,
    )


def test_settings_defaults_are_mock_cpu_and_no_prompt() -> None:
    settings = TTSSettings()

    assert settings.provider == "mock"
    assert settings.usage_policy == "production"
    assert settings.device == "cpu"
    assert settings.audio_prompt_path is None
    assert settings.model_variant == "v3"


def test_settings_accept_precise_chatterbox_fields_and_policy_mode() -> None:
    settings = TTSSettings.from_mapping(
        {
            "usage_policy": "evaluation_only",
            "language_id": "pl",
            "model_variant": "v3",
            "exaggeration": 0.5,
            "cfg_weight": 0.3,
            "temperature": 0.7,
            "repetition_penalty": 1.1,
            "min_p": 0.1,
            "top_p": 0.9,
        },
        provider="chatterbox_v3",
    )

    assert settings.provider == "chatterbox_v3"
    assert settings.usage_policy == "evaluation_only"
    assert settings.language_id == "pl"
    assert settings.top_p == 0.9


def test_settings_accept_piper_model_key_and_path_selection() -> None:
    with TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp_dir:
        model_path = Path(temp_dir) / "pl_PL-gosia-medium.onnx"
        model_path.write_bytes(b"piper-model")

        key_settings = TTSSettings.from_mapping({"model_key": "pl_PL-gosia-medium"}, provider="piper")
        path_settings = TTSSettings.from_mapping({"model_path": model_path}, provider="piper")

        assert key_settings.provider == "piper"
        assert key_settings.model_key == "pl_PL-gosia-medium"
        assert key_settings.model_path is None
        assert path_settings.model_key is None
        assert path_settings.model_path == model_path


@pytest.mark.parametrize("provider_name", ["mock", "chatterbox_v3"])
def test_settings_reject_piper_fields_for_other_providers(provider_name: str) -> None:
    with TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp_dir:
        model_path = Path(temp_dir) / "pl_PL-gosia-medium.onnx"
        model_path.write_bytes(b"piper-model")

        with pytest.raises(TTSSettingsError):
            TTSSettings.from_mapping({"model_path": model_path}, provider=provider_name)

    with pytest.raises(TTSSettingsError):
        TTSSettings.from_mapping({"model_key": "pl_PL-gosia-medium"}, provider=provider_name)


@pytest.mark.parametrize(
    "values",
    [
        {"model_variant": "v2"},
        {"temperature": "warm"},
        {"unexpected": "value"},
        {"provider": "mock"},
        {"usage_policy": "preview"},
    ],
)
def test_settings_reject_invalid_values(values: dict[str, object]) -> None:
    with pytest.raises(TTSSettingsError):
        TTSSettings.from_mapping(values, provider="chatterbox_v3")


def test_factory_selects_mock_and_resolves_it_through_registry() -> None:
    registry = ProviderRegistry()
    provider = build_tts_provider(_config(), registry=registry)

    assert isinstance(provider, MockTTSProvider)
    assert registry.resolve_from_config(_config()) is provider


def test_factory_selects_chatterbox_lazily_without_optional_runtime_import() -> None:
    sys.modules.pop("torch", None)
    sys.modules.pop("chatterbox", None)

    provider = build_tts_provider(_config("chatterbox_v3", {"usage_policy": "evaluation_only"}))

    assert isinstance(provider, ChatterboxV3Provider)
    assert provider.device == "cpu"
    assert provider.language_id == "pl"
    assert provider._backend is None
    assert provider.capabilities().usage_policy == "production"
    assert "torch" not in sys.modules
    assert "chatterbox" not in sys.modules


def test_factory_selects_piper_lazily_without_optional_runtime_import() -> None:
    with TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp_dir:
        model_path = Path(temp_dir) / "pl_PL-gosia-medium.onnx"
        model_path.write_bytes(b"piper-model")
        sys.modules.pop("piper", None)
        sys.modules.pop("piper.voice", None)

        provider = build_tts_provider(_config("piper", {"model_path": model_path}))

        assert isinstance(provider, PiperTTSProvider)
        assert provider.model_path == model_path
        assert provider._backend is None
        assert "piper" not in sys.modules


def test_factory_rejects_unknown_and_wrong_type_configs() -> None:
    with pytest.raises(TTSFactoryError, match="Unsupported TTS provider"):
        build_tts_provider(_config("other"))
    with pytest.raises(TTSFactoryError, match="ProviderConfig instance"):
        build_tts_provider("mock")  # type: ignore[arg-type]
    wrong_type = ProviderConfig.create(
        workflow_config_id="workflow", provider_type=ProviderType.LLM, provider_name="mock"
    )
    with pytest.raises(TTSFactoryError, match="provider_type 'tts'"):
        build_tts_provider(wrong_type)


def test_workflow_layers_do_not_import_concrete_tts_providers() -> None:
    import app.modules.voiceover as voiceover_module
    import app.workflow.engine as engine_module

    for module in (engine_module, voiceover_module):
        source = inspect.getsource(module)
        assert "chatterbox_v3" not in source
        assert "mock_tts" not in source

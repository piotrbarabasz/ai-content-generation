from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from app.tts.catalog import TTSCatalog
from app.providers.tts_capabilities import TTSCapabilities
from app.tts.catalog import TTSModelDescriptor, TTSProviderDescriptor, TTSVoiceDescriptor


def _load_tts_catalog_module(module_name: str = "app.providers.tts_catalog_import_regression") -> types.ModuleType:
    module_path = Path(__file__).resolve().parents[2] / "app" / "providers" / "tts_catalog.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _fresh_tts_catalog_module() -> types.ModuleType:
    return _load_tts_catalog_module()


def test_default_adapter_registration_is_explicit_and_deterministic() -> None:
    module = _fresh_tts_catalog_module()

    assert module.list_tts_catalog_adapters() == ("chatterbox_v3", "piper", "xtts_v2_eval")

    catalog = module.build_tts_catalog()

    assert isinstance(catalog, TTSCatalog)
    assert tuple(provider.id for provider in catalog.providers) == (
        "chatterbox_v3",
        "piper",
        "xtts_v2_eval",
    )
    assert all("moss" not in provider.id for provider in catalog.providers)


def test_chatterbox_adapter_exposes_builtin_and_reference_voices() -> None:
    module = _fresh_tts_catalog_module()
    catalog = module.build_tts_catalog()
    provider = catalog.get_provider("chatterbox_v3")

    assert provider.capabilities.provider_name == "chatterbox_v3"
    assert provider.capabilities.supported_languages == ("en", "pl")
    assert provider.capabilities.voice_modes == ("builtin", "reference")
    assert provider.capabilities.reference_audio_required is False
    assert provider.capabilities.speaking_rate_supported is False
    assert provider.usage_policy == "production"
    assert tuple(model.id for model in provider.models) == ("v3",)

    model = provider.get_model("v3")
    builtin_voice = provider.get_voice("v3", "builtin")
    reference_voice = provider.get_voice("v3", "reference")

    assert builtin_voice.voice_mode == "builtin"
    assert builtin_voice.reference_audio_required is False
    assert builtin_voice.supported_languages == ("en", "pl")
    assert reference_voice.voice_mode == "reference"
    assert reference_voice.reference_audio_required is True
    assert reference_voice.supported_languages == ("en", "pl")


def test_piper_adapter_maps_each_curated_voice_once_without_local_paths() -> None:
    module = _fresh_tts_catalog_module()
    catalog = module.build_tts_catalog()
    provider = catalog.get_provider("piper")

    assert provider.capabilities.provider_name == "piper"
    assert provider.capabilities.supported_languages == ("pl",)
    assert provider.capabilities.voice_modes == ("catalog", "local_path")
    assert provider.capabilities.reference_audio_required is False
    assert provider.capabilities.speaking_rate_supported is False
    assert provider.usage_policy == "production"
    assert tuple(model.id for model in provider.models) == (
        "pl_PL-bass-high",
        "pl_PL-darkman-medium",
        "pl_PL-gosia-medium",
        "pl_PL-mc_speech-medium",
        "pl_PL-mls_6892-low",
    )

    for entry in module.list_piper_voice_catalog():
        model = provider.get_model(entry.provider_key)
        voice = provider.get_voice(entry.provider_key, entry.voice_name)
        metadata = voice.public_metadata

        assert model.id == entry.provider_key
        assert voice.id == entry.voice_name
        assert voice.voice_mode == "catalog"
        assert voice.reference_audio_required is False
        assert voice.supported_languages == ("pl",)
        assert model.runtime_required is True
        assert model.asset_required is True
        assert metadata["provider_key"] == entry.provider_key
        assert metadata["voice_name"] == entry.voice_name
        assert metadata["language_id"] == entry.language_id
        assert metadata["quality"] == entry.quality
        assert metadata["expected_sample_rate_hz"] == entry.expected_sample_rate_hz
        assert metadata["source_repository"] == {
            "owner": "rhasspy",
            "name": "piper-voices",
        }
        assert metadata["checksums"] == {
            "onnx": entry.checksums[0][1],
            "onnx_json": entry.checksums[1][1],
            "model_card": entry.checksums[2][1],
        }
        assert metadata["license_identifier"] == {
            "engine": entry.engine_license_identifier,
            "model": entry.model_license_identifier,
        }
        assert "required_files" not in metadata
        assert all(not str(value).startswith(("C:\\", "D:\\", "/Users/", "C:/Users/")) for value in metadata.values() if isinstance(value, str))


def test_xtts_adapter_is_evaluation_only_and_requires_reference_audio() -> None:
    module = _fresh_tts_catalog_module()
    catalog = module.build_tts_catalog()
    provider = catalog.get_provider("xtts_v2_eval")

    assert provider.capabilities.provider_name == "xtts_v2_eval"
    assert provider.capabilities.supported_languages == ("pl",)
    assert provider.capabilities.voice_modes == ("reference",)
    assert provider.capabilities.reference_audio_required is True
    assert provider.capabilities.speaking_rate_supported is False
    assert provider.capabilities.usage_policy == "evaluation_only"
    assert provider.usage_policy == "evaluation_only"
    assert tuple(model.id for model in provider.models) == ("xtts_v2",)

    model = provider.get_model("xtts_v2")
    voice = provider.get_voice("xtts_v2", "reference")

    assert voice.voice_mode == "reference"
    assert voice.reference_audio_required is True
    assert voice.supported_languages == ("pl",)
    assert model.runtime_required is True
    assert model.asset_required is True


def test_duplicate_registration_is_rejected_and_custom_adapters_remain_supported() -> None:
    module = _fresh_tts_catalog_module()

    def builder() -> TTSProviderDescriptor:
        return TTSProviderDescriptor(
            id="approved_extra",
            display_name="Approved Extra",
            usage_policy="production",
            supported_languages=("pl",),
            capabilities=TTSCapabilities(
                provider_name="approved_extra",
                supported_languages=("pl",),
                voice_modes=("builtin",),
                reference_audio_required=False,
                speaking_rate_supported=False,
                usage_policy="production",
            ),
            models=(
                TTSModelDescriptor(
                    id="approved_extra_model",
                    display_name="Approved Extra Model",
                    provider_id="approved_extra",
                    supported_languages=("pl",),
                    voices=(
                        TTSVoiceDescriptor(
                            id="approved_voice",
                            display_name="Approved Voice",
                            provider_id="approved_extra",
                            model_id="approved_extra_model",
                            voice_mode="builtin",
                            supported_languages=("pl",),
                            preview_supported=True,
                            reference_audio_required=False,
                        ),
                    ),
                    runtime_required=False,
                    asset_required=False,
                ),
            ),
        )

    registry = module.TTSCatalogAdapterRegistry()
    adapter = module.TTSCatalogAdapter(provider_id="approved_extra", build_provider_descriptor=builder)

    registry.register(adapter)
    with pytest.raises(module.TTSCatalogAdapterError, match="Duplicate"):
        registry.register(adapter)

    custom_registry = module.TTSCatalogAdapterRegistry(
        (
            module.TTSCatalogAdapter(provider_id="approved_extra", build_provider_descriptor=builder),
        )
    )
    custom_catalog = module.build_tts_catalog(registry=custom_registry)

    assert tuple(provider.id for provider in custom_catalog.providers) == ("approved_extra",)
    assert custom_catalog.get_provider("approved_extra").id == "approved_extra"


def test_import_and_build_do_not_trigger_optional_runtime_loading_or_filesystem_scans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for module_name in ("torch", "torchaudio", "piper", "TTS", "chatterbox", "chatterbox.mtl_tts"):
        sys.modules.pop(module_name, None)

    def _deny_scan(*args, **kwargs):  # pragma: no cover - defensive sentinel.
        raise AssertionError("catalog adapters must not scan the filesystem.")

    monkeypatch.setattr(Path, "glob", _deny_scan, raising=False)
    monkeypatch.setattr(Path, "iterdir", _deny_scan, raising=False)
    monkeypatch.setattr(Path, "rglob", _deny_scan, raising=False)

    module_name = "app.providers.tts_catalog_optional_runtime_guard"
    module_path = Path(__file__).resolve().parents[2] / "app" / "providers" / "tts_catalog.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        catalog = module.build_tts_catalog()
    finally:
        sys.modules.pop(module_name, None)

    assert tuple(provider.id for provider in catalog.providers) == (
        "chatterbox_v3",
        "piper",
        "xtts_v2_eval",
    )
    assert "torch" not in sys.modules
    assert "torchaudio" not in sys.modules
    assert "piper" not in sys.modules
    assert "TTS" not in sys.modules
    assert "chatterbox" not in sys.modules
    assert "chatterbox.mtl_tts" not in sys.modules
    assert json.dumps(catalog.to_payload(), sort_keys=True)

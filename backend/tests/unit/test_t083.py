from __future__ import annotations

import json
import sys
from copy import deepcopy

import pytest

from app.providers.tts_capabilities import TTSCapabilities
from app.tts.catalog import (
    TTSCatalog,
    TTSCatalogError,
    TTSModelDescriptor,
    TTSProviderDescriptor,
    TTSVoiceDescriptor,
)


def _chatterbox_catalog() -> TTSCatalog:
    builtin = TTSVoiceDescriptor(
        id="builtin",
        display_name="Builtin voice",
        provider_id="chatterbox_v3",
        model_id="v3",
        voice_mode="builtin",
        supported_languages=("pl", "EN", "pl"),
        preview_supported=True,
        reference_audio_required=False,
        public_metadata={"sampleRateHz": 24_000, "license": "MIT"},
    )
    reference = TTSVoiceDescriptor(
        id="reference",
        display_name="Reference voice",
        provider_id="chatterbox_v3",
        model_id="v3",
        voice_mode="reference",
        supported_languages=("pl",),
        preview_supported=True,
        reference_audio_required=True,
    )
    experimental = TTSVoiceDescriptor(
        id="reference_experimental",
        display_name="Experimental reference voice",
        provider_id="chatterbox_v3",
        model_id="v3_experimental",
        voice_mode="reference",
        supported_languages=("pl",),
        preview_supported=False,
        reference_audio_required=True,
    )
    chatterbox = TTSProviderDescriptor(
        id="chatterbox_v3",
        display_name="Chatterbox Multilingual V3",
        usage_policy="production",
        supported_languages=("pl", "en"),
        capabilities=TTSCapabilities(
            provider_name="chatterbox_v3",
            supported_languages=("en", "pl"),
            voice_modes=("builtin", "reference"),
            reference_audio_required=False,
            speaking_rate_supported=False,
            usage_policy="production",
        ),
        models=(
            TTSModelDescriptor(
                id="v3",
                display_name="Chatterbox V3",
                provider_id="chatterbox_v3",
                supported_languages=("en", "pl"),
                voices=(builtin, reference),
                runtime_required=True,
                asset_required=False,
            ),
            TTSModelDescriptor(
                id="v3_experimental",
                display_name="Chatterbox Experimental",
                provider_id="chatterbox_v3",
                supported_languages=("pl",),
                voices=(experimental,),
                runtime_required=True,
                asset_required=True,
            ),
        ),
    )
    piper = TTSProviderDescriptor(
        id="piper",
        display_name="Piper",
        usage_policy="production",
        supported_languages=("pl",),
        capabilities=TTSCapabilities(
            provider_name="piper",
            supported_languages=("pl",),
            voice_modes=("catalog",),
            reference_audio_required=False,
            speaking_rate_supported=True,
            usage_policy="production",
        ),
        models=(
            TTSModelDescriptor(
                id="pl_PL-gosia-medium",
                display_name="Gosia medium",
                provider_id="piper",
                supported_languages=("pl",),
                voices=(
                    TTSVoiceDescriptor(
                        id="gosia",
                        display_name="Gosia",
                        provider_id="piper",
                        model_id="pl_PL-gosia-medium",
                        voice_mode="catalog",
                        supported_languages=("pl",),
                        preview_supported=True,
                        reference_audio_required=False,
                        public_metadata={"quality": "medium", "checksum": "abc123"},
                    ),
                ),
                runtime_required=True,
                asset_required=True,
            ),
        ),
    )
    xtts = TTSProviderDescriptor(
        id="xtts_v2_eval",
        display_name="XTTS v2",
        usage_policy="evaluation_only",
        supported_languages=("pl",),
        capabilities=TTSCapabilities(
            provider_name="xtts_v2_eval",
            supported_languages=("pl",),
            voice_modes=("reference",),
            reference_audio_required=True,
            speaking_rate_supported=False,
            usage_policy="evaluation_only",
        ),
        models=(
            TTSModelDescriptor(
                id="xtts_v2",
                display_name="XTTS v2",
                provider_id="xtts_v2_eval",
                supported_languages=("pl",),
                voices=(
                    TTSVoiceDescriptor(
                        id="reference",
                        display_name="Reference",
                        provider_id="xtts_v2_eval",
                        model_id="xtts_v2",
                        voice_mode="reference",
                        supported_languages=("pl",),
                        preview_supported=True,
                        reference_audio_required=True,
                    ),
                ),
                runtime_required=True,
                asset_required=True,
            ),
        ),
    )
    return TTSCatalog(providers=(chatterbox, piper, xtts))


def test_catalog_serialization_is_deterministic_and_json_round_trips() -> None:
    catalog = _chatterbox_catalog()
    payload = catalog.to_payload()

    assert tuple(provider["id"] for provider in payload["providers"]) == (
        "chatterbox_v3",
        "piper",
        "xtts_v2_eval",
    )
    assert tuple(model["id"] for model in payload["providers"][0]["models"]) == ("v3", "v3_experimental")
    assert tuple(voice["id"] for voice in payload["providers"][0]["models"][0]["voices"]) == (
        "builtin",
        "reference",
    )
    assert payload == json.loads(json.dumps(payload, sort_keys=True))
    assert TTSCatalog.from_payload(payload) == catalog
    assert catalog.to_payload() == deepcopy(payload)


def test_catalog_lookup_returns_the_expected_selection() -> None:
    catalog = _chatterbox_catalog()

    provider = catalog.get_provider("chatterbox_v3")
    model = catalog.get_model("chatterbox_v3", "v3")
    voice = catalog.get_voice("chatterbox_v3", "v3", "builtin")

    assert provider.id == "chatterbox_v3"
    assert model.provider_id == "chatterbox_v3"
    assert voice.model_id == "v3"
    assert voice.public_metadata["license"] == "MIT"


def test_catalog_filtering_is_non_mutating_and_drops_empty_nested_entries() -> None:
    catalog = _chatterbox_catalog()
    before = catalog.to_payload()

    filtered = catalog.filter(language="EN", usage_policy="production")

    assert filtered is not catalog
    assert tuple(provider.id for provider in filtered.providers) == ("chatterbox_v3",)
    assert tuple(model.id for model in filtered.get_provider("chatterbox_v3").models) == ("v3",)
    assert tuple(voice.id for voice in filtered.get_model("chatterbox_v3", "v3").voices) == ("builtin",)
    assert filtered.get_voice("chatterbox_v3", "v3", "builtin").public_metadata["license"] == "MIT"
    assert catalog.to_payload() == before


def test_catalog_usage_policy_filter_excludes_evaluation_only_provider_from_production() -> None:
    catalog = _chatterbox_catalog()

    production_only = catalog.filter(usage_policy="production")
    evaluation_allowed = catalog.filter(usage_policy="evaluation_only")

    assert tuple(provider.id for provider in production_only.providers) == ("chatterbox_v3", "piper")
    assert tuple(provider.id for provider in evaluation_allowed.providers) == (
        "chatterbox_v3",
        "piper",
        "xtts_v2_eval",
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TTSVoiceDescriptor(
            id=" ",
            display_name="Blank voice id",
            provider_id="provider",
            model_id="model",
            voice_mode="builtin",
            supported_languages=("pl",),
            preview_supported=True,
            reference_audio_required=False,
        ),
        lambda: TTSProviderDescriptor(
            id="duplicate",
            display_name="Duplicate provider",
            usage_policy="production",
            supported_languages=("pl",),
            capabilities=TTSCapabilities(
                provider_name="duplicate",
                supported_languages=("pl",),
                voice_modes=("builtin",),
                reference_audio_required=False,
                speaking_rate_supported=False,
                usage_policy="production",
            ),
            models=(
                TTSModelDescriptor(
                    id="one",
                    display_name="One",
                    provider_id="duplicate",
                    supported_languages=("pl",),
                    voices=(
                        TTSVoiceDescriptor(
                            id="voice",
                            display_name="Voice",
                            provider_id="duplicate",
                            model_id="one",
                            voice_mode="builtin",
                            supported_languages=("pl",),
                            preview_supported=True,
                            reference_audio_required=False,
                        ),
                    ),
                    runtime_required=True,
                    asset_required=False,
                ),
                TTSModelDescriptor(
                    id="one",
                    display_name="One again",
                    provider_id="duplicate",
                    supported_languages=("pl",),
                    voices=(
                        TTSVoiceDescriptor(
                            id="voice_two",
                            display_name="Voice two",
                            provider_id="duplicate",
                            model_id="one",
                            voice_mode="builtin",
                            supported_languages=("pl",),
                            preview_supported=True,
                            reference_audio_required=False,
                        ),
                    ),
                    runtime_required=True,
                    asset_required=False,
                ),
            ),
        ),
        lambda: TTSModelDescriptor(
            id="model",
            display_name="Model",
            provider_id="provider",
            supported_languages=("pl",),
            voices=(
                TTSVoiceDescriptor(
                    id="voice",
                    display_name="Voice",
                    provider_id="provider",
                    model_id="model",
                    voice_mode="builtin",
                    supported_languages=("pl",),
                    preview_supported=True,
                    reference_audio_required=False,
                ),
                TTSVoiceDescriptor(
                    id="voice",
                    display_name="Voice duplicate",
                    provider_id="provider",
                    model_id="model",
                    voice_mode="builtin",
                    supported_languages=("pl",),
                    preview_supported=True,
                    reference_audio_required=False,
                ),
            ),
            runtime_required=True,
            asset_required=False,
        ),
        lambda: TTSVoiceDescriptor(
            id="voice",
            display_name="Voice",
            provider_id="provider",
            model_id="model",
            voice_mode="builtin",
            supported_languages=("pl",),
            preview_supported=True,
            reference_audio_required=False,
            public_metadata={"reference_audio_path": r"C:\\private\\voice.wav"},
        ),
        lambda: TTSVoiceDescriptor(
            id="voice",
            display_name="Voice",
            provider_id="provider",
            model_id="model",
            voice_mode="builtin",
            supported_languages=("pl",),
            preview_supported=True,
            reference_audio_required=False,
            public_metadata={"note": "Bearer abc123"},
        ),
        lambda: TTSVoiceDescriptor(
            id="voice",
            display_name="Voice",
            provider_id="provider",
            model_id="model",
            voice_mode="builtin",
            supported_languages=("pl",),
            preview_supported=True,
            reference_audio_required=False,
            public_metadata={"secret_token": "abc123"},
        ),
        lambda: TTSVoiceDescriptor(
            id="voice",
            display_name="Voice",
            provider_id="provider",
            model_id="model",
            voice_mode="builtin",
            supported_languages=("pl",),
            preview_supported=True,
            reference_audio_required=False,
            public_metadata={"notes": ["alpha", "sk-live-abc123"]},
        ),
    ],
)
def test_descriptor_validation_rejects_duplicates_paths_and_secrets(factory) -> None:
    with pytest.raises(TTSCatalogError):
        factory()


def test_descriptor_validation_rejects_inconsistent_relationships_and_language_tags() -> None:
    voice = TTSVoiceDescriptor(
        id="voice",
        display_name="Voice",
        provider_id="provider",
        model_id="model",
        voice_mode="builtin",
        supported_languages=("pl",),
        preview_supported=True,
        reference_audio_required=False,
    )

    with pytest.raises(TTSCatalogError, match="provider id"):
        TTSProviderDescriptor(
            id="provider",
            display_name="Provider",
            usage_policy="production",
            supported_languages=("pl",),
            capabilities=TTSCapabilities(
                provider_name="different",
                supported_languages=("pl",),
                voice_modes=("builtin",),
                reference_audio_required=False,
                speaking_rate_supported=False,
                usage_policy="production",
            ),
            models=(
                TTSModelDescriptor(
                    id="model",
                    display_name="Model",
                    provider_id="provider",
                    supported_languages=("pl",),
                    voices=(voice,),
                    runtime_required=True,
                    asset_required=False,
                ),
            ),
        )

    with pytest.raises(TTSCatalogError, match="normalized language tag"):
        TTSVoiceDescriptor(
            id="voice",
            display_name="Voice",
            provider_id="provider",
            model_id="model",
            voice_mode="builtin",
            supported_languages=("invalid tag",),
            preview_supported=True,
            reference_audio_required=False,
        )


def test_catalog_construction_does_not_import_optional_runtimes() -> None:
    for module_name in ("torch", "torchaudio", "piper", "TTS", "chatterbox", "chatterbox.mtl_tts"):
        sys.modules.pop(module_name, None)

    catalog = _chatterbox_catalog()

    assert catalog.providers[0].capabilities.provider_name == "chatterbox_v3"
    assert "torch" not in sys.modules
    assert "torchaudio" not in sys.modules
    assert "piper" not in sys.modules
    assert "TTS" not in sys.modules
    assert "chatterbox" not in sys.modules

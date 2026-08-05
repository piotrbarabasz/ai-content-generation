from __future__ import annotations

import io
import tempfile
import wave
from pathlib import Path

import pytest

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.mock_tts import MockTTSProvider
from app.providers.registry import ProviderRegistry
from app.providers.tts_capabilities import TTSCapabilities
from app.providers.tts_factory import TTSFactoryError, build_tts_provider
from app.providers.tts_settings import TTSSettings, TTSSettingsError
from app.tooling import tts_smoke


def _config(name: str, settings: dict[str, object] | None = None) -> ProviderConfig:
    return ProviderConfig.create(
        workflow_config_id="workflow",
        provider_type=ProviderType.TTS,
        provider_name=name,
        settings=settings,
    )


def _wav_bytes(*, sample_rate: int = 24_000, frames: int = 12) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\0\0" * frames)
    return buffer.getvalue()


def _capabilities(provider_name: str) -> TTSCapabilities:
    if provider_name == "mock":
        return TTSCapabilities(
            provider_name="mock",
            supported_languages=("*",),
            voice_modes=("mock",),
            reference_audio_required=False,
            speaking_rate_supported=False,
            usage_policy="production",
        )
    if provider_name == "chatterbox_v3":
        return TTSCapabilities(
            provider_name="chatterbox_v3",
            supported_languages=("en", "pl"),
            voice_modes=("builtin", "reference"),
            reference_audio_required=False,
            speaking_rate_supported=False,
            usage_policy="production",
        )
    if provider_name == "piper":
        return TTSCapabilities(
            provider_name="piper",
            supported_languages=("pl",),
            voice_modes=("catalog", "local_path"),
            reference_audio_required=False,
            speaking_rate_supported=False,
            usage_policy="production",
        )
    if provider_name == "xtts_v2_eval":
        return TTSCapabilities(
            provider_name="xtts_v2_eval",
            supported_languages=("pl",),
            voice_modes=("reference",),
            reference_audio_required=True,
            speaking_rate_supported=False,
            usage_policy="evaluation_only",
        )
    raise AssertionError(f"Unexpected provider name: {provider_name}")


class SelectedProvider(MockTTSProvider):
    def __init__(self, provider_name: str) -> None:
        super().__init__(provider_name)
        self.received_settings: TTSSettings | None = None

    def capabilities(self) -> TTSCapabilities:
        return _capabilities(self.provider_name)


def _provider_factories(seen: dict[str, SelectedProvider]) -> dict[str, object]:
    def factory(provider_name: str):
        def _build(settings: TTSSettings) -> SelectedProvider:
            provider = SelectedProvider(provider_name)
            provider.received_settings = settings
            seen[provider_name] = provider
            return provider

        return _build

    return {
        "mock": factory("mock"),
        "chatterbox_v3": factory("chatterbox_v3"),
        "piper": factory("piper"),
        "xtts_v2_eval": factory("xtts_v2_eval"),
    }


@pytest.mark.parametrize(
    ("provider_name", "settings", "field_checks"),
    [
        ("mock", {}, ()),
        (
            "chatterbox_v3",
            {"language_id": "pl", "exaggeration": 0.5, "usage_policy": "production"},
            ("language_id", "exaggeration", "usage_policy"),
        ),
        (
            "piper",
            {
                "language_id": "pl",
                "model_key": "pl_PL-gosia-medium",
                "length_scale": 1.2,
                "volume": 0.75,
                "noise_scale": 0.2,
                "noise_w_scale": 0.9,
            },
            ("model_key", "length_scale", "noise_w_scale"),
        ),
        (
            "xtts_v2_eval",
            {
                "language_id": "pl",
                "approved_label": "consent-2026-08",
                "usage_policy": "evaluation_only",
            },
            ("approved_label", "usage_policy"),
        ),
    ],
)
def test_tts_settings_accept_supported_provider_specific_fields(
    provider_name: str,
    settings: dict[str, object],
    field_checks: tuple[str, ...],
) -> None:
    if provider_name == "xtts_v2_eval":
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            reference_audio_path = Path(directory) / "approved-reference.wav"
            reference_audio_path.write_bytes(_wav_bytes())
            settings = {**settings, "reference_audio_path": reference_audio_path}
            result = TTSSettings.from_mapping(settings, provider=provider_name)
            assert result.provider == provider_name
            for field_name in field_checks:
                assert getattr(result, field_name) is not None
            assert result.reference_audio_path == settings["reference_audio_path"]
            return
    result = TTSSettings.from_mapping(settings, provider=provider_name)

    assert result.provider == provider_name
    for field_name in field_checks:
        assert getattr(result, field_name) is not None
    if provider_name == "xtts_v2_eval":
        assert result.reference_audio_path == settings["reference_audio_path"]


@pytest.mark.parametrize(
    ("provider_name", "settings", "expected_message"),
    [
        ("mock", {"model_key": "pl_PL-gosia-medium"}, "model_key"),
        ("chatterbox_v3", {"approved_label": "consent-2026-08"}, "approved_label"),
        ("piper", {"audio_prompt_path": Path("speaker.wav")}, "audio_prompt_path"),
        ("xtts_v2_eval", {"model_path": Path("model.onnx")}, "model_path"),
    ],
)
def test_tts_settings_reject_foreign_provider_fields(
    provider_name: str,
    settings: dict[str, object],
    expected_message: str,
) -> None:
    with pytest.raises(TTSSettingsError, match=expected_message):
        TTSSettings.from_mapping(settings, provider=provider_name)


@pytest.mark.parametrize(
    ("provider_name", "settings"),
    [
        ("mock", {}),
        ("chatterbox_v3", {"language_id": "pl", "exaggeration": 0.5}),
        (
            "piper",
            {
                "language_id": "pl",
                "model_key": "pl_PL-gosia-medium",
                "length_scale": 1.25,
                "noise_w_scale": 0.9,
            },
        ),
        (
            "xtts_v2_eval",
            {
                "language_id": "pl",
                "approved_label": "consent-2026-08",
                "usage_policy": "evaluation_only",
            },
        ),
    ],
)
def test_factory_composes_supported_providers_through_the_registry(
    provider_name: str,
    settings: dict[str, object],
) -> None:
    if provider_name == "xtts_v2_eval":
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            reference_audio_path = Path(directory) / "approved-reference.wav"
            reference_audio_path.write_bytes(_wav_bytes())
            settings = {**settings, "reference_audio_path": reference_audio_path}
            registry = ProviderRegistry()
            seen: dict[str, SelectedProvider] = {}
            provider = build_tts_provider(
                _config(provider_name, settings),
                registry=registry,
                provider_factories=_provider_factories(seen),
            )

            assert provider is seen[provider_name]
            assert registry.resolve_from_config(_config(provider_name, settings)) is provider
            assert seen[provider_name].received_settings is not None
            assert seen[provider_name].received_settings.provider == provider_name
            assert seen[provider_name].received_settings.approved_label == "consent-2026-08"
            assert seen[provider_name].received_settings.usage_policy == "evaluation_only"
            return
    registry = ProviderRegistry()
    seen: dict[str, SelectedProvider] = {}
    provider = build_tts_provider(
        _config(provider_name, settings),
        registry=registry,
        provider_factories=_provider_factories(seen),
    )

    assert provider is seen[provider_name]
    assert registry.resolve_from_config(_config(provider_name, settings)) is provider
    assert seen[provider_name].received_settings is not None
    assert seen[provider_name].received_settings.provider == provider_name
    if provider_name == "piper":
        assert seen[provider_name].received_settings.model_key == "pl_PL-gosia-medium"
        assert seen[provider_name].received_settings.length_scale == 1.25
    if provider_name == "xtts_v2_eval":
        assert seen[provider_name].received_settings.approved_label == "consent-2026-08"
        assert seen[provider_name].received_settings.usage_policy == "evaluation_only"


def test_factory_rejects_xtts_production_policy_before_registry_registration(
) -> None:
    registry = ProviderRegistry()
    seen: dict[str, SelectedProvider] = {}
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        reference_audio_path = Path(directory) / "xtts-reference.wav"
        reference_audio_path.write_bytes(_wav_bytes())

        with pytest.raises(TTSFactoryError, match="production mode"):
            build_tts_provider(
                _config(
                    "xtts_v2_eval",
                    {
                        "reference_audio_path": reference_audio_path,
                        "approved_label": "consent-2026-08",
                        "usage_policy": "production",
                    },
                ),
                registry=registry,
                provider_factories=_provider_factories(seen),
            )

    assert registry.snapshot() == ()
    assert "xtts_v2_eval" in seen


def test_smoke_uses_the_same_factory_path_as_direct_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePiperProvider(MockTTSProvider):
        def __init__(self, provider_name: str, **kwargs: object) -> None:
            super().__init__(provider_name)
            self.kwargs = dict(kwargs)

        def capabilities(self) -> TTSCapabilities:
            return _capabilities("piper")

    monkeypatch.setattr(tts_smoke, "PiperTTSProvider", FakePiperProvider)

    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        args = tts_smoke.build_parser().parse_args(
            [
                "--provider",
                "piper",
                "--text",
                "Jedna, dwie, trzy.",
                "--output",
                str(Path(directory) / "speech.wav"),
                "--model-key",
                "pl_PL-gosia-medium",
                "--length-scale",
                "1.25",
                "--volume",
                "0.75",
            ]
        )

        smoke_provider = tts_smoke._create_provider(args)
        direct_provider = build_tts_provider(
            _config(
                "piper",
                {
                    "model_key": "pl_PL-gosia-medium",
                    "length_scale": 1.25,
                    "volume": 0.75,
                },
            ),
            provider_factories=tts_smoke._provider_factories(),
        )

        assert isinstance(smoke_provider, FakePiperProvider)
        assert isinstance(direct_provider, FakePiperProvider)
        assert smoke_provider.kwargs == direct_provider.kwargs

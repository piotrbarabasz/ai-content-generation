from __future__ import annotations

import hashlib
import io
import json
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.tts_factory import TTSFactoryError, build_tts_provider
from app.providers.xtts_v2 import (
    XTTSConfigurationError,
    XTTSAudioValidationError,
    XTTSV2EvalProvider,
)


def _config(
    settings: dict[str, object] | None = None,
    *,
    provider_name: str = "xtts_v2_eval",
) -> ProviderConfig:
    return ProviderConfig.create(
        workflow_config_id="workflow",
        provider_type=ProviderType.TTS,
        provider_name=provider_name,
        settings=settings,
    )


def _wav_bytes(
    *,
    sample_rate: int = 24_000,
    channels: int = 1,
    sample_width: int = 2,
    frames: int = 12,
) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\0" * (frames * channels * sample_width))
    return buffer.getvalue()


class RecordingRuntime:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    def synthesize(self, text: str, **kwargs: object) -> bytes:
        self.calls.append((text, dict(kwargs)))
        return self.payload


def test_xtts_settings_round_trip_through_factory_and_registry() -> None:
    with TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp_dir:
        reference_audio_path = Path(temp_dir) / "approved-reference.wav"
        reference_audio_path.write_bytes(_wav_bytes())

        registry_provider = build_tts_provider(
            _config(
                {
                    "reference_audio_path": reference_audio_path,
                    "approved_label": "consent-2026-08",
                    "usage_policy": "evaluation_only",
                }
            )
        )

        assert isinstance(registry_provider, XTTSV2EvalProvider)
        assert registry_provider.provider_name == "xtts_v2_eval"
        assert registry_provider._backend is None
        assert registry_provider.reference_audio_path == reference_audio_path
        assert registry_provider.approved_label == "consent-2026-08"


def test_xtts_factory_rejects_production_policy_before_backend_load(monkeypatch) -> None:
    calls: list[str] = []

    def fail_loader(_model_reference: dict[str, object]) -> object:
        calls.append("called")
        raise AssertionError("XTTS backend should not load for production policy.")

    monkeypatch.setattr("app.providers.xtts_v2._load_runtime_backend", fail_loader)

    with TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp_dir:
        reference_audio_path = Path(temp_dir) / "approved-reference.wav"
        reference_audio_path.write_bytes(_wav_bytes())

        with pytest.raises(TTSFactoryError, match="evaluation-only|production mode"):
            build_tts_provider(
                _config(
                    {
                        "reference_audio_path": reference_audio_path,
                        "approved_label": "consent-2026-08",
                        "usage_policy": "production",
                    }
                )
            )

    assert calls == []


def test_xtts_identity_stores_checksum_and_approved_label_without_paths() -> None:
    with TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp_dir:
        reference_audio_path = Path(temp_dir) / "approved-reference.wav"
        reference_bytes = _wav_bytes(sample_rate=22_050, frames=24)
        reference_audio_path.write_bytes(reference_bytes)

        provider = XTTSV2EvalProvider(
            reference_audio_path=reference_audio_path,
            approved_label="consent-2026-08",
            model_loader=lambda _: RecordingRuntime(_wav_bytes()),
        )

        identity = provider.effective_synthesis_identity()

        assert provider._backend is None
        assert identity["provider"] == "xtts_v2_eval"
        assert identity["model_variant"] == "xtts_v2"
        assert identity["language_id"] == "pl"
        assert identity["voice"] == {
            "mode": "reference",
            "approved_label": "consent-2026-08",
            "content_checksum": hashlib.sha256(reference_bytes).hexdigest(),
        }
        assert str(reference_audio_path) not in json.dumps(identity, sort_keys=True)


def test_xtts_synthesize_passes_reference_audio_and_validates_wav() -> None:
    with TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp_dir:
        reference_audio_path = Path(temp_dir) / "approved-reference.wav"
        reference_audio_path.write_bytes(_wav_bytes(sample_rate=24_000, frames=48))
        runtime = RecordingRuntime(_wav_bytes(sample_rate=22_050, frames=32))
        provider = XTTSV2EvalProvider(
            reference_audio_path=reference_audio_path,
            approved_label="consent-2026-08",
            model_loader=lambda _: runtime,
        )

        result = provider.synthesize("Tekst próbny")

        assert runtime.calls == [
            (
                "Tekst próbny",
                {
                    "speaker_wav": str(reference_audio_path),
                    "language": "pl",
                },
            )
        ]
        assert result.provider_name == "xtts_v2_eval"
        assert result.sample_rate == 22_050
        assert result.metadata["voice"] == {
            "mode": "reference",
            "approved_label": "consent-2026-08",
            "content_checksum": hashlib.sha256(reference_audio_path.read_bytes()).hexdigest(),
        }


def test_xtts_rejects_invalid_wav_output() -> None:
    with TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp_dir:
        reference_audio_path = Path(temp_dir) / "approved-reference.wav"
        reference_audio_path.write_bytes(_wav_bytes())
        provider = XTTSV2EvalProvider(
            reference_audio_path=reference_audio_path,
            approved_label="consent-2026-08",
            model_loader=lambda _: RecordingRuntime(b"not-a-wav"),
        )

        with pytest.raises(XTTSAudioValidationError, match="WAV"):
            provider.synthesize("Tekst próbny")


def test_xtts_rejects_missing_reference_audio_with_redacted_message() -> None:
    missing_reference = Path(__file__).resolve().parent / "missing-reference.wav"
    provider = XTTSV2EvalProvider(
        reference_audio_path=missing_reference,
        approved_label="consent-2026-08",
        model_loader=lambda _: RecordingRuntime(_wav_bytes()),
    )

    with pytest.raises(XTTSConfigurationError) as exc_info:
        provider.effective_synthesis_identity()

    message = str(exc_info.value)
    assert missing_reference.name in message
    assert str(missing_reference) not in message

from __future__ import annotations

import io
import json
import tempfile
import wave
from pathlib import Path

import pytest

from app.providers.piper_tts import PiperTTSProvider
from app.providers.tts_settings import TTSSettings, TTSSettingsError
from app.tooling import tts_compare, tts_smoke


def _wav(*, sample_rate: int = 24_000, channels: int = 1, sample_width: int = 2, frames: int = 12) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\0" * (frames * channels * sample_width))
    return buffer.getvalue()


class RecordingBackend:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    def synthesize(self, text: str, **kwargs: object) -> bytes:
        self.calls.append((text, dict(kwargs)))
        return self.payload


def test_piper_settings_validate_controls_and_reject_leakage() -> None:
    settings = TTSSettings.from_mapping(
        {
            "model_key": "pl_PL-gosia-medium",
            "length_scale": 1.25,
            "volume": 0.75,
            "noise_scale": 0.2,
            "noise_w_scale": 0.9,
        },
        provider="piper",
    )

    assert settings.provider == "piper"
    assert settings.model_key == "pl_PL-gosia-medium"
    assert settings.length_scale == 1.25
    assert settings.volume == 0.75
    assert settings.noise_scale == 0.2
    assert settings.noise_w_scale == 0.9

    with pytest.raises(TTSSettingsError, match="only supported by Piper"):
        TTSSettings.from_mapping({"length_scale": 1.0}, provider="mock")
    with pytest.raises(TTSSettingsError, match="greater than zero"):
        TTSSettings.from_mapping({"model_key": "pl_PL-gosia-medium", "length_scale": 0.0}, provider="piper")
    with pytest.raises(TTSSettingsError, match="greater than or equal to zero"):
        TTSSettings.from_mapping({"model_key": "pl_PL-gosia-medium", "noise_w_scale": -0.1}, provider="piper")


def test_piper_provider_forwards_controls_and_records_effective_identity() -> None:
    backend = RecordingBackend(_wav())
    provider = PiperTTSProvider(
        model_key="pl_PL-gosia-medium",
        length_scale=1.25,
        volume=0.75,
        noise_scale=0.2,
        noise_w_scale=0.9,
        model_loader=lambda _: backend,
    )

    identity = provider.effective_synthesis_identity()
    result = provider.synthesize("tekst")

    assert identity["generation_settings"] == {
        "length_scale": 1.25,
        "volume": 0.75,
        "noise_scale": 0.2,
        "noise_w_scale": 0.9,
    }
    assert result.metadata["generation_settings"] == identity["generation_settings"]
    assert backend.calls == [
        (
            "tekst",
            {
                "language_id": "pl",
                "length_scale": 1.25,
                "volume": 0.75,
                "noise_scale": 0.2,
                "noise_w_scale": 0.9,
            },
        )
    ]


def test_default_comparison_profiles_include_chatterbox_and_curated_piper_voices() -> None:
    assert tuple(profile.profile_id for profile in tts_compare.default_profiles()) == (
        "chatterbox-neutral",
        "piper-pl_PL-bass-high",
        "piper-pl_PL-darkman-medium",
        "piper-pl_PL-gosia-medium",
        "piper-pl_PL-mc_speech-medium",
        "piper-pl_PL-mls_6892-low",
    )


def test_comparison_runner_runs_profiles_sequentially_and_continues_after_failure(
    monkeypatch,
) -> None:
    call_order: list[str] = []

    def fake_smoke_run(args):
        call_order.append(args.report.parent.name)
        args.output.write_bytes(_wav())
        if args.model_key == "pl_PL-darkman-medium":
            raise RuntimeError("profile boom")
        report = {
            "provider": args.provider,
            "model": args.model_key or "v3",
            "device": args.device,
            "language": args.language,
            "voice_mode": "builtin" if args.provider == "chatterbox_v3" else "catalog",
            "word_count": len(args.text.split()),
            "normalized_text": args.text,
            "report_path": str(args.report),
        }
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report

    monkeypatch.setattr(tts_smoke, "run", fake_smoke_run)

    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        output_dir = Path(directory) / "comparison"
        summary = tts_compare.run(
            tts_compare.build_parser().parse_args(
                ["--text", "Witaj   świecie", "--output-dir", str(output_dir)]
            )
        )

        assert call_order == [
            "chatterbox-neutral",
            "piper-pl_PL-bass-high",
            "piper-pl_PL-darkman-medium",
            "piper-pl_PL-gosia-medium",
            "piper-pl_PL-mc_speech-medium",
            "piper-pl_PL-mls_6892-low",
        ]
        assert summary["normalized_text"] == "Witaj świecie"
        assert summary["summary"] == {"profile_count": 6, "completed_count": 5, "failed_count": 1}
        assert [profile["normalized_text"] for profile in summary["profiles"]] == ["Witaj świecie"] * 6

        failed = next(profile for profile in summary["profiles"] if profile["status"] == "failed")
        assert failed["profile_id"] == "piper-pl_PL-darkman-medium"
        assert "profile boom" in failed["reason"]

        playlist = Path(summary["playlist_path"]).read_text(encoding="utf-8").splitlines()
        assert playlist[0] == "#EXTM3U"
        assert all(line.endswith(".wav") for line in playlist[2::2])
        assert len(playlist[1::2]) == 5
        assert all(Path(profile["report_path"]).exists() for profile in summary["profiles"])

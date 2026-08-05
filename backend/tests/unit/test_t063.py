from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.modules.voiceover import VoiceoverModule
from app.providers.mock_tts import MockTTSProvider
from app.providers.tts_result import TTSSynthesisResult
from app.storage.local_store import LocalArtifactStore
from app.tooling import tts_smoke
from app.tts.benchmark import build_benchmark_report
from app.tts.chunk_synthesis import ResumableChunkSynthesizer
from app.tts.chunking import chunk_narration
from app.workflow.execution import ModuleExecutionContext


class IdentityProvider(MockTTSProvider):
    def effective_synthesis_identity(self, voice_config=None):
        return {
            "provider": "effective-provider",
            "model_variant": "effective-model",
            "device": "effective-device",
            "language_id": "pl",
            "voice": {"mode": "reference", "speaker_path": "C:\\private\\speaker.wav"},
        }


def test_smoke_report_uses_effective_model_identity_not_default_cli_variant(monkeypatch) -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        output = Path(directory) / "speech.wav"
        monkeypatch.setattr(tts_smoke, "_create_provider", lambda args: IdentityProvider())

        report = tts_smoke.run(
            tts_smoke.build_parser().parse_args(["--text", "One two.", "--output", str(output)])
        )

        assert report["model"] == "effective-model"
        assert report["model_variant"] == "effective-model"
        assert "C:\\private\\speaker.wav" not in str(report)


def test_resumable_manifest_persists_sanitized_identity_and_current_run_counts() -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        runtime_dir = Path(directory)
        provider = IdentityProvider()
        chunks = chunk_narration("One. Two.", max_words=1)
        synthesizer = ResumableChunkSynthesizer(provider)

        first = synthesizer.synthesize(chunks, runtime_dir=runtime_dir)
        second = synthesizer.synthesize(chunks, runtime_dir=runtime_dir)

        assert first.manifest.generated_chunk_count == 2
        assert first.manifest.reused_chunk_count == 0
        assert second.manifest.generated_chunk_count == 0
        assert second.manifest.reused_chunk_count == 2
        assert second.manifest.failed_chunk_count == 0
        assert second.manifest.effective_synthesis_identity["voice"] == {"mode": "reference"}
        report = build_benchmark_report(
            second.manifest,
            provider="caller-guess",
            model="caller-guess",
            device="caller-guess",
            language="caller-guess",
            word_count=2,
            generation_wall_time_seconds=1.0,
        )
        assert (report.provider, report.model, report.device, report.language, report.voice_mode) == (
            "effective-provider", "effective-model", "effective-device", "pl", "reference"
        )
        assert report.full_cache_hit
        assert report.real_time_factor == round(1.0 / second.manifest.final_duration_seconds, 6)
        assert "speaker.wav" not in str(second.manifest.to_payload())


class CompressedWavProvider(MockTTSProvider):
    def synthesize(self, text, voice_config=None):
        valid = super().synthesize(text, voice_config)
        return TTSSynthesisResult(
            audio_bytes=valid.audio_bytes.replace(b"WAVEfmt ", b"WAVEJUNK", 1),
            sample_rate=valid.sample_rate,
            duration_seconds=valid.duration_seconds,
            audio_format="wav",
            provider_name=valid.provider_name,
        )


def test_voiceover_uses_shared_pcm_rejection() -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        module = VoiceoverModule(
            tts_provider=CompressedWavProvider(), artifact_store=LocalArtifactStore(directory)
        )
        context = ModuleExecutionContext("run", "config", "voiceover", inputs={"text": "Narration."})

        with pytest.raises(ValueError, match="readable WAV"):
            module.execute(context)


def test_smoke_report_accepts_piper_controls_and_keeps_resolved_identity(monkeypatch) -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        output = Path(directory) / "speech.wav"
        captured: dict[str, object] = {}

        class FakePiperProvider:
            provider_name = "piper"

            def __init__(self, provider_name: str, **kwargs: object) -> None:
                self.provider_name = provider_name
                self.kwargs = dict(kwargs)

            def effective_synthesis_identity(self, voice_config=None):
                return {
                    "provider": "piper",
                    "model_variant": "pl_PL-gosia-medium",
                    "device": self.kwargs["device"],
                    "language_id": self.kwargs["language_id"],
                    "generation_settings": {
                        "length_scale": self.kwargs["length_scale"],
                        "volume": self.kwargs["volume"],
                        "noise_scale": self.kwargs["noise_scale"],
                        "noise_w_scale": self.kwargs["noise_w_scale"],
                    },
                    "voice": {
                        "mode": "catalog",
                        "model": {
                            "kind": "catalog_voice",
                            "provider_key": "pl_PL-gosia-medium",
                        },
                    },
                }

            def synthesize(self, text, voice_config=None):
                captured["text"] = text
                captured["voice_config"] = voice_config
                captured["kwargs"] = dict(self.kwargs)
                return MockTTSProvider("piper").synthesize(text, {})

        monkeypatch.setattr(tts_smoke, "PiperTTSProvider", FakePiperProvider)
        report = tts_smoke.run(
            tts_smoke.build_parser().parse_args(
                [
                    "--provider",
                    "piper",
                    "--text",
                    "Jedna, dwie, trzy.",
                    "--output",
                    str(output),
                    "--model-key",
                    "pl_PL-gosia-medium",
                    "--length-scale",
                    "1.25",
                    "--volume",
                    "0.75",
                    "--noise-scale",
                    "0.2",
                    "--noise-w-scale",
                    "0.9",
                ]
            )
        )

        assert captured["text"] == "Jedna, dwie, trzy."
        assert captured["voice_config"] == {"language_id": "pl"}
        assert captured["kwargs"] == {
            "device": "cpu",
            "language_id": "pl",
            "model_key": "pl_PL-gosia-medium",
            "model_path": None,
            "length_scale": 1.25,
            "volume": 0.75,
            "noise_scale": 0.2,
            "noise_w_scale": 0.9,
        }
        assert report["provider"] == "piper"
        assert report["model_variant"] == "pl_PL-gosia-medium"
        assert report["effective_synthesis_identity"]["generation_settings"] == {
            "length_scale": 1.25,
            "volume": 0.75,
            "noise_scale": 0.2,
            "noise_w_scale": 0.9,
        }
        assert report["effective_synthesis_identity"]["voice"]["model"]["provider_key"] == "pl_PL-gosia-medium"

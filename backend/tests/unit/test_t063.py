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

from __future__ import annotations

from hashlib import sha256
import io
from math import inf, nan
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest

from app.modules import voiceover as voiceover_module
from app.modules.voiceover import VoiceoverModule, _provider_voice_config
from app.providers.mock_tts import MockTTSProvider
from app.storage.local_store import LocalArtifactStore
from app.tts.assembly import inspect_pcm_wav
from app.tts.post_processing import (
    AudioPostProcessingError,
    AudioPostProcessingResult,
    process_pcm_wav_tempo,
    tempo_from_voice_config,
    validate_tempo,
)
from app.workflow.execution import ModuleExecutionContext


def _wav(*, duration: float = 1.0, sample_rate: int = 8_000, channels: int = 1) -> bytes:
    output = io.BytesIO()
    frame_count = round(duration * sample_rate)
    with wave.open(output, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\0\0" * channels * frame_count)
    return output.getvalue()


def _successful_runner(calls: list[tuple[list[str], dict[str, object]]]):
    def run(command, **kwargs):
        calls.append((command, kwargs))
        source = Path(command[command.index("-i") + 1]).read_bytes()
        source_parameters, _ = inspect_pcm_wav(source)
        tempo = float(command[command.index("-filter:a") + 1].split("=", 1)[1])
        Path(command[-1]).write_bytes(
            _wav(
                duration=source_parameters.duration_seconds / tempo,
                sample_rate=source_parameters.sample_rate,
                channels=source_parameters.channels,
            )
        )
        return SimpleNamespace(returncode=0, stderr=b"")

    return run


def test_default_tempo_is_byte_compatible_noop_and_never_locates_or_runs_ffmpeg() -> None:
    source = _wav()

    def unexpected(*_args, **_kwargs):
        raise AssertionError("FFmpeg path must not be touched for tempo 1.0")

    result = process_pcm_wav_tempo(
        source, process_runner=unexpected, ffmpeg_locator=unexpected
    )

    assert result.audio_bytes is source
    assert result.processor == "none"
    assert result.tempo == 1.0
    assert result.input_checksum == result.output_checksum == sha256(source).hexdigest()


def test_tempo_uses_ffmpeg_argument_list_without_shell_and_validates_longer_output() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    result = process_pcm_wav_tempo(
        _wav(),
        0.92,
        process_runner=_successful_runner(calls),
        ffmpeg_locator=lambda _name: "ffmpeg-test",
    )

    command, kwargs = calls[0]
    assert isinstance(command, list)
    assert command[:7] == [
        "ffmpeg-test", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i"
    ]
    assert command[command.index("-filter:a") + 1] == "atempo=0.92"
    assert command[command.index("-c:a") + 1] == "pcm_s16le"
    assert "shell" not in kwargs
    assert result.processor == "ffmpeg_atempo"
    assert result.output_duration_seconds > result.input_duration_seconds
    assert result.audio_parameters.sample_width == 2


@pytest.mark.parametrize("value", [0, -1, 0.49, 2.01, True, nan, inf, -inf, "0.92"])
def test_invalid_tempo_is_rejected_without_clamping(value: object) -> None:
    with pytest.raises(ValueError):
        validate_tempo(value)


def test_missing_ffmpeg_is_actionable_only_when_processing_is_requested() -> None:
    with pytest.raises(AudioPostProcessingError, match="FFmpeg is required.*tempo != 1.0"):
        process_pcm_wav_tempo(_wav(), 0.92, ffmpeg_locator=lambda _name: None)


def test_corrupt_ffmpeg_output_is_rejected() -> None:
    def corrupt(command, **_kwargs):
        Path(command[-1]).write_bytes(b"not wav")
        return SimpleNamespace(returncode=0, stderr=b"")

    with pytest.raises(AudioPostProcessingError, match="invalid PCM WAV"):
        process_pcm_wav_tempo(
            _wav(), 0.92, process_runner=corrupt, ffmpeg_locator=lambda _name: "ffmpeg"
        )


def test_config_accepts_both_naming_conventions_and_provider_filter_strips_them() -> None:
    assert tempo_from_voice_config({"post_processing": {"tempo": 0.92}}) == 0.92
    assert tempo_from_voice_config({"postProcessing": {"tempo": 0.9}}) == 0.9
    assert tempo_from_voice_config({}) == 1.0
    assert _provider_voice_config(
        {
            "voice": "narrator",
            "resumable_chunking": {"max_words": 2},
            "post_processing": {"tempo": 0.92},
        }
    ) == {"voice": "narrator"}


class CapturingProvider(MockTTSProvider):
    def __init__(self) -> None:
        super().__init__()
        self.configs: list[dict[str, object]] = []

    def synthesize(self, text, voice_config=None):
        self.configs.append(dict(voice_config or {}))
        return super().synthesize(text, voice_config)


def _fake_processor(calls: list[tuple[bytes, float]]):
    def process(audio_bytes: bytes, tempo: float):
        calls.append((audio_bytes, tempo))
        parameters, _ = inspect_pcm_wav(audio_bytes)
        processed = _wav(
            duration=parameters.duration_seconds / tempo,
            sample_rate=parameters.sample_rate,
            channels=parameters.channels,
        )
        output_parameters, _ = inspect_pcm_wav(processed)
        return AudioPostProcessingResult(
            processed,
            output_parameters,
            tempo,
            "ffmpeg_atempo",
            parameters.duration_seconds,
            output_parameters.duration_seconds,
            sha256(audio_bytes).hexdigest(),
            sha256(processed).hexdigest(),
        )

    return process


def test_voiceover_direct_mode_processes_once_and_reports_final_audio(tmp_path, monkeypatch) -> None:
    provider = CapturingProvider()
    calls: list[tuple[bytes, float]] = []
    monkeypatch.setattr(voiceover_module, "process_pcm_wav_tempo", _fake_processor(calls))
    store = LocalArtifactStore(tmp_path / "artifacts")
    result = VoiceoverModule(tts_provider=provider, artifact_store=store).execute(
        ModuleExecutionContext(
            "direct", "config", "voiceover",
            inputs={"text": "Direct narration.", "voiceConfig": {"postProcessing": {"tempo": 0.92}}},
        )
    )

    voiceover = result.output["voiceover"]
    saved = store.read_artifact(voiceover["audio_storage_key"])
    parameters, _ = inspect_pcm_wav(saved)
    assert len(calls) == 1
    assert "postProcessing" not in provider.configs[0]
    assert voiceover["duration_seconds"] == round(parameters.duration_seconds, 3)
    assert voiceover["post_processing"]["tempo"] == 0.92
    assert voiceover["post_processing"]["output_checksum"] == sha256(saved).hexdigest()


def test_chunked_mode_processes_after_assembly_and_tempo_change_reuses_chunks(tmp_path, monkeypatch) -> None:
    provider = CapturingProvider()
    calls: list[tuple[bytes, float]] = []
    monkeypatch.setattr(voiceover_module, "process_pcm_wav_tempo", _fake_processor(calls))
    module = VoiceoverModule(
        tts_provider=provider,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        resumable_runtime_dir=tmp_path / "runtime",
    )

    def context(tempo: float):
        return ModuleExecutionContext(
            "chunked", "config", "voiceover",
            inputs={
                "text": "One two. Three four.",
                "voiceConfig": {
                    "resumableChunking": {"maxWords": 2, "maxAttempts": 1},
                    "postProcessing": {"tempo": tempo},
                },
            },
        )

    first = module.execute(context(0.92))
    provider_call_count = len(provider.configs)
    second = module.execute(context(0.9))

    assert provider_call_count == 2
    assert len(provider.configs) == provider_call_count
    assert len(calls) == 2
    assert calls[0][1] == 0.92 and calls[1][1] == 0.9
    assert first.output["voiceover"]["chunk_count"] == 2
    assert second.output["voiceover"]["post_processing"]["tempo"] == 0.9
    assert all("postProcessing" not in config and "resumableChunking" not in config for config in provider.configs)


def test_run_tts_demo_exposes_language_and_tempo() -> None:
    script = Path("scripts/run-tts-demo.ps1").read_text(encoding="utf-8")
    assert "[string]$Language = 'pl'" in script
    assert "[double]$Tempo = 1.0" in script
    assert "--language $Language" in script
    assert "--tempo $Tempo" in script

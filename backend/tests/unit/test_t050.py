from __future__ import annotations

import io
from contextlib import contextmanager
import shutil
from pathlib import Path
import wave

from app.domain.content_brief import ContentBrief
from app.domain.enums import DurationProfile, ProviderType
from app.modules.voiceover import VoiceoverModule
from app.providers.interfaces import TTSProvider
from app.providers.tts_result import TTSSynthesisResult
from app.storage.local_store import LocalArtifactStore
from app.workflow.execution import ModuleExecutionContext


@contextmanager
def _workspace_tempdir(name: str):
    root = Path(__file__).resolve().parents[3] / ".tmp" / name
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _build_wav_bytes(*, sample_rate: int = 16_000, duration_seconds: float = 0.5) -> bytes:
    frame_count = max(int(sample_rate * duration_seconds), sample_rate // 10)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * frame_count)
    return buffer.getvalue()


class PathLikeTTSProvider(TTSProvider):
    provider_type = ProviderType.TTS
    provider_name = "path-like"

    def synthesize(self, text: str, voice_config=None) -> TTSSynthesisResult:
        return TTSSynthesisResult(
            audio_bytes=_build_wav_bytes(),
            sample_rate=16_000,
            duration_seconds=0.5,
            audio_format="wav",
            provider_name=self.provider_name,
            metadata={
                "source_ref": "file:///tmp/private/voiceover.wav",
                "voice_config": dict(voice_config or {}),
            },
        )


def test_voiceover_module_persists_audio_bytes_instead_of_path_strings() -> None:
    with _workspace_tempdir("test_t050_voiceover") as store_root:
        store = LocalArtifactStore(store_root)
        provider = PathLikeTTSProvider()
        module = VoiceoverModule(tts_provider=provider, artifact_store=store)
        brief = ContentBrief.create(
            project_id="project_1",
            topic="Launch teaser",
            objective="Create a polished spoken teaser",
            audience="Early adopters",
            constraints=["Keep it under 30 seconds"],
            duration_profile=DurationProfile.SIXTY_SECONDS,
            success_criteria=["Sound confident"],
        )
        context = ModuleExecutionContext(
            workflow_run_id="workflow_run_50",
            workflow_config_id="workflow_config_50",
            module_name="voiceover",
            inputs={
                "content_brief": brief,
                "voice_config": {"voice": "narrator"},
            },
        )

        result = module.execute(context)
        voiceover = result.output["voiceover"]
        stored_audio = store.read_artifact(voiceover["audio_storage_key"])

        assert result.status == "completed"
        assert voiceover["audio_ref"] == "file:///tmp/private/voiceover.wav"
        assert voiceover["source_ref"] == "file:///tmp/private/voiceover.wav"
        assert stored_audio == _build_wav_bytes()
        assert stored_audio != b"file:///tmp/private/voiceover.wav"
        assert stored_audio.startswith(b"RIFF")
        with wave.open(io.BytesIO(stored_audio), "rb") as reader:
            assert reader.getnchannels() == 1
            assert reader.getsampwidth() == 2
            assert reader.getframerate() == 16_000
        assert result.output["artifact"]["metadata"]["source_ref"] == "file:///tmp/private/voiceover.wav"
        assert result.output["artifact"]["metadata"]["sample_rate"] == 16_000
        assert result.output["artifact"]["metadata"]["audio_format"] == "wav"

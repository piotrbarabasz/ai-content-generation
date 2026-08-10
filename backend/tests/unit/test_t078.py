from __future__ import annotations

import io
import json
import wave

from app.domain.enums import ProviderType
from app.providers.tts_capabilities import TTSCapabilities
from app.providers.tts_result import TTSSynthesisResult
from app.tts.assembly import inspect_pcm_wav
from app.tts.chunk_synthesis import ResumableChunkSynthesizer
from app.tts.chunking import chunk_narration, normalize_narration


def _wav(sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\1\0" * 400)
    return buffer.getvalue()


class FakeEnglishTTS:
    provider_type = ProviderType.TTS

    def __init__(self, provider_name="fake_a", sample_rate=24_000, fail_text="") -> None:
        self.provider_name = provider_name
        self.sample_rate = sample_rate
        self.fail_text = fail_text
        self.calls: list[str] = []

    def capabilities(self):
        return TTSCapabilities(
            provider_name=self.provider_name,
            supported_languages=("en",),
            voice_modes=("builtin",),
            reference_audio_required=False,
            speaking_rate_supported=False,
        )

    def effective_synthesis_identity(self, voice_config=None):
        config = dict(voice_config or {})
        return {
            "provider": self.provider_name,
            "model_variant": config.get("model", "v3"),
            "language_id": config.get("language_id", "en"),
            "device": "fake",
            "voice": {"mode": "builtin", "name": config.get("voice", "neutral")},
            "generation_settings": {"temperature": config.get("temperature", 0.7)},
        }

    def synthesize(self, text, voice_config=None):
        self.calls.append(text)
        if self.fail_text and self.fail_text in text:
            raise RuntimeError("intentional interruption")
        return TTSSynthesisResult(
            audio_bytes=_wav(self.sample_rate),
            sample_rate=self.sample_rate,
            duration_seconds=400 / self.sample_rate,
            audio_format="wav",
            provider_name=self.provider_name,
            metadata={},
        )


def test_long_english_chunking_interruption_resume_and_manifest(tmp_path) -> None:
    text = """Dr. Maya reviewed the first stable paragraph. It preserves names and punctuation.

    The second paragraph is intentionally interrupted. The final paragraph resumes normally."""
    chunks = chunk_narration(text, max_words=9)
    assert " ".join(chunk.text for chunk in chunks) == normalize_narration(text)
    failing = FakeEnglishTTS(fail_text="intentionally")
    first = ResumableChunkSynthesizer(failing, max_attempts=1).synthesize(
        chunks, runtime_dir=tmp_path, voice_config={"language_id": "en"}
    )
    assert not first.completed
    assert not (tmp_path / "voiceover.wav").exists()

    resumed = FakeEnglishTTS()
    second = ResumableChunkSynthesizer(resumed, max_attempts=1).synthesize(
        chunks, runtime_dir=tmp_path, voice_config={"language_id": "en"}
    )
    assert second.completed
    assert second.manifest.reused_chunk_count == len(chunks) - 1
    assert second.manifest.generated_chunk_count == 1
    assert second.manifest.effective_synthesis_identity["language_id"] == "en"
    parameters, _ = inspect_pcm_wav((tmp_path / "voiceover.wav").read_bytes())
    assert parameters.frame_count == 400 * len(chunks)
    persisted = json.loads((tmp_path / "synthesis-manifest.json").read_text(encoding="utf-8"))
    assert persisted["final_status"] == "completed"
    assert persisted["final_checksum"]


def test_cache_identity_and_artifact_contract_are_provider_neutral(tmp_path) -> None:
    chunks = chunk_narration("One stable sentence. Another stable sentence.", max_words=4)
    first = FakeEnglishTTS("fake_chatterbox", 24_000)
    first_result = ResumableChunkSynthesizer(first).synthesize(
        chunks,
        runtime_dir=tmp_path,
        voice_config={"language_id": "en", "voice": "a", "temperature": 0.7},
    )
    changed = FakeEnglishTTS("fake_other", 22_050)
    changed_result = ResumableChunkSynthesizer(changed).synthesize(
        chunks,
        runtime_dir=tmp_path,
        voice_config={"language_id": "en", "voice": "b", "temperature": 0.8},
    )
    assert first_result.completed and changed_result.completed
    assert changed_result.manifest.reused_chunk_count == 0
    assert changed_result.manifest.generated_chunk_count == len(chunks)
    assert changed_result.manifest.final_artifact_ref == "voiceover.wav"
    assert (tmp_path / "synthesis-manifest.json").exists()

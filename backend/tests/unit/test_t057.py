from __future__ import annotations

import tempfile
import wave
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest

from app.providers.mock_tts import MockTTSProvider
from app.tts.assembly import WavAssemblyError, assemble_pcm_wav, inspect_pcm_wav
from app.tts.chunk_synthesis import ResumableChunkSynthesizer
from app.tts.chunking import chunk_narration


@pytest.fixture
def runtime_dir():
    # The controlled test runner cannot enumerate its global OS temp root.
    # Keep these throwaway provider outputs under the writable test workspace.
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        yield Path(directory)


def test_resumable_synthesis_records_relative_valid_chunk_metadata_and_reuses(runtime_dir):
    provider = MockTTSProvider()
    chunks = chunk_narration("Pierwsze zdanie. Drugie zdanie.", max_words=2)
    synthesizer = ResumableChunkSynthesizer(provider)
    first = synthesizer.synthesize(chunks, runtime_dir=runtime_dir, voice_config={"voice": "narrator"})
    second = synthesizer.synthesize(chunks, runtime_dir=runtime_dir, voice_config={"voice": "narrator"})
    assert first.completed and second.completed
    assert second.manifest.final_status == "completed"
    assert all(record.artifact_ref and not record.artifact_ref.startswith("/") for record in second.manifest.chunks.values())
    assert all(record.wav_checksum and record.duration_seconds and record.audio_parameters for record in second.manifest.chunks.values())
    assert [record.attempts for record in second.manifest.chunks.values()] == [1] * len(chunks)
    with wave.open(str(runtime_dir / "voiceover.wav"), "rb") as final:
        assert final.getnframes() == sum(record.audio_parameters.frame_count for record in second.manifest.chunks.values())


def test_corrupt_chunk_is_regenerated_and_partial_failure_has_no_final_artifact(runtime_dir):
    chunks = chunk_narration("One. Two.", max_words=1)
    synthesizer = ResumableChunkSynthesizer(MockTTSProvider())
    first = synthesizer.synthesize(chunks, runtime_dir=runtime_dir)
    first_chunk = next(iter(first.manifest.chunks.values()))
    (runtime_dir / first_chunk.artifact_ref).write_bytes(b"corrupt")
    resumed = synthesizer.synthesize(chunks, runtime_dir=runtime_dir)
    assert resumed.completed
    assert resumed.manifest.chunks[first_chunk.chunk_id].attempts == 2

    class FailingProvider(MockTTSProvider):
        def synthesize(self, text, voice_config=None):
            if text.startswith("Two"):
                raise RuntimeError("provider unavailable")
            return super().synthesize(text, voice_config)

    failed_root = runtime_dir / "failed"
    failed = ResumableChunkSynthesizer(FailingProvider(), max_attempts=2).synthesize(chunks, runtime_dir=failed_root)
    assert not failed.completed
    assert failed.manifest.final_status == "failed"
    assert failed.manifest.final_artifact_ref is None
    assert not (failed_root / "voiceover.wav").exists()


def test_assembly_rejects_incompatible_wav_chunks():
    first = MockTTSProvider().synthesize("one").audio_bytes
    second = MockTTSProvider().synthesize("two").audio_bytes
    with wave.open(__import__("io").BytesIO(second), "rb") as reader:
        frames = reader.readframes(reader.getnframes())
    buffer = __import__("io").BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(frames)
    try:
        assemble_pcm_wav([first, buffer.getvalue()])
    except WavAssemblyError:
        pass
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("Expected incompatible WAV parameters to be rejected.")


class CountingMockTTSProvider(MockTTSProvider):
    """Deterministic mock provider that exposes only local synthesis calls."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def synthesize(self, text, voice_config=None):
        self.calls.append(text)
        return super().synthesize(text, voice_config)


def test_changed_narration_and_config_invalidate_only_affected_reuse(runtime_dir):
    provider = CountingMockTTSProvider()
    synthesizer = ResumableChunkSynthesizer(provider)
    original = chunk_narration("First. Second.", max_words=1)

    assert synthesizer.synthesize(original, runtime_dir=runtime_dir, voice_config={"voice": "a"}).completed
    assert provider.calls == [chunk.text for chunk in original]

    revised = chunk_narration("First. Updated.", max_words=1)
    assert synthesizer.synthesize(revised, runtime_dir=runtime_dir, voice_config={"voice": "a"}).completed
    assert provider.calls == [chunk.text for chunk in original] + ["Updated."]

    assert synthesizer.synthesize(revised, runtime_dir=runtime_dir, voice_config={"voice": "b"}).completed
    assert provider.calls == [chunk.text for chunk in original] + ["Updated."] + [chunk.text for chunk in revised]


def test_interrupted_run_reuses_completed_chunks_and_synthesizes_only_unfinished(runtime_dir):
    chunks = chunk_narration("One. Two.", max_words=1)

    class InterruptingProvider(CountingMockTTSProvider):
        def synthesize(self, text, voice_config=None):
            self.calls.append(text)
            if text == "Two.":
                raise RuntimeError("interrupted")
            return MockTTSProvider.synthesize(self, text, voice_config)

    interrupted_provider = InterruptingProvider()
    interrupted = ResumableChunkSynthesizer(interrupted_provider, max_attempts=1).synthesize(
        chunks, runtime_dir=runtime_dir
    )
    assert not interrupted.completed
    assert interrupted_provider.calls == ["One.", "Two."]
    assert interrupted.manifest.chunks[chunks[0].id].status == "completed"

    resumed_provider = CountingMockTTSProvider()
    resumed = ResumableChunkSynthesizer(resumed_provider, max_attempts=1).synthesize(chunks, runtime_dir=runtime_dir)
    assert resumed.completed
    assert resumed_provider.calls == ["Two."]
    assert resumed.manifest.chunks[chunks[0].id].attempts == 1
    assert resumed.manifest.chunks[chunks[1].id].attempts == 2


def test_transient_chunk_failure_retries_exactly_the_configured_attempts(runtime_dir):
    class TransientProvider(CountingMockTTSProvider):
        def synthesize(self, text, voice_config=None):
            self.calls.append(text)
            if len(self.calls) < 3:
                raise RuntimeError("temporary provider outage")
            return MockTTSProvider.synthesize(self, text, voice_config)

    provider = TransientProvider()
    chunk = chunk_narration("Retry this.", max_words=3)
    result = ResumableChunkSynthesizer(provider, max_attempts=3).synthesize(chunk, runtime_dir=runtime_dir)
    assert result.completed
    assert provider.calls == ["Retry this."] * 3
    assert result.manifest.chunks[chunk[0].id].attempts == 3


def test_persisted_incompatible_wav_is_rejected_without_final_assembly(runtime_dir):
    chunks = chunk_narration("One. Two.", max_words=1)
    initial_provider = CountingMockTTSProvider()
    first = ResumableChunkSynthesizer(initial_provider).synthesize(chunks, runtime_dir=runtime_dir)
    assert first.completed

    record = first.manifest.chunks[chunks[1].id]
    original = (runtime_dir / record.artifact_ref).read_bytes()
    with wave.open(BytesIO(original), "rb") as reader:
        frames = reader.readframes(reader.getnframes())
    incompatible = BytesIO()
    with wave.open(incompatible, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(frames)
    incompatible_payload = incompatible.getvalue()
    (runtime_dir / record.artifact_ref).write_bytes(incompatible_payload)
    record.wav_checksum = sha256(incompatible_payload).hexdigest()
    record.audio_parameters, _ = inspect_pcm_wav(incompatible_payload)
    record.duration_seconds = record.audio_parameters.duration_seconds
    first.manifest.save(runtime_dir / "synthesis-manifest.json")

    resumed_provider = CountingMockTTSProvider()
    resumed = ResumableChunkSynthesizer(resumed_provider).synthesize(chunks, runtime_dir=runtime_dir)
    assert not resumed.completed
    assert resumed_provider.calls == []
    assert resumed.manifest.chunks[chunks[1].id].status == "failed"
    assert "incompatible" in (resumed.manifest.chunks[chunks[1].id].error or "")
    assert resumed.manifest.final_status == "failed"
    assert resumed.manifest.final_artifact_ref is None

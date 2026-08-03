from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.providers.mock_tts import MockTTSProvider
from app.tts.assembly import WavAssemblyError, persist_pcm_wav_atomically
from app.tts.chunk_synthesis import ResumableChunkSynthesizer
from app.tts.chunking import chunk_narration
from app.tts.manifest import SynthesisManifest


@pytest.fixture
def runtime_dir():
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        yield Path(directory)


def test_new_run_clears_stale_final_before_a_chunk_failure(runtime_dir):
    chunks = chunk_narration("One. Two.", max_words=1)
    initial = ResumableChunkSynthesizer(MockTTSProvider()).synthesize(chunks, runtime_dir=runtime_dir)
    assert initial.completed
    assert (runtime_dir / "voiceover.wav").exists()

    class FailingProvider(MockTTSProvider):
        def synthesize(self, text, voice_config=None):
            raise RuntimeError("offline failure")

    failed = ResumableChunkSynthesizer(FailingProvider(), max_attempts=1).synthesize(
        chunks, runtime_dir=runtime_dir, voice_config={"variant": "retry"}
    )

    persisted = SynthesisManifest.load(runtime_dir / "synthesis-manifest.json", config_hash=failed.manifest.config_hash)
    assert not failed.completed
    assert persisted.final_status == "failed"
    assert persisted.final_artifact_ref is None
    assert persisted.final_checksum is None
    assert not (runtime_dir / "voiceover.wav").exists()


def test_stale_running_manifest_resumes_valid_completed_chunks(runtime_dir):
    chunks = chunk_narration("One. Two.", max_words=1)

    class InterruptingProvider(MockTTSProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[str] = []

        def synthesize(self, text, voice_config=None):
            self.calls.append(text)
            if text == "Two.":
                raise RuntimeError("interrupted")
            return super().synthesize(text, voice_config)

    interrupted_provider = InterruptingProvider()
    interrupted = ResumableChunkSynthesizer(interrupted_provider, max_attempts=1).synthesize(
        chunks, runtime_dir=runtime_dir
    )
    assert interrupted.manifest.final_status == "failed"

    class ResumingProvider(MockTTSProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[str] = []

        def synthesize(self, text, voice_config=None):
            self.calls.append(text)
            return super().synthesize(text, voice_config)

    resumed_provider = ResumingProvider()
    resumed = ResumableChunkSynthesizer(resumed_provider, max_attempts=1).synthesize(chunks, runtime_dir=runtime_dir)

    assert resumed.completed
    # The previously completed first chunk remains available even though the
    # old manifest is now treated as an interrupted run.
    assert resumed_provider.calls == ["Two."]


def test_final_atomic_publication_failure_clears_final_evidence_and_artifact(runtime_dir, monkeypatch):
    chunks = chunk_narration("One. Two.", max_words=1)
    original_replace = Path.replace

    def fail_final_publication(path: Path, target: Path) -> Path:
        if target == runtime_dir / "voiceover.wav" and path.name.startswith(".voiceover.wav."):
            raise OSError("simulated atomic publication failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_final_publication)

    result = ResumableChunkSynthesizer(MockTTSProvider()).synthesize(chunks, runtime_dir=runtime_dir)
    persisted = SynthesisManifest.load(runtime_dir / "synthesis-manifest.json", config_hash=result.manifest.config_hash)

    assert not result.completed
    assert result.final_wav is None
    assert persisted.final_status == "failed"
    assert persisted.final_artifact_ref is None
    assert persisted.final_checksum is None
    assert not (runtime_dir / "voiceover.wav").exists()


def test_corrupt_temporary_wav_is_rejected_before_atomic_publication(runtime_dir, monkeypatch):
    output_path = runtime_dir / "voiceover.wav"
    payload = MockTTSProvider().synthesize("One.").audio_bytes
    original_read_bytes = Path.read_bytes
    replacements: list[Path] = []
    original_replace = Path.replace

    def corrupt_temporary_read(path: Path) -> bytes:
        if path.name.startswith(".voiceover.wav.") and path.suffix == ".tmp":
            return b"not a WAV file"
        return original_read_bytes(path)

    def record_replace(path: Path, target: Path) -> Path:
        replacements.append(target)
        return original_replace(path, target)

    monkeypatch.setattr(Path, "read_bytes", corrupt_temporary_read)
    monkeypatch.setattr(Path, "replace", record_replace)

    with pytest.raises(WavAssemblyError, match="readable WAV"):
        persist_pcm_wav_atomically(payload, output_path)

    assert replacements == []
    assert not output_path.exists()


def test_interruption_before_completed_manifest_resumes_only_validated_chunks(runtime_dir, monkeypatch):
    chunks = chunk_narration("One. Two.", max_words=1)
    original_save = SynthesisManifest.save

    def interrupt_before_completed_manifest(manifest: SynthesisManifest, path: Path) -> None:
        if manifest.final_status == "completed":
            raise RuntimeError("simulated process interruption")
        original_save(manifest, path)

    with monkeypatch.context() as interrupted_process:
        interrupted_process.setattr(SynthesisManifest, "save", interrupt_before_completed_manifest)
        with pytest.raises(RuntimeError, match="process interruption"):
            ResumableChunkSynthesizer(MockTTSProvider()).synthesize(chunks, runtime_dir=runtime_dir)

    persisted = SynthesisManifest.load(
        runtime_dir / "synthesis-manifest.json",
        config_hash=ResumableChunkSynthesizer(MockTTSProvider())._config_hash({}),
    )
    assert persisted.final_status == "running"
    corrupt_record = persisted.chunks[chunks[1].id]
    (runtime_dir / corrupt_record.artifact_ref).write_bytes(b"corrupt")

    class ResumingProvider(MockTTSProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[str] = []

        def synthesize(self, text, voice_config=None):
            self.calls.append(text)
            return super().synthesize(text, voice_config)

    resumed_provider = ResumingProvider()
    resumed = ResumableChunkSynthesizer(resumed_provider).synthesize(chunks, runtime_dir=runtime_dir)

    assert resumed.completed
    assert resumed_provider.calls == ["Two."]
    assert resumed.manifest.chunks[chunks[0].id].attempts == 1
    assert resumed.manifest.chunks[chunks[1].id].attempts == 2


def test_post_replacement_verification_does_not_report_completion(runtime_dir, monkeypatch):
    chunks = chunk_narration("One. Two.", max_words=1)
    output_path = runtime_dir / "voiceover.wav"
    original_read_bytes = Path.read_bytes

    def corrupt_published_final(path: Path) -> bytes:
        if path == output_path:
            return b"corrupt published output"
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", corrupt_published_final)
    result = ResumableChunkSynthesizer(MockTTSProvider()).synthesize(chunks, runtime_dir=runtime_dir)
    persisted = SynthesisManifest.load(runtime_dir / "synthesis-manifest.json", config_hash=result.manifest.config_hash)

    assert not result.completed
    assert result.final_wav is None
    assert persisted.final_status == "failed"
    assert persisted.final_artifact_ref is None
    assert persisted.final_checksum is None
    assert not output_path.exists()

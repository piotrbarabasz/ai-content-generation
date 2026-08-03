from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from app.providers.mock_tts import MockTTSProvider
from app.tts.chunk_synthesis import ResumableChunkSynthesizer
from app.tts.chunking import chunk_narration
from app.tts.manifest import relative_reference


@pytest.fixture
def runtime_dir():
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        yield Path(directory)


class IdentityCountingProvider(MockTTSProvider):
    def __init__(self, *, identity: dict[str, object] | None = None) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.identity = identity or {"provider": "mock", "model": "mock", "voice": {"mode": "built-in"}}

    def synthesize(self, text, voice_config=None):
        self.calls.append(text)
        return super().synthesize(text, voice_config)

    def effective_synthesis_identity(self, voice_config=None):
        return self.identity


class NormalizingIdentityProvider(IdentityCountingProvider):
    """A provider whose raw aliases resolve to one synthesis request."""

    def effective_synthesis_identity(self, voice_config=None):
        config = dict(voice_config or {})
        return {
            "provider": "mock",
            "model": "mock",
            "voice": {"name": config.get("voice", "default").lower()},
        }


def test_full_hit_and_partial_text_change_reuse_only_matching_chunks(runtime_dir):
    provider = IdentityCountingProvider()
    synthesizer = ResumableChunkSynthesizer(provider)
    original = chunk_narration("First. Second.", max_words=1)

    assert synthesizer.synthesize(original, runtime_dir=runtime_dir).completed
    assert synthesizer.synthesize(original, runtime_dir=runtime_dir).completed
    assert provider.calls == [chunk.text for chunk in original]

    revised = chunk_narration("First. Changed.", max_words=1)
    assert synthesizer.synthesize(revised, runtime_dir=runtime_dir).completed
    assert provider.calls == [chunk.text for chunk in original] + ["Changed."]


def test_shortened_narration_prunes_stale_records_and_only_safe_orphan_wavs(runtime_dir):
    provider = IdentityCountingProvider()
    synthesizer = ResumableChunkSynthesizer(provider)
    original = chunk_narration("One. Two. Three.", max_words=1)
    assert synthesizer.synthesize(original, runtime_dir=runtime_dir).completed

    chunks_dir = runtime_dir / "chunks"
    safe_orphan = chunks_dir / "orphan.wav"
    safe_orphan.write_bytes(b"orphan")
    outside = runtime_dir / "outside.wav"
    outside.write_bytes(b"must remain")

    shortened = original[:1]
    result = synthesizer.synthesize(shortened, runtime_dir=runtime_dir)

    assert result.completed
    assert result.manifest.chunk_count == 1
    assert result.manifest.to_payload()["chunk_count"] == 1
    assert set(result.manifest.chunks) == {shortened[0].id}
    assert not safe_orphan.exists()
    assert outside.read_bytes() == b"must remain"
    with pytest.raises(ValueError):
        relative_reference(outside, chunks_dir)
    saved = json.loads((runtime_dir / "synthesis-manifest.json").read_text(encoding="utf-8"))
    assert saved["chunk_count"] == 1


def test_effective_identity_changes_invalidate_cache_without_persisting_absolute_paths(runtime_dir):
    reference = runtime_dir / "private-reference.wav"
    reference.write_bytes(b"reference-v1")
    provider = IdentityCountingProvider(
        identity={
            "provider": "mock",
            "model": "mock-v1",
            "voice": {"mode": "reference", "checksum": "checksum-v1"},
        }
    )
    chunks = chunk_narration("Identity check.", max_words=2)
    synthesizer = ResumableChunkSynthesizer(provider)

    assert synthesizer.synthesize(chunks, runtime_dir=runtime_dir, voice_config={"reference_path": str(reference)}).completed
    provider.identity["voice"] = {"mode": "reference", "checksum": "checksum-v2"}
    assert synthesizer.synthesize(chunks, runtime_dir=runtime_dir, voice_config={"reference_path": str(reference)}).completed

    assert provider.calls == [chunks[0].text, chunks[0].text]
    manifest_payload = (runtime_dir / "synthesis-manifest.json").read_text(encoding="utf-8")
    assert str(reference.resolve()) not in manifest_payload


def test_equivalent_raw_voice_configs_reuse_cache_by_effective_identity(runtime_dir):
    provider = NormalizingIdentityProvider()
    synthesizer = ResumableChunkSynthesizer(provider)
    chunks = chunk_narration("Equivalent identity.", max_words=2)

    assert synthesizer.synthesize(
        chunks,
        runtime_dir=runtime_dir,
        voice_config={"voice": "NOVA", "ui_label": "Warm narration"},
    ).completed
    assert synthesizer.synthesize(
        chunks,
        runtime_dir=runtime_dir,
        voice_config={"voice": "nova", "ui_label": "Narration default"},
    ).completed

    assert provider.calls == [chunks[0].text]

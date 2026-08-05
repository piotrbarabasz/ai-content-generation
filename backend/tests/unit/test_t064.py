from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from app.modules.voiceover import VoiceoverModule
from app.providers.mock_tts import MockTTSProvider
from app.storage.local_store import LocalArtifactStore
from app.tts.assembly import inspect_pcm_wav
from app.tts.chunk_synthesis import ResumableChunkSynthesizer
from app.tts.chunking import NarrationChunkingSettings, chunk_narration
from app.workflow.execution import ModuleExecutionContext


_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "narrations" / "story_04_15min.txt"
_CHUNKING = {"max_words": 400, "max_attempts": 1}


class DeterministicFixtureProvider(MockTTSProvider):
    """Offline provider with an explicit, content-based effective identity."""

    def __init__(
        self,
        *,
        provider_name: str = "fixture-tts-v1",
        voice_mode: str = "reference",
        model_key: str | None = None,
        length_scale: float | None = None,
        reference_checksum: str = "reference-a",
        fail_after_successes: int | None = None,
    ) -> None:
        super().__init__(provider_name)
        self.voice_mode = voice_mode
        self.model_key = model_key
        self.length_scale = length_scale
        self.reference_checksum = reference_checksum
        self.fail_after_successes = fail_after_successes
        self.calls: list[str] = []
        self._result = super().synthesize("deterministic offline fixture", {"language": "pl"})

    def effective_synthesis_identity(self, voice_config=None):
        config = dict(voice_config or {})
        voice: dict[str, object] = {
            "mode": str(config.get("voice_mode", self.voice_mode)),
        }
        model_key = config.get("model_key", self.model_key)
        if model_key is not None:
            voice["model"] = {
                "kind": "catalog_voice",
                "provider_key": str(model_key),
            }
        reference_checksum = config.get("reference_checksum", self.reference_checksum)
        if reference_checksum is not None:
            voice["content_checksum"] = str(reference_checksum)

        generation_settings: dict[str, object] = {}
        length_scale = config.get("length_scale", self.length_scale)
        if length_scale is not None:
            generation_settings["length_scale"] = length_scale

        return {
            "provider": self.provider_name,
            "model_variant": "deterministic-fixture",
            "device": "cpu",
            "language_id": "pl",
            "generation_settings": generation_settings,
            "voice": voice,
        }

    def synthesize(self, text, voice_config=None):
        self.calls.append(text)
        if self.fail_after_successes is not None and len(self.calls) > self.fail_after_successes:
            raise RuntimeError("controlled offline interruption")
        return self._result


def _context(text: str, *, workflow_run_id: str = "t064-15-minute-run") -> ModuleExecutionContext:
    return ModuleExecutionContext(
        workflow_run_id=workflow_run_id,
        workflow_config_id="t064-config",
        module_name="voiceover",
        inputs={"text": text, "resumable_chunking": _CHUNKING},
    )


def _module(root: Path, provider: DeterministicFixtureProvider) -> VoiceoverModule:
    return VoiceoverModule(
        tts_provider=provider,
        artifact_store=LocalArtifactStore(root / "artifacts"),
        resumable_runtime_dir=root / "runtime",
    )


def test_fifteen_minute_voiceover_resume_reuses_valid_chunks_and_publishes_consistent_pcm() -> None:
    text = _FIXTURE.read_text(encoding="utf-8")
    expected_chunks = chunk_narration(
        " ".join(text.split()), NarrationChunkingSettings(max_words=_CHUNKING["max_words"])
    )

    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        root = Path(directory)
        interrupted_provider = DeterministicFixtureProvider(fail_after_successes=2)
        with pytest.raises(RuntimeError, match="chunked synthesis failed"):
            _module(root, interrupted_provider).execute(_context(text))

        runtime = root / "runtime" / "t064-15-minute-run"
        interrupted_manifest = json.loads((runtime / "synthesis-manifest.json").read_text(encoding="utf-8"))
        assert interrupted_manifest["final_status"] == "failed"
        assert interrupted_manifest["final_artifact_ref"] is None
        assert not (runtime / "voiceover.wav").exists()
        assert sum(item["status"] == "completed" for item in interrupted_manifest["chunks"]) == 2

        resumed_provider = DeterministicFixtureProvider()
        resumed = _module(root, resumed_provider).execute(_context(text))
        manifest = json.loads((runtime / "synthesis-manifest.json").read_text(encoding="utf-8"))
        benchmark = json.loads(
            (root / "artifacts" / resumed.output["benchmark_artifact"]["storage_key"]).read_text(
                encoding="utf-8"
            )
        )
        final_bytes = (root / "artifacts" / resumed.output["artifact"]["storage_key"]).read_bytes()
        parameters, _ = inspect_pcm_wav(final_bytes)

        assert len(resumed_provider.calls) == len(expected_chunks) - 2
        assert resumed_provider.calls == [chunk.text for chunk in expected_chunks[2:]]
        assert parameters.channels == 1
        assert parameters.sample_width == 2
        assert parameters.compression_type == "NONE"
        assert parameters.frame_count == sum(
            item["audio_parameters"]["frame_count"] for item in manifest["chunks"]
        )
        assert [item["chunk_id"] for item in manifest["chunks"]] == [chunk.id for chunk in expected_chunks]
        assert [item["index"] for item in manifest["chunks"]] == list(range(len(expected_chunks)))
        assert manifest["final_status"] == "completed"
        assert manifest["generated_chunk_count"] == len(expected_chunks) - 2
        assert manifest["reused_chunk_count"] == 2
        assert manifest["failed_chunk_count"] == 0
        assert benchmark["generated_chunk_count"] == len(expected_chunks) - 2
        assert benchmark["reused_chunk_count"] == 2
        assert benchmark["failed_chunk_count"] == 0


def test_fifteen_minute_resume_invalidates_provider_and_reference_content_identity() -> None:
    text = _FIXTURE.read_text(encoding="utf-8")
    expected_count = len(
        chunk_narration(" ".join(text.split()), NarrationChunkingSettings(max_words=_CHUNKING["max_words"]))
    )

    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        root = Path(directory)
        first = DeterministicFixtureProvider(provider_name="fixture-tts-v1", reference_checksum="reference-a")
        _module(root, first).execute(_context(text, workflow_run_id="t064-identity-run"))

        changed_provider = DeterministicFixtureProvider(provider_name="fixture-tts-v2", reference_checksum="reference-a")
        _module(root, changed_provider).execute(_context(text, workflow_run_id="t064-identity-run"))
        assert len(changed_provider.calls) == expected_count

        changed_reference = DeterministicFixtureProvider(provider_name="fixture-tts-v2", reference_checksum="reference-b")
        _module(root, changed_reference).execute(_context(text, workflow_run_id="t064-identity-run"))
        assert len(changed_reference.calls) == expected_count


def test_fifteen_minute_resume_invalidates_model_and_speaking_rate_identity_changes() -> None:
    text = _FIXTURE.read_text(encoding="utf-8")
    chunks = chunk_narration(
        " ".join(text.split()), NarrationChunkingSettings(max_words=_CHUNKING["max_words"])
    )

    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        runtime_dir = Path(directory)
        provider = DeterministicFixtureProvider(
            provider_name="fixture-tts-v1",
            voice_mode="catalog",
            model_key="pl_PL-gosia-medium",
            length_scale=1.0,
            reference_checksum="reference-a",
        )
        synthesizer = ResumableChunkSynthesizer(provider)
        voice_config = {
            "voice_mode": "catalog",
            "model_key": "pl_PL-gosia-medium",
            "length_scale": 1.0,
            "reference_checksum": "reference-a",
        }

        first = synthesizer.synthesize(chunks, runtime_dir=runtime_dir, voice_config=voice_config)
        second = synthesizer.synthesize(chunks, runtime_dir=runtime_dir, voice_config=voice_config)
        changed_model = synthesizer.synthesize(
            chunks,
            runtime_dir=runtime_dir,
            voice_config={**voice_config, "model_key": "pl_PL-darkman-medium"},
        )
        changed_rate = synthesizer.synthesize(
            chunks,
            runtime_dir=runtime_dir,
            voice_config={**voice_config, "length_scale": 1.25},
        )
        changed_reference = synthesizer.synthesize(
            chunks,
            runtime_dir=runtime_dir,
            voice_config={**voice_config, "reference_checksum": "reference-b"},
        )

        assert first.completed is True
        assert second.completed is True
        assert changed_model.completed is True
        assert changed_rate.completed is True
        assert changed_reference.completed is True
        assert first.manifest.generated_chunk_count == len(chunks)
        assert second.manifest.reused_chunk_count == len(chunks)
        assert changed_model.manifest.generated_chunk_count == len(chunks)
        assert changed_rate.manifest.generated_chunk_count == len(chunks)
        assert changed_reference.manifest.generated_chunk_count == len(chunks)

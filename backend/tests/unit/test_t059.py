from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.modules.voiceover import VoiceoverModule
from app.providers.mock_tts import MockTTSProvider
from app.storage.local_store import LocalArtifactStore
from app.workflow.execution import ModuleExecutionContext


class CountingProvider(MockTTSProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def synthesize(self, text, voice_config=None):
        self.calls.append(text)
        return super().synthesize(text, voice_config)


def _context(text: str) -> ModuleExecutionContext:
    return ModuleExecutionContext(
        workflow_run_id="resumable-run",
        workflow_config_id="config",
        module_name="voiceover",
        inputs={"text": text, "resumable_chunking": {"max_words": 2}},
    )


def test_voiceover_module_persists_resumable_artifacts_and_reuses_chunks() -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        root = Path(directory)
        provider = CountingProvider()
        store = LocalArtifactStore(root / "artifacts")
        module = VoiceoverModule(
            tts_provider=provider,
            artifact_store=store,
            resumable_runtime_dir=root / "runtime",
        )
        context = _context("One two. Three four.")

        first = module.execute(context)
        first_calls = list(provider.calls)
        second = module.execute(context)

        assert first.output["voiceover"]["chunk_count"] == 2
        assert provider.calls == first_calls
        assert second.output["voiceover"]["audio_storage_key"].endswith("voiceover.wav")
        assert {artifact.name for artifact in store.list_artifacts()} == {
            "voiceover.wav", "speech_timeline.json", "synthesis-manifest.json", "tts-benchmark.json"
        }
        manifest = json.loads(store.read_artifact(first.output["synthesis_manifest_artifact"]["storage_key"]))
        benchmark = json.loads(store.read_artifact(first.output["benchmark_artifact"]["storage_key"]))
        assert manifest["final_status"] == "completed"
        assert benchmark["chunk_count"] == 2


def test_voiceover_module_keeps_short_direct_synthesis_provider_neutral() -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        provider = CountingProvider()
        module = VoiceoverModule(tts_provider=provider, artifact_store=LocalArtifactStore(directory))

        result = module.execute(
            ModuleExecutionContext("run", "config", "voiceover", inputs={"text": "One minute narration."})
        )

        assert result.output["voiceover"]["chunk_count"] == 1
        assert provider.calls == ["One minute narration."]
        assert "benchmark_artifact" not in result.output


class SelectionProvider(MockTTSProvider):
    def __init__(self, provider_name: str) -> None:
        super().__init__(provider_name)
        self.calls: list[str] = []

    def synthesize(self, text, voice_config=None):
        self.calls.append(text)
        return super().synthesize(text, voice_config)


def test_voiceover_cache_invalidates_when_provider_selection_changes() -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        root = Path(directory)
        store = LocalArtifactStore(root / "artifacts")
        first_provider = SelectionProvider("selection-a")
        second_provider = SelectionProvider("selection-b")
        context = _context("One two. Three four.")

        VoiceoverModule(
            tts_provider=first_provider,
            artifact_store=store,
            resumable_runtime_dir=root / "runtime",
        ).execute(context)
        VoiceoverModule(
            tts_provider=second_provider,
            artifact_store=store,
            resumable_runtime_dir=root / "runtime",
        ).execute(context)

        assert first_provider.calls == ["One two.", "Three four."]
        assert second_provider.calls == ["One two.", "Three four."]
        manifest = json.loads(
            (root / "runtime" / "resumable-run" / "synthesis-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["effective_synthesis_identity"]["provider"] == "selection-b"
        assert manifest["generated_chunk_count"] == 2
        assert manifest["reused_chunk_count"] == 0

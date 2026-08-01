from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import io
import shutil
from pathlib import Path
import wave

from app.domain.content_brief import ContentBrief
from app.domain.enums import ContentGenre, ContentType, DurationProfile, TargetPlatform, WorkflowPreset
from app.providers.mocks import build_mock_provider_registry
from app.providers.mock_tts import MockTTSProvider
from app.storage.local_store import LocalArtifactStore
from app.workflow.engine import CoreWorkflowEngine
from app.workflow.execution import ModuleExecutionContext, ModuleResult
from app.workflow.module import ModuleDefinition
from app.workflow.registry import ModuleRegistry
from app.domain.workflow_config import WorkflowConfig
from app.modules.voiceover import VoiceoverModule


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


def _assert_valid_wav(audio_bytes: bytes) -> tuple[int, int, int]:
    with wave.open(io.BytesIO(audio_bytes), "rb") as reader:
        return reader.getnchannels(), reader.getsampwidth(), reader.getframerate()


def test_voiceover_module_definition_matches_contract() -> None:
    with _workspace_tempdir("test_t012_definition") as store_root:
        module = VoiceoverModule(
            tts_provider=MockTTSProvider(),
            artifact_store=LocalArtifactStore(store_root),
        )

        assert module.definition.name == "voiceover"
        assert module.definition.dependencies == (("brief",),)
        assert module.definition.enabled_by_default is False
        assert module.definition.disabled_behavior == "skip"
        assert module.definition.retry_limit == 2
        assert module.definition.artifact_outputs == ("voiceover.wav", "speech_timeline.json")
        assert module.definition.config_schema["properties"]["default_voice"]["type"] == "string"


def test_voiceover_module_generates_artifact_reference_and_timeline() -> None:
    with _workspace_tempdir("test_t012_voiceover") as store_root:
        store = LocalArtifactStore(store_root)
        tts_provider = MockTTSProvider("mock")
        module = VoiceoverModule(tts_provider=tts_provider, artifact_store=store)
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
            workflow_run_id="workflow_run_1",
            workflow_config_id="workflow_config_1",
            module_name="voiceover",
            inputs={
                "content_brief": brief,
                "voice_config": {"voice": "narrator"},
            },
        )

        result = module.execute(context)
        voiceover = result.output["voiceover"]
        artifact = result.output["artifact"]
        speech_timeline = result.output["speech_timeline"]
        speech_artifact = result.output["speech_timeline_artifact"]
        stored_manifests = store.list_artifacts()
        expected_synthesis = tts_provider.synthesize(
            "Create a polished spoken teaser",
            {"voice": "narrator", "language": "en", "tone": "neutral", "source_kind": "brief"},
        )

        assert result.status == "completed"
        assert result.output_artifact_ids == ("voiceover.wav", "speech_timeline.json")
        assert voiceover["source_kind"] == "brief"
        assert voiceover["text"] == "Create a polished spoken teaser"
        assert voiceover["provider"] == "mock"
        assert voiceover["audio_ref"] == expected_synthesis.metadata["source_ref"]
        assert voiceover["source_ref"] == expected_synthesis.metadata["source_ref"]
        assert voiceover["audio_format"] == "wav"
        assert voiceover["sample_rate"] == expected_synthesis.sample_rate
        assert voiceover["audio_storage_key"].endswith("voiceover.wav")
        assert artifact["name"] == "voiceover.wav"
        assert artifact["artifact_type"] == "voiceover"
        assert artifact["metadata"]["provider"] == "mock"
        assert artifact["metadata"]["source_ref"] == expected_synthesis.metadata["source_ref"]
        assert artifact["metadata"]["sample_rate"] == expected_synthesis.sample_rate
        assert artifact["metadata"]["audio_format"] == "wav"
        assert speech_timeline["voiceover_storage_key"] == voiceover["audio_storage_key"]
        assert speech_timeline["word_timings"][0]["word"] == "Create"
        assert speech_timeline["provider"] == "mock"
        assert speech_timeline["source_ref"] == expected_synthesis.metadata["source_ref"]
        assert speech_artifact["name"] == "speech_timeline.json"
        assert {manifest.name for manifest in stored_manifests} == {"voiceover.wav", "speech_timeline.json"}
        stored_audio = store.read_artifact(voiceover["audio_storage_key"])
        assert stored_audio == expected_synthesis.audio_bytes
        assert stored_audio.startswith(b"RIFF")
        assert _assert_valid_wav(stored_audio) == (1, 2, expected_synthesis.sample_rate)


@dataclass(slots=True)
class RecordingModule:
    definition: ModuleDefinition
    order: list[str]

    def execute(self, context) -> ModuleResult:
        self.order.append(context.module_name)
        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=(f"{self.definition.name}.json",),
            output={"module": self.definition.name},
        )


def _definition(name: str, *, disabled_behavior: str = "fail") -> ModuleDefinition:
    return ModuleDefinition(
        name=name,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        config_schema={"type": "object"},
        disabled_behavior=disabled_behavior,
        artifact_outputs=(f"{name}.json",),
    )


def test_disabled_voiceover_module_does_not_block_export() -> None:
    with _workspace_tempdir("test_t012_disabled") as _store_root:
        order: list[str] = []
        registry = ModuleRegistry(
            [
                _definition("brief"),
                _definition("voiceover", disabled_behavior="skip"),
                _definition("export"),
            ]
        )
        modules = {
            "brief": RecordingModule(definition=_definition("brief"), order=order),
            "export": RecordingModule(definition=_definition("export"), order=order),
        }
        plan = registry.build_execution_plan(
            ("brief", "voiceover", "export"),
            disabled_modules=("voiceover",),
        )
        engine = CoreWorkflowEngine(registry, modules)
        workflow_config = WorkflowConfig.create(
            project_id="project_1",
            workflow_preset=WorkflowPreset.SHORT_VIDEO,
            content_type=ContentType.SHORT_VIDEO,
            content_genre=ContentGenre.NEWS,
            duration_profile=DurationProfile.SIXTY_SECONDS,
            target_platform=TargetPlatform.YOUTUBE_SHORTS,
            language="en",
            tone="dynamic",
            enabled_modules=["brief", "export"],
            disabled_modules=["voiceover"],
            provider_config={
                "LLMProvider": {"providerName": "mock", "enabled": True},
                "StorageProvider": {"providerName": "mock", "enabled": True},
            },
        )

        result = engine.run(
            plan,
            workflow_run_id="workflow_run_2",
            workflow_config_id=workflow_config.id,
            workflow_config=workflow_config,
            provider_registry=build_mock_provider_registry(),
        )

        assert order == ["brief", "export"]
        assert result.status == "completed"
        assert result.module_results["voiceover"].status == "skipped"
        assert result.artifact_ids == ("brief.json", "export.json")

from __future__ import annotations

import io
import json
import shutil
from contextlib import contextmanager
from pathlib import Path
import wave

from app.domain.enums import ProviderType
from app.domain.enums import ContentGenre, ContentType, DurationProfile, TargetPlatform, WorkflowPreset
from app.domain.workflow_config import WorkflowConfig
from app.modules.brief import BriefModule
from app.modules.dossier import DossierModule
from app.modules.export import ExportModule
from app.modules.outline import OutlineModule
from app.modules.post_processing import PostProcessingModule
from app.modules.qa import QAModule
from app.modules.research import ResearchModule
from app.modules.script_generation import ScriptGenerationModule
from app.modules.voiceover import VoiceoverModule
from app.providers.mock_llm import MockLLMProvider
from app.providers.mock_tts import MockTTSProvider
from app.providers.mocks import build_mock_provider_registry
from app.providers.tts_capabilities import TTSCapabilities
from app.providers.tts_result import TTSSynthesisResult
from app.storage.local_store import LocalArtifactStore
from app.workflow.engine import CoreWorkflowEngine
from app.workflow.presets import LONG_FORM_SCRIPT_VOICEOVER_PRESET
from app.workflow.registry import ModuleRegistry


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


def _wav_bytes(*, sample_rate: int, duration_seconds: float = 0.5) -> bytes:
    buffer = io.BytesIO()
    frame_count = max(int(sample_rate * duration_seconds), 1)
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\0\0" * frame_count)
    return buffer.getvalue()


class ProfiledWorkflowTTSProvider:
    provider_type = ProviderType.TTS

    def __init__(
        self,
        provider_name: str,
        *,
        sample_rate: int,
        model_variant: str,
        voice_mode: str,
        usage_policy: str,
        generation_settings: dict[str, object] | None = None,
        voice_identity: dict[str, object] | None = None,
        reference_audio_required: bool = False,
    ) -> None:
        self.provider_name = provider_name
        self._sample_rate = sample_rate
        self._model_variant = model_variant
        self._voice_mode = voice_mode
        self._usage_policy = usage_policy
        self._generation_settings = dict(generation_settings or {})
        self._voice_identity = dict(voice_identity or {})
        self._reference_audio_required = reference_audio_required

    def capabilities(self) -> TTSCapabilities:
        return TTSCapabilities(
            provider_name=self.provider_name,
            supported_languages=("pl",),
            voice_modes=(self._voice_mode,),
            reference_audio_required=self._reference_audio_required,
            speaking_rate_supported="length_scale" in self._generation_settings,
            usage_policy=self._usage_policy,
        )

    def effective_synthesis_identity(self, voice_config=None):
        config = dict(voice_config or {})
        generation_settings = dict(self._generation_settings)
        if "length_scale" in config and config["length_scale"] is not None:
            generation_settings["length_scale"] = config["length_scale"]
        voice = {"mode": str(config.get("voice_mode", self._voice_mode))}
        voice.update(self._voice_identity)
        return {
            "provider": self.provider_name,
            "model_variant": self._model_variant,
            "device": "cpu",
            "language_id": str(config.get("language_id", "pl")),
            "generation_settings": generation_settings,
            "voice": voice,
        }

    def synthesize(self, text, voice_config=None):
        return TTSSynthesisResult(
            audio_bytes=_wav_bytes(sample_rate=self._sample_rate),
            sample_rate=self._sample_rate,
            duration_seconds=0.5,
            audio_format="wav",
            provider_name=self.provider_name,
            metadata={},
        )


def _build_modules(store: LocalArtifactStore, *, tts_provider: object | None = None) -> dict[str, object]:
    llm_provider = MockLLMProvider("mock")
    tts_provider = tts_provider or MockTTSProvider("mock")
    return {
        "brief": BriefModule(),
        "research": ResearchModule(llm_provider=llm_provider, artifact_store=store),
        "dossier": DossierModule(artifact_store=store),
        "outline": OutlineModule(artifact_store=store),
        "scriptGeneration": ScriptGenerationModule(
            llm_provider=llm_provider,
            artifact_store=store,
        ),
        "postProcessing": PostProcessingModule(artifact_store=store),
        "qa": QAModule(llm_provider=llm_provider, artifact_store=store),
        "voiceover": VoiceoverModule(tts_provider=tts_provider, artifact_store=store),
        "export": ExportModule(artifact_store=store),
    }


def _workflow_config_payload(
    *,
    project_id: str,
    disabled_modules: tuple[str, ...],
    provider_config: dict[str, object],
) -> dict[str, object]:
    payload = LONG_FORM_SCRIPT_VOICEOVER_PRESET.build_workflow_config_payload(project_id=project_id)
    payload["enabledModules"] = [
        module_name
        for module_name in LONG_FORM_SCRIPT_VOICEOVER_PRESET.module_sequence
        if module_name not in disabled_modules
    ]
    payload["disabledModules"] = list(disabled_modules)
    payload["providerConfig"] = provider_config
    return payload


def _run_long_form_workflow(
    store_root: Path,
    *,
    project_id: str,
    workflow_run_id: str,
    topic: str,
    disabled_modules: tuple[str, ...],
    resumable_chunking: dict[str, object] | None = None,
    tts_provider: object | None = None,
) -> tuple[object, LocalArtifactStore, dict[str, object]]:
    store = LocalArtifactStore(store_root)
    modules = _build_modules(store, tts_provider=tts_provider)
    registry = ModuleRegistry(module.definition for module in modules.values())
    plan = registry.build_execution_plan(
        LONG_FORM_SCRIPT_VOICEOVER_PRESET.module_sequence,
        disabled_modules=disabled_modules,
    )

    workflow_config_payload = _workflow_config_payload(
        project_id=project_id,
        disabled_modules=disabled_modules,
        provider_config={
            "LLMProvider": {"providerName": "mock", "enabled": True},
            "StorageProvider": {"providerName": "mock", "enabled": True},
            **(
                {"TTSProvider": {"providerName": "mock", "enabled": True}}
                if "voiceover" not in disabled_modules
                else {}
            ),
        },
    )
    workflow_config = WorkflowConfig.from_payload(workflow_config_payload)
    engine = CoreWorkflowEngine(registry, modules)

    result = engine.run(
        plan,
        workflow_run_id=workflow_run_id,
        workflow_config_id=workflow_config.id,
        workflow_config=workflow_config,
        provider_registry=build_mock_provider_registry(),
        inputs={
            "project_id": project_id,
            "workflow_preset": WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER.value,
            "content_type": ContentType.LONG_FORM_VIDEO.value,
            "content_genre": ContentGenre.DOCUMENTARY.value,
            "duration_profile": DurationProfile.EIGHT_FIFTEEN_MINUTES.value,
            "target_platform": TargetPlatform.YOUTUBE.value,
            "topic": topic,
            "objective": "Create a detailed long-form launch narrative.",
            "audience": "Early adopters",
            "constraints": ["Keep the pacing focused."],
            "success_criteria": ["Produce a complete export bundle."],
            "workflow_config": workflow_config_payload,
            "workflow_run": {
                "id": workflow_run_id,
                "workflow_config_id": workflow_config.id,
                "status": "running",
                "current_stage": "export",
            },
            **({"resumable_chunking": resumable_chunking} if resumable_chunking is not None else {}),
        },
    )

    return result, store, workflow_config_payload


def test_long_form_workflow_executes_from_topic_with_research_enabled() -> None:
    with _workspace_tempdir("test_long_form_workflow_research_enabled") as store_root:
        result, store, workflow_config_payload = _run_long_form_workflow(
            store_root,
            project_id="project_1",
            workflow_run_id="workflow_run_1",
            topic="Launch teaser",
            disabled_modules=("voiceover",),
        )

        manifest_names = {manifest.name for manifest in store.list_artifacts()}
        export_manifest = result.module_results["export"].output["manifest"]
        qa_report = result.module_results["qa"].output["qa_report"]

        assert result.status == "completed"
        assert result.completed_modules == (
            "brief",
            "research",
            "dossier",
            "outline",
            "scriptGeneration",
            "postProcessing",
            "qa",
            "export",
        )
        assert result.module_results["brief"].output["source_kind"] == "topic"
        assert result.module_results["research"].status == "completed"
        assert result.module_results["research"].output["source_kind"] == "topic"
        assert result.module_results["dossier"].status == "completed"
        assert result.module_results["dossier"].output["source_kind"] == "topic"
        assert result.module_results["outline"].output["source_kind"] == "dossier"
        assert result.module_results["scriptGeneration"].output["source_kind"] == "outline"
        assert result.module_results["postProcessing"].output["source_kind"] == "outline"
        assert result.module_results["qa"].status == "completed"
        assert qa_report["next_stage"] == "export"
        assert result.module_results["voiceover"].status == "skipped"
        assert result.module_results["voiceover"].skipped_reason == "disabled"
        assert export_manifest["missingOptionalArtifacts"] == [
            "voiceover.wav",
            "speech_timeline.json",
        ]
        assert "voiceover.wav" not in export_manifest["artifactReferences"]
        assert manifest_names == {
            "research.json",
            "dossier.json",
            "outline.json",
            "script.txt",
            "script.json",
            "narrative_segments.json",
            "post_processed_script.txt",
            "qa_report.json",
            "manifest.json",
        }
        assert workflow_config_payload["disabledModules"] == ["voiceover"]


def test_long_form_workflow_executes_without_research_or_voiceover() -> None:
    with _workspace_tempdir("test_long_form_workflow_research_disabled") as store_root:
        result, store, workflow_config_payload = _run_long_form_workflow(
            store_root,
            project_id="project_2",
            workflow_run_id="workflow_run_2",
            topic="Launch teaser",
            disabled_modules=("research", "dossier", "voiceover"),
        )

        manifest_names = {manifest.name for manifest in store.list_artifacts()}
        export_manifest = result.module_results["export"].output["manifest"]
        qa_report = result.module_results["qa"].output["qa_report"]

        assert result.status == "completed"
        assert result.completed_modules == (
            "brief",
            "outline",
            "scriptGeneration",
            "postProcessing",
            "qa",
            "export",
        )
        assert result.module_results["brief"].output["source_kind"] == "topic"
        assert result.module_results["research"].status == "skipped"
        assert result.module_results["dossier"].status == "skipped"
        assert result.module_results["outline"].output["source_kind"] == "dossier"
        assert result.module_results["scriptGeneration"].output["source_kind"] == "outline"
        assert result.module_results["postProcessing"].output["source_kind"] == "outline"
        assert result.module_results["qa"].status == "completed"
        assert qa_report["next_stage"] == "export"
        assert result.module_results["voiceover"].status == "skipped"
        assert result.module_results["voiceover"].skipped_reason == "disabled"
        assert export_manifest["missingOptionalArtifacts"] == [
            "research.json",
            "dossier.json",
            "voiceover.wav",
            "speech_timeline.json",
        ]
        assert "research.json" not in export_manifest["artifactReferences"]
        assert "dossier.json" not in export_manifest["artifactReferences"]
        assert "voiceover.wav" not in export_manifest["artifactReferences"]
        assert manifest_names == {
            "outline.json",
            "script.txt",
            "script.json",
            "narrative_segments.json",
            "post_processed_script.txt",
            "qa_report.json",
            "manifest.json",
        }
        assert workflow_config_payload["disabledModules"] == ["research", "dossier", "voiceover"]


def test_long_form_workflow_exports_chunked_voiceover_evidence() -> None:
    with _workspace_tempdir("test_long_form_workflow_chunked_voiceover") as store_root:
        result, store, _ = _run_long_form_workflow(
            store_root,
            project_id="project_chunked_voiceover",
            workflow_run_id="workflow_run_chunked_voiceover",
            topic="Launch teaser",
            disabled_modules=(),
            resumable_chunking={"max_words": 5, "max_attempts": 1},
        )

        export_manifest = result.module_results["export"].output["manifest"]
        voiceover_result = result.module_results["voiceover"]
        assert result.status == "completed"
        assert voiceover_result.status == "completed"
        assert {"voiceover.wav", "speech_timeline.json", "synthesis-manifest.json", "tts-benchmark.json"} <= set(
            export_manifest["artifactReferences"]
        )
        assert {"voiceover.wav", "speech_timeline.json", "synthesis-manifest.json", "tts-benchmark.json"} <= {
            artifact.name for artifact in store.list_artifacts()
        }


def test_long_form_workflow_preserves_voiceover_contracts_across_fake_provider_profiles() -> None:
    with _workspace_tempdir("test_long_form_workflow_multi_provider_profiles") as store_root:
        reference_audio = store_root / "approved-reference.wav"
        reference_audio.write_bytes(_wav_bytes(sample_rate=16_000))

        profiles = (
            (
                "chatterbox_v3",
                ProfiledWorkflowTTSProvider(
                    "chatterbox_v3",
                    sample_rate=24_000,
                    model_variant="v3",
                    voice_mode="builtin",
                    usage_policy="production",
                    generation_settings={"exaggeration": 0.2},
                ),
                {"language_id": "pl", "exaggeration": 0.2},
                {"mode": "builtin"},
            ),
            (
                "piper",
                ProfiledWorkflowTTSProvider(
                    "piper",
                    sample_rate=22_050,
                    model_variant="v3",
                    voice_mode="catalog",
                    usage_policy="production",
                    generation_settings={"length_scale": 1.15},
                    voice_identity={
                        "model": {
                            "kind": "catalog_voice",
                            "provider_key": "pl_PL-gosia-medium",
                            "model_path": "C:\\private\\voices\\pl_PL-gosia-medium.onnx",
                        }
                    },
                ),
                {"language_id": "pl", "length_scale": 1.15},
                {
                    "mode": "catalog",
                    "model": {
                        "kind": "catalog_voice",
                        "provider_key": "pl_PL-gosia-medium",
                    },
                },
            ),
            (
                "xtts_v2_eval",
                ProfiledWorkflowTTSProvider(
                    "xtts_v2_eval",
                    sample_rate=16_000,
                    model_variant="xtts_v2",
                    voice_mode="reference",
                    usage_policy="evaluation_only",
                    voice_identity={
                        "reference_path": "C:\\private\\references\\approved.wav",
                        "content_checksum": "approved-checksum",
                    },
                    reference_audio_required=True,
                ),
                {
                    "language_id": "pl",
                    "reference_audio_path": str(reference_audio),
                    "approved_label": "consent-2026-08",
                },
                {
                    "mode": "reference",
                    "content_checksum": "approved-checksum",
                },
            ),
        )

        baseline_contract: dict[str, object] | None = None
        for provider_name, provider, _voice_config, expected_voice_identity in profiles:
            workflow_run_id = f"workflow_run_{provider_name}"
            first, _, _ = _run_long_form_workflow(
                store_root,
                project_id=f"project_{provider_name}",
                workflow_run_id=workflow_run_id,
                topic="Launch teaser",
                disabled_modules=(),
                resumable_chunking={"max_words": 5, "max_attempts": 1},
                tts_provider=provider,
            )
            second, _, _ = _run_long_form_workflow(
                store_root,
                project_id=f"project_{provider_name}",
                workflow_run_id=workflow_run_id,
                topic="Launch teaser",
                disabled_modules=(),
                resumable_chunking={"max_words": 5, "max_attempts": 1},
                tts_provider=provider,
            )
            manifest = json.loads(
                (
                    store_root
                    / ".tts-runs"
                    / workflow_run_id
                    / "synthesis-manifest.json"
                ).read_text(encoding="utf-8")
            )
            voiceover = second.module_results["voiceover"].output["voiceover"]
            short_contract = {
                "output_keys": tuple(sorted(second.module_results["voiceover"].output)),
                "voiceover_keys": tuple(sorted(second.module_results["voiceover"].output["voiceover"])),
                "artifact_keys": tuple(sorted(second.module_results["voiceover"].output["artifact"])),
                "timeline_keys": tuple(sorted(second.module_results["voiceover"].output["speech_timeline"])),
                "source_kind": second.module_results["voiceover"].output["source_kind"],
                "duration_seconds": voiceover["duration_seconds"],
                "chunk_count": voiceover["chunk_count"],
            }

            assert first.status == "completed"
            assert second.status == "completed"
            assert voiceover["provider"] == provider_name
            assert voiceover["sample_rate"] == provider._sample_rate
            assert second.module_results["voiceover"].output["artifact"]["metadata"]["provider"] == provider_name
            assert manifest["final_status"] == "completed"
            assert manifest["effective_synthesis_identity"]["voice"] == expected_voice_identity

            if baseline_contract is None:
                baseline_contract = short_contract
            else:
                assert short_contract == baseline_contract

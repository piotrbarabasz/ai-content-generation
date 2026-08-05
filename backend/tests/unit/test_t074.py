from __future__ import annotations

import io
import json
import tempfile
import wave
from pathlib import Path

import pytest

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.modules.voiceover import VoiceoverModule
from app.providers.tts_capabilities import TTSCapabilities
from app.providers.tts_factory import TTSFactoryError, build_tts_provider
from app.providers.tts_result import TTSSynthesisResult
from app.storage.local_store import LocalArtifactStore
from app.tts.chunking import NarrationChunkingSettings, chunk_narration
from app.workflow.execution import ModuleExecutionContext


def _wav_bytes(*, sample_rate: int, duration_seconds: float = 0.5) -> bytes:
    buffer = io.BytesIO()
    frame_count = max(int(sample_rate * duration_seconds), 1)
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\0\0" * frame_count)
    return buffer.getvalue()


class ProfiledOfflineProvider:
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
        supports_speaking_rate: bool = False,
        reference_audio_required: bool = False,
    ) -> None:
        self.provider_name = provider_name
        self._sample_rate = sample_rate
        self._model_variant = model_variant
        self._voice_mode = voice_mode
        self._usage_policy = usage_policy
        self._generation_settings = dict(generation_settings or {})
        self._voice_identity = dict(voice_identity or {})
        self._supports_speaking_rate = supports_speaking_rate
        self._reference_audio_required = reference_audio_required

    def capabilities(self) -> TTSCapabilities:
        return TTSCapabilities(
            provider_name=self.provider_name,
            supported_languages=("pl",),
            voice_modes=(self._voice_mode,),
            reference_audio_required=self._reference_audio_required,
            speaking_rate_supported=self._supports_speaking_rate,
            usage_policy=self._usage_policy,
        )

    def effective_synthesis_identity(self, voice_config=None):
        config = dict(voice_config or {})
        generation_settings = dict(self._generation_settings)
        for key in ("exaggeration", "length_scale"):
            if key in config and config[key] is not None:
                generation_settings[key] = config[key]

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


def _run_voiceover(
    root: Path,
    provider: ProfiledOfflineProvider,
    *,
    workflow_run_id: str,
    text: str,
    voice_config: dict[str, object],
    resumable_chunking: dict[str, object] | None = None,
):
    module = VoiceoverModule(
        tts_provider=provider,
        artifact_store=LocalArtifactStore(root / "artifacts"),
        resumable_runtime_dir=root / "runtime",
    )
    inputs: dict[str, object] = {"text": text, "voice_config": voice_config}
    if resumable_chunking is not None:
        inputs["resumable_chunking"] = resumable_chunking
    context = ModuleExecutionContext(
        workflow_run_id=workflow_run_id,
        workflow_config_id="t074-config",
        module_name="voiceover",
        inputs=inputs,
    )
    return module.execute(context)


def _direct_contract(result) -> dict[str, object]:
    return {
        "output_keys": tuple(sorted(result.output)),
        "voiceover_keys": tuple(sorted(result.output["voiceover"])),
        "artifact_keys": tuple(sorted(result.output["artifact"])),
        "timeline_keys": tuple(sorted(result.output["speech_timeline"])),
        "source_kind": result.output["source_kind"],
        "duration_seconds": result.output["voiceover"]["duration_seconds"],
        "chunk_count": result.output["voiceover"]["chunk_count"],
        "provider": result.output["voiceover"]["provider"],
        "sample_rate": result.output["voiceover"]["sample_rate"],
    }


def _resumable_contract(result) -> dict[str, object]:
    contract = _direct_contract(result)
    contract["output_keys"] = tuple(sorted(result.output))
    return contract


def test_multi_provider_voiceover_contracts_stay_aligned_across_short_and_resumable_runs() -> None:
    short_text = "Witaj swiecie."
    long_text = "Jeden dwa trzy cztery piec szesc siedem osiem."

    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        root = Path(directory)
        approved_reference = root / "approved-reference.wav"
        approved_reference.write_bytes(_wav_bytes(sample_rate=16_000))

        profiles = (
            (
                "chatterbox_v3",
                ProfiledOfflineProvider(
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
                ProfiledOfflineProvider(
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
                    supports_speaking_rate=True,
                ),
                {"language_id": "pl", "model_key": "pl_PL-gosia-medium", "length_scale": 1.15},
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
                ProfiledOfflineProvider(
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
                    "reference_audio_path": str(approved_reference),
                    "approved_label": "consent-2026-08",
                },
                {
                    "mode": "reference",
                    "content_checksum": "approved-checksum",
                },
            ),
        )

        direct_baseline: dict[str, object] | None = None
        resumable_baseline: dict[str, object] | None = None
        resumable_chunk_count = len(
            chunk_narration(" ".join(long_text.split()), NarrationChunkingSettings(max_words=3))
        )

        for provider_name, provider, voice_config, expected_voice_identity in profiles:
            short_result = _run_voiceover(
                root / provider_name / "short",
                provider,
                workflow_run_id=f"{provider_name}-short",
                text=short_text,
                voice_config=voice_config,
            )
            long_first = _run_voiceover(
                root / provider_name / "long",
                provider,
                workflow_run_id=f"{provider_name}-long",
                text=long_text,
                voice_config=voice_config,
                resumable_chunking={"max_words": 3, "max_attempts": 1},
            )
            long_second = _run_voiceover(
                root / provider_name / "long",
                provider,
                workflow_run_id=f"{provider_name}-long",
                text=long_text,
                voice_config=voice_config,
                resumable_chunking={"max_words": 3, "max_attempts": 1},
            )

            assert short_result.status == "completed"
            assert long_first.status == "completed"
            assert long_second.status == "completed"
            assert short_result.output["voiceover"]["provider"] == provider_name
            assert short_result.output["voiceover"]["sample_rate"] == provider._sample_rate
            assert long_second.output["voiceover"]["provider"] == provider_name
            assert long_second.output["voiceover"]["sample_rate"] == provider._sample_rate
            assert long_second.output["voiceover"]["chunk_count"] == resumable_chunk_count
            assert long_second.output["voiceover"]["duration_seconds"] == 0.5 * resumable_chunk_count
            assert long_second.output["artifact"]["metadata"]["provider"] == provider_name

            short_contract = _direct_contract(short_result)
            resumable_contract = _resumable_contract(long_second)
            normalized_short = {key: value for key, value in short_contract.items() if key not in {"provider", "sample_rate"}}
            normalized_resumable = {
                key: value for key, value in resumable_contract.items() if key not in {"provider", "sample_rate"}
            }
            if direct_baseline is None:
                direct_baseline = normalized_short
            else:
                assert normalized_short == direct_baseline
            if resumable_baseline is None:
                resumable_baseline = normalized_resumable
            else:
                assert normalized_resumable == resumable_baseline

            runtime_dir = root / provider_name / "long" / "runtime" / f"{provider_name}-long"
            manifest = json.loads((runtime_dir / "synthesis-manifest.json").read_text(encoding="utf-8"))
            assert manifest["final_status"] == "completed"
            assert manifest["generated_chunk_count"] == 0
            assert manifest["reused_chunk_count"] == resumable_chunk_count
            assert manifest["effective_synthesis_identity"]["voice"] == expected_voice_identity


def test_xtts_production_policy_is_rejected_before_runtime_loading() -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        root = Path(directory)
        reference_audio = root / "approved-reference.wav"
        reference_audio.write_bytes(_wav_bytes(sample_rate=16_000))
        config = ProviderConfig.create(
            workflow_config_id="workflow",
            provider_type=ProviderType.TTS,
            provider_name="xtts_v2_eval",
            settings={
                "usage_policy": "production",
                "reference_audio_path": reference_audio,
                "approved_label": "consent-2026-08",
            },
        )

        with pytest.raises(TTSFactoryError, match="evaluation-only"):
            build_tts_provider(config)


def test_m006_operational_decision_record_is_linked_and_descriptive() -> None:
    root = Path(__file__).resolve().parents[3]
    docs_index = (root / "docs" / "INDEX.md").read_text(encoding="utf-8")
    decision_doc = (root / "docs" / "tts" / "M006_PROVIDER_DECISION.md").read_text(encoding="utf-8")

    assert "docs/tts/M006_PROVIDER_DECISION.md" in docs_index
    assert "docs/tts/RUNTIME_PROFILES.md" in docs_index
    assert "docs/tts/PROVIDER_COMPARISON.md" in docs_index
    assert "Provider roles" in decision_doc
    assert "Runtime profiles" in decision_doc
    assert "Licensing and usage policy" in decision_doc
    assert "Manual comparison contract" in decision_doc
    assert "human license review remains required" in decision_doc.lower()

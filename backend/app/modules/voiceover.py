"""Deterministic voiceover generation module."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from time import monotonic

from app.domain.content_brief import ContentBrief
from app.domain.types import JsonDict
from app.providers.interfaces import TTSProvider
from app.providers.tts_result import TTSSynthesisResult
from app.storage.artifact_store import ArtifactStore
from app.tts.benchmark import build_benchmark_report
from app.tts.assembly import WavAssemblyError, inspect_pcm_wav
from app.tts.chunk_synthesis import ResumableChunkSynthesizer
from app.tts.chunking import NarrationChunkingSettings, chunk_narration
from app.workflow.execution import ModuleExecutionContext, ModuleResult
from app.workflow.module import ModuleDefinition


def _pick(mapping: Mapping[str, object], *names: str, default: object | None = None) -> object | None:
    for name in names:
        if name not in mapping:
            continue
        value = mapping[name]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return default


def _optional_text(value: object | None) -> str:
    return " ".join(str(value).strip().split()) if value is not None else ""


def _coerce_text(value: object | None, *, field_name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"VoiceoverModule {field_name} is required.")
    return text


def _coerce_mapping(value: object | None) -> JsonDict:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _source_payload(inputs: Mapping[str, object], module_results: Mapping[str, object]) -> tuple[str, str]:
    explicit_text = _optional_text(
        _pick(inputs, "voiceover_text", "script", "script_text", "text", "narration")
    )
    if explicit_text:
        return explicit_text, "script" if "script" in inputs or "script_text" in inputs else "text"

    content_brief = _pick(inputs, "content_brief", "brief")
    if isinstance(content_brief, ContentBrief):
        if content_brief.objective.strip():
            return content_brief.objective, "brief"
        if content_brief.topic.strip():
            return content_brief.topic, "brief"
    if isinstance(content_brief, Mapping):
        objective = _optional_text(content_brief.get("objective"))
        if objective:
            return objective, "brief"
        topic = _optional_text(content_brief.get("topic"))
        if topic:
            return topic, "brief"

    script_result = module_results.get("scriptGeneration")
    if script_result is not None:
        output = getattr(script_result, "output", {})
        if isinstance(output, Mapping):
            for key in ("script_text", "script", "content"):
                source_text = _optional_text(output.get(key))
                if source_text:
                    return source_text, "script"

    raise ValueError("VoiceoverModule requires script, text or brief input.")


def _voice_config_payload(
    inputs: Mapping[str, object],
    *,
    default_voice: str,
    default_language: str,
    default_tone: str,
    source_kind: str,
) -> JsonDict:
    voice_config = _coerce_mapping(
        _pick(inputs, "voice_config", "voiceConfig", "voice_profile", "voiceProfile")
    )
    voice_config.setdefault("voice", _optional_text(_pick(inputs, "voice")) or default_voice)
    voice_config.setdefault("language", _optional_text(_pick(inputs, "language")) or default_language)
    voice_config.setdefault("tone", _optional_text(_pick(inputs, "tone")) or default_tone)
    voice_config.setdefault("source_kind", source_kind)
    resumable_chunking = _pick(inputs, "resumable_chunking", "resumableChunking")
    if resumable_chunking is not None:
        voice_config.setdefault("resumable_chunking", resumable_chunking)
    return voice_config


def _word_timings(text: str, duration_seconds: float) -> list[JsonDict]:
    words = text.split()
    if not words:
        return []
    if duration_seconds <= 0:
        duration_seconds = 0.25 * len(words)
    step = duration_seconds / len(words)
    timings: list[JsonDict] = []
    for index, word in enumerate(words):
        start = round(step * index, 3)
        end = round(step * (index + 1), 3)
        timings.append(
            {
                "index": index,
                "word": word,
                "start_seconds": start,
                "end_seconds": end,
            }
        )
    return timings


def _validate_wave_bytes(audio_bytes: bytes, *, expected_sample_rate: int) -> None:
    try:
        parameters, _ = inspect_pcm_wav(audio_bytes)
    except WavAssemblyError as exc:
        # Use the same PCM validation and rejection reason as chunk assembly.
        raise ValueError(str(exc)) from exc
    if parameters.channels != 1:
        raise ValueError("VoiceoverModule requires mono WAV audio.")
    if parameters.sample_width != 2:
        raise ValueError("VoiceoverModule requires 16-bit PCM WAV audio.")
    if parameters.sample_rate != expected_sample_rate:
        raise ValueError("VoiceoverModule WAV sample rate does not match the synthesis result.")


def _stable_text_reference(workflow_run_id: str, text: str, source_kind: str) -> str:
    signature = sha256(
        f"{workflow_run_id}:{source_kind}:{' '.join(text.split())}".encode("utf-8")
    ).hexdigest()[:12]
    return f"text_ref_{signature}"


def _resumable_settings(voice_config: Mapping[str, object]) -> tuple[int, int] | None:
    """Read provider-neutral chunking controls without selecting a provider."""
    value = _pick(voice_config, "resumable_chunking", "resumableChunking")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("VoiceoverModule resumable_chunking must be an object.")
    if value.get("enabled", True) is False:
        return None
    max_words = value.get("max_words", value.get("maxWords", 120))
    max_attempts = value.get("max_attempts", value.get("maxAttempts", 2))
    if not isinstance(max_words, int) or isinstance(max_words, bool):
        raise ValueError("VoiceoverModule resumable_chunking.max_words must be an integer.")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise ValueError("VoiceoverModule resumable_chunking.max_attempts must be a positive integer.")
    NarrationChunkingSettings(max_words=max_words)
    return max_words, max_attempts


def _provider_voice_config(voice_config: Mapping[str, object]) -> JsonDict:
    """Do not leak orchestration controls into a provider request."""
    return {
        key: value
        for key, value in voice_config.items()
        if key not in {"resumable_chunking", "resumableChunking"}
    }


class VoiceoverModule:
    """Generate deterministic mock voiceover artifacts from supplied text."""

    definition = ModuleDefinition(
        name="voiceover",
        input_schema={
            "type": "object",
            "properties": {
                "content_brief": {"type": ["object", "string"]},
                "brief": {"type": ["object", "string"]},
                "voiceover_text": {"type": "string"},
                "script": {"type": "string"},
                "script_text": {"type": "string"},
                "text": {"type": "string"},
                "narration": {"type": "string"},
                "voice_config": {"type": "object"},
                "resumable_chunking": {"type": "object"},
                "voice": {"type": "string"},
                "language": {"type": "string"},
                "tone": {"type": "string"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "voiceover": {"type": "object"},
                "artifact": {"type": "object"},
                "speech_timeline": {"type": "object"},
                "speech_timeline_artifact": {"type": "object"},
                "synthesis_manifest_artifact": {"type": "object"},
                "benchmark_artifact": {"type": "object"},
                "workflow_snapshot": {"type": "object"},
                "source_kind": {"type": "string"},
            },
        },
        config_schema={
            "type": "object",
            "properties": {
                "default_voice": {"type": "string"},
                "default_language": {"type": "string"},
                "default_tone": {"type": "string"},
                "artifact_name": {"type": "string"},
            },
        },
        dependencies=(("brief",),),
        disabled_behavior="skip",
        enabled_by_default=False,
        retry_limit=2,
        artifact_outputs=(
            "voiceover.wav",
            "speech_timeline.json",
            "synthesis-manifest.json",
            "tts-benchmark.json",
        ),
        error_behavior="request_missing_fields",
    )

    def __init__(
        self,
        *,
        tts_provider: TTSProvider,
        artifact_store: ArtifactStore,
        default_voice: str = "narrator",
        default_language: str = "en",
        default_tone: str = "neutral",
        artifact_name: str = "voiceover.wav",
        speech_timeline_name: str = "speech_timeline.json",
        resumable_runtime_dir: Path | str | None = None,
    ) -> None:
        self._tts_provider = tts_provider
        self._artifact_store = artifact_store
        self._default_voice = _optional_text(default_voice) or "narrator"
        self._default_language = _optional_text(default_language) or "en"
        self._default_tone = _optional_text(default_tone) or "neutral"
        self._artifact_name = _optional_text(artifact_name) or "voiceover.wav"
        self._speech_timeline_name = (
            _optional_text(speech_timeline_name) or "speech_timeline.json"
        )
        self._resumable_runtime_dir = (
            Path(resumable_runtime_dir) if resumable_runtime_dir is not None else None
        )

    def _runtime_dir(self, workflow_run_id: str) -> Path:
        if self._resumable_runtime_dir is not None:
            return self._resumable_runtime_dir / workflow_run_id
        root = getattr(self._artifact_store, "root", None)
        if isinstance(root, Path):
            return root / ".tts-runs" / workflow_run_id
        raise ValueError(
            "VoiceoverModule chunked synthesis requires resumable_runtime_dir with this artifact store."
        )

    def execute(self, context: ModuleExecutionContext) -> ModuleResult:
        inputs = dict(context.inputs)
        text, source_kind = _source_payload(inputs, context.module_results)
        normalized_text = _coerce_text(text, field_name="text")
        voice_config = _voice_config_payload(
            inputs,
            default_voice=self._default_voice,
            default_language=self._default_language,
            default_tone=self._default_tone,
            source_kind=source_kind,
        )

        provider_voice_config = _provider_voice_config(voice_config)
        resumable = _resumable_settings(voice_config)
        synthesis_manifest_payload: JsonDict | None = None
        benchmark_payload: JsonDict | None = None
        if resumable is None:
            synthesis = self._tts_provider.synthesize(normalized_text, provider_voice_config)
            if not isinstance(synthesis, TTSSynthesisResult):
                raise TypeError("VoiceoverModule TTS provider must return TTSSynthesisResult.")
            if synthesis.audio_format != "wav":
                raise ValueError("VoiceoverModule TTS provider must return WAV audio.")
            audio_bytes = synthesis.audio_bytes
            sample_rate = synthesis.sample_rate
            duration_seconds = round(float(synthesis.duration_seconds), 3)
            source_metadata = synthesis.metadata
            chunk_count = 1
        else:
            max_words, max_attempts = resumable
            started = monotonic()
            chunks = chunk_narration(normalized_text, NarrationChunkingSettings(max_words=max_words))
            result = ResumableChunkSynthesizer(self._tts_provider, max_attempts=max_attempts).synthesize(
                chunks,
                runtime_dir=self._runtime_dir(context.workflow_run_id),
                voice_config=provider_voice_config,
            )
            if not result.completed or result.final_wav is None:
                failed = ", ".join(result.manifest.failed_chunk_ids) or "unknown chunk"
                raise RuntimeError(f"VoiceoverModule chunked synthesis failed: {failed}.")
            audio_bytes = result.final_wav.audio_bytes
            sample_rate = result.final_wav.audio_parameters.sample_rate
            duration_seconds = round(result.final_wav.duration_seconds, 3)
            source_metadata = {}
            chunk_count = len(chunks)
            synthesis_manifest_payload = result.manifest.to_payload()
            benchmark_payload = build_benchmark_report(
                result.manifest,
                word_count=len(normalized_text.split()),
                generation_wall_time_seconds=monotonic() - started,
            ).to_payload()

        _validate_wave_bytes(audio_bytes, expected_sample_rate=sample_rate)

        source_ref = _optional_text(
            _pick(
                source_metadata,
                "source_ref",
                "sourceRef",
                "audio_ref",
                "audioRef",
            )
        ) or _stable_text_reference(context.workflow_run_id, normalized_text, source_kind)

        text_reference_id = _optional_text(
            _pick(inputs, "text_reference_id", "textReferenceId")
        ) or _stable_text_reference(context.workflow_run_id, normalized_text, source_kind)

        voiceover_payload = {
            "workflow_run_id": context.workflow_run_id,
            "text_reference_id": text_reference_id,
            "provider": self._tts_provider.provider_name,
            "voice_config": voice_config,
            "text": normalized_text,
            "source_kind": source_kind,
            "audio_ref": source_ref,
            "source_ref": source_ref,
            "audio_format": "wav",
            "sample_rate": sample_rate,
            "duration_seconds": duration_seconds,
            "word_count": len(normalized_text.split()),
            "chunk_count": chunk_count,
        }

        voiceover_manifest = self._artifact_store.save_artifact(
            self._artifact_name,
            audio_bytes,
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "voiceover",
                "source_kind": source_kind,
                "provider": self._tts_provider.provider_name,
                "source_ref": source_ref,
                "sample_rate": sample_rate,
                "duration_seconds": duration_seconds,
                "audio_format": "wav",
                "text_reference_id": text_reference_id,
                "voice_config": voice_config,
            },
        )

        speech_timeline_payload = {
            "workflow_run_id": context.workflow_run_id,
            "voiceover_storage_key": voiceover_manifest.storage_key,
            "source_kind": source_kind,
            "duration_seconds": duration_seconds,
            "word_timings": _word_timings(normalized_text, duration_seconds),
            "voice_config": voice_config,
            "provider": self._tts_provider.provider_name,
            "source_ref": source_ref,
            "sample_rate": sample_rate,
            "audio_format": "wav",
        }
        speech_timeline_manifest = self._artifact_store.save_artifact(
            self._speech_timeline_name,
            json.dumps(speech_timeline_payload, indent=2, sort_keys=True),
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "speech_timeline",
                "source_kind": source_kind,
                "provider": self._tts_provider.provider_name,
                "voiceover_storage_key": voiceover_manifest.storage_key,
            },
        )

        synthesis_manifest_artifact = None
        benchmark_artifact = None
        if synthesis_manifest_payload is not None and benchmark_payload is not None:
            synthesis_manifest_artifact = self._artifact_store.save_artifact(
                "synthesis-manifest.json",
                json.dumps(synthesis_manifest_payload, indent=2, sort_keys=True),
                metadata={"workflow_run_id": context.workflow_run_id, "module_name": self.definition.name, "artifact_type": "tts_synthesis_manifest"},
            )
            benchmark_artifact = self._artifact_store.save_artifact(
                "tts-benchmark.json",
                json.dumps(benchmark_payload, indent=2, sort_keys=True),
                metadata={"workflow_run_id": context.workflow_run_id, "module_name": self.definition.name, "artifact_type": "tts_benchmark"},
            )

        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=(
                self.definition.artifact_outputs
                if synthesis_manifest_payload is not None
                else self.definition.artifact_outputs[:2]
            ),
            output={
                "voiceover": {
                    **voiceover_payload,
                    "audio_storage_key": voiceover_manifest.storage_key,
                },
                "artifact": voiceover_manifest.to_payload(),
                "speech_timeline": speech_timeline_payload,
                "speech_timeline_artifact": speech_timeline_manifest.to_payload(),
                **({"synthesis_manifest_artifact": synthesis_manifest_artifact.to_payload()} if synthesis_manifest_artifact else {}),
                **({"benchmark_artifact": benchmark_artifact.to_payload()} if benchmark_artifact else {}),
                "workflow_snapshot": {
                    "workflow_run_id": context.workflow_run_id,
                    "workflow_config_id": context.workflow_config_id,
                    "module_name": context.module_name,
                    "enabled_modules": list(context.enabled_modules),
                    "disabled_modules": list(context.disabled_modules),
                },
                "source_kind": source_kind,
            },
        )

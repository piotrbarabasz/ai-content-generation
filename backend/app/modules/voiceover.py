"""Deterministic voiceover generation module."""

from __future__ import annotations

import json
import io
from collections.abc import Mapping
from hashlib import sha256
import wave

from app.domain.content_brief import ContentBrief
from app.domain.types import JsonDict
from app.providers.interfaces import TTSProvider
from app.providers.tts_result import TTSSynthesisResult
from app.storage.artifact_store import ArtifactStore
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
        with wave.open(io.BytesIO(audio_bytes), "rb") as reader:
            if reader.getnchannels() != 1:
                raise ValueError("VoiceoverModule requires mono WAV audio.")
            if reader.getsampwidth() != 2:
                raise ValueError("VoiceoverModule requires 16-bit PCM WAV audio.")
            if reader.getframerate() != expected_sample_rate:
                raise ValueError(
                    "VoiceoverModule WAV sample rate does not match the synthesis result."
                )
            if reader.getcomptype() != "NONE":
                raise ValueError("VoiceoverModule requires uncompressed PCM WAV audio.")
    except (EOFError, wave.Error, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError("VoiceoverModule TTS provider returned invalid WAV audio.") from exc


def _stable_text_reference(workflow_run_id: str, text: str, source_kind: str) -> str:
    signature = sha256(
        f"{workflow_run_id}:{source_kind}:{' '.join(text.split())}".encode("utf-8")
    ).hexdigest()[:12]
    return f"text_ref_{signature}"


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
        artifact_outputs=("voiceover.wav", "speech_timeline.json"),
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

        synthesis = self._tts_provider.synthesize(normalized_text, voice_config)
        if not isinstance(synthesis, TTSSynthesisResult):
            raise TypeError("VoiceoverModule TTS provider must return TTSSynthesisResult.")
        if synthesis.audio_format != "wav":
            raise ValueError("VoiceoverModule TTS provider must return WAV audio.")

        _validate_wave_bytes(synthesis.audio_bytes, expected_sample_rate=synthesis.sample_rate)

        source_ref = _optional_text(
            _pick(
                synthesis.metadata,
                "source_ref",
                "sourceRef",
                "audio_ref",
                "audioRef",
            )
        ) or _stable_text_reference(context.workflow_run_id, normalized_text, source_kind)

        duration_seconds = round(float(synthesis.duration_seconds), 3)
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
            "audio_format": synthesis.audio_format,
            "sample_rate": synthesis.sample_rate,
            "duration_seconds": duration_seconds,
            "word_count": len(normalized_text.split()),
        }

        voiceover_manifest = self._artifact_store.save_artifact(
            self._artifact_name,
            synthesis.audio_bytes,
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "voiceover",
                "source_kind": source_kind,
                "provider": self._tts_provider.provider_name,
                "source_ref": source_ref,
                "sample_rate": synthesis.sample_rate,
                "duration_seconds": duration_seconds,
                "audio_format": synthesis.audio_format,
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
            "sample_rate": synthesis.sample_rate,
            "audio_format": synthesis.audio_format,
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

        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=self.definition.artifact_outputs,
            output={
                "voiceover": {
                    **voiceover_payload,
                    "audio_storage_key": voiceover_manifest.storage_key,
                },
                "artifact": voiceover_manifest.to_payload(),
                "speech_timeline": speech_timeline_payload,
                "speech_timeline_artifact": speech_timeline_manifest.to_payload(),
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

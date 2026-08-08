"""Deterministic captions generation module."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from hashlib import sha256
from pathlib import PurePosixPath

from app.domain.caption_track import (
    CaptionTrack,
    serialize_srt,
    validate_caption_segments,
)
from app.domain.export_config import normalize_language
from app.domain.types import JsonDict
from app.providers.interfaces import CaptionProvider, TranscriptionProvider
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
        raise ValueError(f"CaptionsModule {field_name} is required.")
    return text


def _coerce_mapping(value: object | None) -> JsonDict:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _json_default(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _stable_identifier(*parts: object, prefix: str) -> str:
    signature = sha256(
        ":".join(_optional_text(part) for part in parts).encode("utf-8")
    ).hexdigest()[:12]
    return f"{prefix}_{signature}"


def _extract_voiceover_payload(
    inputs: Mapping[str, object],
    module_results: Mapping[str, object],
) -> tuple[str, JsonDict, str]:
    explicit_voiceover = _coerce_mapping(_pick(inputs, "voiceover", "voiceover_result"))
    if explicit_voiceover:
        audio_ref = _optional_text(
            _pick(explicit_voiceover, "audio_ref", "audioRef", "audio_storage_ref")
        )
        if audio_ref:
            return audio_ref, explicit_voiceover, "voiceover"

    voiceover_result = module_results.get("voiceover")
    if voiceover_result is not None:
        output = getattr(voiceover_result, "output", {})
        if isinstance(output, Mapping):
            voiceover_payload = _coerce_mapping(output.get("voiceover"))
            if voiceover_payload:
                audio_ref = _optional_text(
                    _pick(voiceover_payload, "audio_ref", "audioRef", "audio_storage_ref")
                )
                if audio_ref:
                    return audio_ref, voiceover_payload, "voiceover"
            audio_ref = _optional_text(_pick(output, "audio_ref", "audioRef"))
            if audio_ref:
                return audio_ref, _coerce_mapping(output), "voiceover"

    audio_ref = _optional_text(
        _pick(inputs, "voiceover_audio_ref", "voiceoverAudioRef", "audio_ref", "audioRef")
    )
    if audio_ref:
        return audio_ref, {}, "voiceover"

    script_source = _optional_text(
        _pick(inputs, "script", "script_text", "text", "narration")
    )
    if not script_source:
        script_result = module_results.get("scriptGeneration")
        if script_result is not None:
            output = getattr(script_result, "output", {})
            if isinstance(output, Mapping):
                script_source = _optional_text(
                    _pick(output, "script_text", "script", "text", "content")
                )
                if not script_source:
                    script_payload = _coerce_mapping(output.get("script"))
                    script_source = _optional_text(
                        _pick(script_payload, "text", "script_text", "content")
                    )

    if not script_source:
        raise ValueError("CaptionsModule requires voiceover, script or audio input.")

    synthetic_audio_ref = f"mock://captions/script/{_stable_identifier(script_source, prefix='audio')}"
    return synthetic_audio_ref, {"script_text": script_source}, "script"


def _extract_scene_plan(
    inputs: Mapping[str, object],
    module_results: Mapping[str, object],
) -> JsonDict:
    explicit_scene_plan = _coerce_mapping(_pick(inputs, "scene_plan", "scenePlan"))
    if explicit_scene_plan:
        return explicit_scene_plan

    scene_planning_result = module_results.get("scenePlanning")
    if scene_planning_result is not None:
        output = getattr(scene_planning_result, "output", {})
        if isinstance(output, Mapping):
            scene_plan = _coerce_mapping(output.get("scene_plan"))
            if scene_plan:
                return scene_plan
            render_scenes = _coerce_mapping(output.get("render_scenes"))
            if render_scenes:
                return render_scenes

    return {}


def _extract_transcript_payload(
    *,
    audio_ref: str,
    source_kind: str,
    inputs: Mapping[str, object],
    transcription_provider: TranscriptionProvider,
    voiceover_payload: JsonDict,
    script_source: str,
) -> tuple[str, str, JsonDict]:
    explicit_transcript = _optional_text(
        _pick(inputs, "transcript", "transcript_text", "captions_text")
    )
    explicit_transcript_ref = _optional_text(
        _pick(inputs, "transcript_ref", "transcriptRef")
    )
    if explicit_transcript:
        transcript_ref = explicit_transcript_ref or _stable_identifier(
            explicit_transcript,
            audio_ref,
            prefix="transcript",
        )
        return transcript_ref, explicit_transcript, {
            "provider": _optional_text(voiceover_payload.get("provider")),
            "audio_ref": audio_ref,
            "transcript_ref": transcript_ref,
            "transcript": explicit_transcript,
            "segments": [],
            "source_kind": source_kind,
        }

    if source_kind == "voiceover":
        transcript_payload = transcription_provider.transcribe(audio_ref)
        transcript_ref = _optional_text(transcript_payload.get("transcript_ref")) or _stable_identifier(
            audio_ref,
            prefix="transcript",
        )
        transcript_text = _optional_text(transcript_payload.get("transcript"))
        if not transcript_text:
            transcript_text = _optional_text(script_source) or audio_ref
        return transcript_ref, transcript_text, dict(transcript_payload)

    transcript_text = _optional_text(script_source)
    transcript_ref = explicit_transcript_ref or _stable_identifier(
        transcript_text,
        audio_ref,
        prefix="transcript",
    )
    return transcript_ref, transcript_text, {
        "provider": _optional_text(voiceover_payload.get("provider")),
        "audio_ref": audio_ref,
        "transcript_ref": transcript_ref,
        "transcript": transcript_text,
        "segments": [],
        "source_kind": source_kind,
    }


def _scene_plan_summary(scene_plan: Mapping[str, object]) -> JsonDict:
    scenes = scene_plan.get("scenes")
    scene_count = len(scenes) if isinstance(scenes, list) else 0
    return {
        "scene_plan_id": _optional_text(scene_plan.get("scene_plan_id")),
        "scene_count": scene_count,
        "platform": _optional_text(scene_plan.get("platform")),
        "aspect_ratio": _optional_text(scene_plan.get("aspect_ratio")),
    }


class CaptionsModule:
    """Generate deterministic caption artifacts from voiceover or script input."""

    definition = ModuleDefinition(
        name="captions",
        input_schema={
            "type": "object",
            "properties": {
                "voiceover": {"type": ["object", "string"]},
                "voiceover_result": {"type": ["object", "string"]},
                "voiceover_audio_ref": {"type": "string"},
                "voiceoverAudioRef": {"type": "string"},
                "audio_ref": {"type": "string"},
                "audioRef": {"type": "string"},
                "scene_plan": {"type": ["object", "string"]},
                "scenePlan": {"type": ["object", "string"]},
                "script": {"type": ["object", "string"]},
                "script_text": {"type": "string"},
                "text": {"type": "string"},
                "narration": {"type": "string"},
                "transcript": {"type": "string"},
                "transcript_text": {"type": "string"},
                "transcript_ref": {"type": "string"},
                "transcriptRef": {"type": "string"},
                "language": {"type": "string"},
                "style": {"type": "string"},
                "caption_style": {"type": "string"},
                "captionStyle": {"type": "string"},
                "captions_text": {"type": "string"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "caption_track": {"type": "object"},
                "captions": {"type": "object"},
                "captions_json": {"type": "array"},
                "captions_srt": {"type": "string"},
                "srt_artifact": {"type": "object"},
                "artifact": {"type": "object"},
                "scene_plan": {"type": "object"},
                "voiceover": {"type": "object"},
                "transcript": {"type": "object"},
                "workflow_snapshot": {"type": "object"},
                "source_kind": {"type": "string"},
            },
        },
        config_schema={
            "type": "object",
            "properties": {
                "default_language": {"type": "string"},
                "default_style": {"type": "string"},
                "captions_name": {"type": "string"},
                "srt_name_template": {"type": "string"},
            },
        },
        dependencies=(("voiceover", "scriptGeneration"), ("scenePlanning",)),
        enabled_by_default=False,
        disabled_behavior="skip",
        retry_limit=1,
        artifact_outputs=("captions.json",),
        error_behavior="request_missing_fields",
    )

    def __init__(
        self,
        *,
        caption_provider: CaptionProvider,
        transcription_provider: TranscriptionProvider,
        artifact_store: ArtifactStore,
        default_language: str = "en",
        default_style: str = "standard",
        captions_name: str = "captions.json",
        srt_name_template: str = "captions.{language}.srt",
    ) -> None:
        self._caption_provider = caption_provider
        self._transcription_provider = transcription_provider
        self._artifact_store = artifact_store
        self._default_language = _optional_text(default_language) or "en"
        self._default_style = _optional_text(default_style) or "standard"
        self._captions_name = _optional_text(captions_name) or "captions.json"
        self._srt_name_template = _optional_text(srt_name_template) or "captions.{language}.srt"

    def execute(self, context: ModuleExecutionContext) -> ModuleResult:
        inputs = dict(context.inputs)
        voiceover_audio_ref, voiceover_payload, source_kind = _extract_voiceover_payload(
            inputs,
            context.module_results,
        )
        scene_plan = _extract_scene_plan(inputs, context.module_results)
        language = normalize_language(
            _optional_text(_pick(inputs, "language")) or self._default_language,
            field_name="caption language",
        )
        style = _optional_text(
            _pick(inputs, "style", "caption_style", "captionStyle")
        ) or self._default_style
        script_source = _optional_text(
            _pick(inputs, "script", "script_text", "text", "narration")
        )
        if not script_source and source_kind == "script":
            script_source = _optional_text(
                _pick(voiceover_payload, "script_text", "script", "text")
            )

        transcript_ref, transcript_text, transcript_payload = _extract_transcript_payload(
            audio_ref=voiceover_audio_ref,
            source_kind=source_kind,
            inputs=inputs,
            transcription_provider=self._transcription_provider,
            voiceover_payload=voiceover_payload,
            script_source=script_source,
        )
        captions_payload = self._caption_provider.generate_captions(
            voiceover_audio_ref,
            transcript_ref,
        )
        captions_json = captions_payload.get("captions_json")
        if not isinstance(captions_json, list):
            raise ValueError("CaptionProvider must return structured captions_json timing.")
        segments = validate_caption_segments(captions_json)
        captions_json = [
            {
                "id": segment.id,
                "index": segment.index,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "text": segment.text,
            }
            for segment in segments
        ]
        # Provider timing is canonical; provider-supplied SRT is deliberately not trusted.
        captions_srt = serialize_srt(segments)

        scene_summary = _scene_plan_summary(scene_plan)
        captions_record = {
            "workflow_run_id": context.workflow_run_id,
            "module_name": self.definition.name,
            "provider": self._caption_provider.provider_name,
            "transcription_provider": self._transcription_provider.provider_name,
            "source_kind": source_kind,
            "language": language,
            "style": style,
            "audio_ref": voiceover_audio_ref,
            "transcript_ref": transcript_ref,
            "transcript": transcript_text,
            "scene_plan": scene_summary,
            "voiceover": {
                "provider": _optional_text(voiceover_payload.get("provider")),
                "audio_ref": voiceover_audio_ref,
                "audio_storage_key": _optional_text(
                    voiceover_payload.get("audio_storage_key")
                ),
            },
            "captions_json": captions_json,
            "captions_srt": captions_srt,
        }

        artifact = self._artifact_store.save_artifact(
            self._captions_name,
            json.dumps(captions_record, indent=2, sort_keys=True, default=_json_default),
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "captions",
                "provider": self._caption_provider.provider_name,
                "transcription_provider": self._transcription_provider.provider_name,
                "source_kind": source_kind,
                "language": language,
                "style": style,
                "audio_ref": voiceover_audio_ref,
                "transcript_ref": transcript_ref,
                "scene_plan_id": scene_summary["scene_plan_id"],
            },
        )
        srt_name = self._srt_name_template.format(language=language.lower())
        if PurePosixPath(srt_name.replace("\\", "/")).name != srt_name:
            raise ValueError("CaptionsModule SRT artifact name must be a filename.")
        srt_artifact = self._artifact_store.save_artifact(
            srt_name,
            captions_srt.encode("utf-8"),
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "captions_srt",
                "provider": self._caption_provider.provider_name,
                "language": language,
                "segment_count": len(segments),
            },
        )

        caption_track = CaptionTrack.create(
            workflow_run_id=context.workflow_run_id,
            provider=self._caption_provider.provider_name,
            caption_storage_key=artifact.storage_key,
            srt_storage_key=srt_artifact.storage_key,
            language=language,
        )

        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=self.definition.artifact_outputs,
            output={
                "caption_track": asdict(caption_track),
                "captions": captions_record,
                "captions_json": captions_json,
                "captions_srt": captions_srt,
                "artifact": artifact.to_payload(),
                "srt_artifact": srt_artifact.to_payload(),
                "scene_plan": scene_plan,
                "voiceover": {
                    "provider": _optional_text(voiceover_payload.get("provider")),
                    "audio_ref": voiceover_audio_ref,
                    "audio_storage_key": _optional_text(
                        voiceover_payload.get("audio_storage_key")
                    ),
                },
                "transcript": transcript_payload,
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

"""Deterministic post-processing module for long-form scripts."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from hashlib import sha256

from app.domain.script import Script
from app.domain.types import JsonDict
from app.storage.artifact_store import ArtifactStore
from app.workflow.execution import ModuleExecutionContext, ModuleResult
from app.workflow.module import ModuleDefinition


_DETERMINISTIC_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)
_DEFAULT_CLEANUP_RULES: JsonDict = {
    "normalize_whitespace": True,
    "collapse_blank_lines": True,
    "strip_trailing_spaces": True,
    "strip_code_fences": True,
    "normalize_punctuation_spacing": True,
}


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


def _coerce_mapping(value: object | None) -> JsonDict:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _coerce_text(value: object | None, *, field_name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"PostProcessingModule {field_name} is required.")
    return text


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _stable_identifier(*, workflow_run_id: str, source_kind: str, text: str) -> str:
    signature = sha256(
        f"postProcessing:{workflow_run_id}:{source_kind}:{text}".encode("utf-8")
    ).hexdigest()[:12]
    return f"post_processing_{signature}"


def _strip_code_fence_blocks(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) < 2:
        return text
    inner = lines[1:-1]
    return "\n".join(inner).strip()


def _normalize_inline_text(text: str, *, normalize_punctuation_spacing: bool) -> str:
    normalized = " ".join(text.strip().split())
    if normalize_punctuation_spacing:
        normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    return normalized


def _normalize_script_text(text: str, *, cleanup_rules: Mapping[str, object]) -> str:
    cleaned = _optional_text(text)
    if not cleaned:
        return ""
    if cleanup_rules.get("strip_code_fences", True):
        cleaned = _strip_code_fence_blocks(cleaned)

    normalize_punctuation_spacing = bool(cleanup_rules.get("normalize_punctuation_spacing", True))
    collapse_blank_lines = bool(cleanup_rules.get("collapse_blank_lines", True))
    strip_trailing_spaces = bool(cleanup_rules.get("strip_trailing_spaces", True))
    normalize_whitespace = bool(cleanup_rules.get("normalize_whitespace", True))

    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.rstrip() if strip_trailing_spaces else raw_line
        if normalize_whitespace:
            line = _normalize_inline_text(
                line,
                normalize_punctuation_spacing=normalize_punctuation_spacing,
            )
        elif normalize_punctuation_spacing:
            line = re.sub(r"\s+([,.;:!?])", r"\1", line)
        lines.append(line)

    if collapse_blank_lines:
        collapsed: list[str] = []
        previous_blank = False
        for line in lines:
            is_blank = not line.strip()
            if is_blank:
                if collapsed and not previous_blank:
                    collapsed.append("")
                previous_blank = True
                continue
            collapsed.append(line.strip() if normalize_whitespace else line)
            previous_blank = False
        lines = collapsed

    return "\n".join(line for line in lines).strip()


def _text_from_script_payload(payload: Mapping[str, object]) -> str:
    for key in ("text", "script_text", "content", "body"):
        text = _optional_text(_pick(payload, key))
        if text:
            return text
    return ""


def _extract_script_result(
    inputs: Mapping[str, object],
    module_results: Mapping[str, object],
) -> tuple[str, JsonDict, JsonDict, str]:
    explicit_script = _coerce_mapping(_pick(inputs, "script", "script_draft", "scriptDraft"))
    if explicit_script:
        text = _text_from_script_payload(explicit_script)
        if not text:
            text = _optional_text(_pick(inputs, "script_text", "text", "draft_text"))
        if text:
            return text, explicit_script, {}, _optional_text(_pick(explicit_script, "source_kind")) or "script"

    explicit_text = _optional_text(_pick(inputs, "script_text", "text", "draft_text"))
    if explicit_text:
        return explicit_text, {}, {}, "script"

    script_result = module_results.get("scriptGeneration")
    if script_result is not None:
        output = getattr(script_result, "output", {})
        if isinstance(output, Mapping):
            script_payload = _coerce_mapping(output.get("script"))
            if not script_payload and isinstance(output.get("script_json"), Mapping):
                script_payload = dict(output["script_json"])
            text = _text_from_script_payload(script_payload) if script_payload else ""
            if not text:
                text = _optional_text(_pick(output, "script_text", "text", "content"))
            if text:
                source_artifact = _coerce_mapping(output.get("artifact"))
                return (
                    text,
                    script_payload,
                    source_artifact,
                    _optional_text(_pick(script_payload, "source_kind")) or _optional_text(_pick(output, "source_kind")) or "scriptGeneration",
                )

    raise ValueError("PostProcessingModule requires scriptGeneration output or script text.")


def _extract_segments(
    inputs: Mapping[str, object],
    module_results: Mapping[str, object],
) -> list[JsonDict]:
    candidate = _pick(inputs, "narrative_segments", "segments")
    if candidate is None:
        script_result = module_results.get("scriptGeneration")
        if script_result is not None:
            output = getattr(script_result, "output", {})
            if isinstance(output, Mapping):
                candidate = output.get("narrative_segments") or output.get("segments")

    segments: list[JsonDict] = []
    if isinstance(candidate, Mapping):
        raw_segments = candidate.get("segments")
        if raw_segments is None and "segment" in candidate:
            raw_segments = candidate["segment"]
        candidate = raw_segments if raw_segments is not None else candidate

    if isinstance(candidate, Iterable) and not isinstance(candidate, (str, bytes, bytearray)):
        for index, segment in enumerate(candidate, start=1):
            if not isinstance(segment, Mapping):
                text = _optional_text(segment)
                if text:
                    segments.append({"order": index, "title": f"Section {index}", "role": "body", "text": text})
                continue
            normalized_text = _optional_text(
                _pick(segment, "text", "summary", "content", "body", "note")
            )
            if not normalized_text:
                continue
            order = int(_pick(segment, "order", default=index) or index)
            title = _optional_text(_pick(segment, "title", "heading")) or f"Section {order}"
            role = _optional_text(_pick(segment, "role")) or "body"
            duration_estimate = _pick(segment, "duration_estimate", "durationEstimate")
            payload: JsonDict = {
                "order": order,
                "title": title,
                "role": role,
                "text": normalized_text,
            }
            if isinstance(duration_estimate, (int, float)) and duration_estimate > 0:
                payload["duration_estimate"] = float(duration_estimate)
            segments.append(payload)
    return segments


def _fallback_segments(cleaned_text: str) -> list[JsonDict]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", cleaned_text) if part.strip()]
    if not paragraphs:
        return []
    segments: list[JsonDict] = []
    for index, paragraph in enumerate(paragraphs, start=1):
        segments.append(
            {
                "order": index,
                "title": {1: "Hook", 2: "Develop", 3: "Close"}.get(index, f"Section {index}"),
                "role": {1: "hook", 2: "body", 3: "close"}.get(index, "body"),
                "text": paragraph,
                "duration_estimate": round(max(6.0, len(paragraph.split()) * 1.35), 2),
            }
        )
    return segments


def _normalize_segments(
    raw_segments: list[JsonDict],
    *,
    cleanup_rules: Mapping[str, object],
    cleaned_text: str,
) -> list[JsonDict]:
    normalized_segments: list[JsonDict] = []
    for index, segment in enumerate(raw_segments, start=1):
        text = _normalize_script_text(
            _optional_text(segment.get("text")),
            cleanup_rules=cleanup_rules,
        )
        if not text:
            continue
        order = int(segment.get("order") or index)
        title = _optional_text(segment.get("title")) or f"Section {order}"
        role = _optional_text(segment.get("role")) or "body"
        duration_estimate = segment.get("duration_estimate")
        if not isinstance(duration_estimate, (int, float)) or duration_estimate <= 0:
            duration_estimate = round(max(6.0, len(text.split()) * 1.35), 2)
        normalized_segments.append(
            {
                "order": order,
                "title": title,
                "role": role,
                "text": text,
                "duration_estimate": float(duration_estimate),
            }
        )

    if normalized_segments:
        return normalized_segments

    fallback = _fallback_segments(cleaned_text)
    if fallback:
        return fallback

    return [
        {
            "order": 1,
            "title": "Section 1",
            "role": "body",
            "text": cleaned_text,
            "duration_estimate": round(max(6.0, len(cleaned_text.split()) * 1.35), 2),
        }
    ] if cleaned_text else []


def _render_cleaned_script(segments: list[JsonDict], cleaned_text: str) -> str:
    if not segments:
        return cleaned_text
    parts: list[str] = []
    for segment in segments:
        title = _optional_text(segment.get("title"))
        text = _optional_text(segment.get("text"))
        if title and text:
            parts.append(f"{title}: {text}")
        elif text:
            parts.append(text)
    return "\n\n".join(parts).strip() or cleaned_text


class PostProcessingModule:
    """Normalize script output for QA, voiceover and export stages."""

    definition = ModuleDefinition(
        name="postProcessing",
        input_schema={
            "type": "object",
            "properties": {
                "script": {"type": ["object", "string"]},
                "script_draft": {"type": ["object", "string"]},
                "script_text": {"type": "string"},
                "text": {"type": "string"},
                "draft_text": {"type": "string"},
                "narrative_segments": {"type": ["array", "object"]},
                "segments": {"type": ["array", "object"]},
                "cleanup_rules": {"type": "object"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "post_processed_script": {"type": "object"},
                "artifact": {"type": "object"},
                "cleaned_script": {"type": "string"},
                "normalized_segments": {"type": "array"},
                "original_script": {"type": "object"},
                "original_script_artifact": {"type": "object"},
                "workflow_snapshot": {"type": "object"},
                "source_kind": {"type": "string"},
            },
        },
        config_schema={
            "type": "object",
            "properties": {
                "cleanup_rules": {"type": "object"},
                "artifact_name": {"type": "string"},
            },
        },
        dependencies=(("scriptGeneration",),),
        enabled_by_default=True,
        disabled_behavior="fail",
        retry_limit=1,
        artifact_outputs=("post_processed_script.txt",),
        error_behavior="request_missing_fields",
    )

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        cleanup_rules: Mapping[str, object] | None = None,
        artifact_name: str = "post_processed_script.txt",
    ) -> None:
        self._artifact_store = artifact_store
        self._cleanup_rules = {**_DEFAULT_CLEANUP_RULES, **_coerce_mapping(cleanup_rules)}
        self._artifact_name = _optional_text(artifact_name) or "post_processed_script.txt"

    def execute(self, context: ModuleExecutionContext) -> ModuleResult:
        inputs = dict(context.inputs)
        script_text, original_script, source_artifact, source_kind = _extract_script_result(
            inputs,
            context.module_results,
        )
        cleanup_rules = {**self._cleanup_rules, **_coerce_mapping(_pick(inputs, "cleanup_rules"))}
        cleaned_text = _normalize_script_text(script_text, cleanup_rules=cleanup_rules)
        raw_segments = _extract_segments(inputs, context.module_results)
        normalized_segments = _normalize_segments(
            raw_segments,
            cleanup_rules=cleanup_rules,
            cleaned_text=cleaned_text,
        )
        if not cleaned_text and normalized_segments:
            cleaned_text = _render_cleaned_script(normalized_segments, cleaned_text)
        if not cleaned_text:
            raise ValueError("PostProcessingModule could not derive cleaned script text.")

        original_script_payload = original_script or {
            "workflow_run_id": context.workflow_run_id,
            "module_name": "scriptGeneration",
            "text": script_text,
            "source_kind": source_kind,
        }
        original_script_payload.setdefault("workflow_run_id", context.workflow_run_id)
        original_script_payload.setdefault("module_name", "scriptGeneration")
        original_script_payload.setdefault("text", script_text)
        original_script_payload.setdefault("source_kind", source_kind)

        post_processed_script = Script.create(
            workflow_run_id=context.workflow_run_id,
            text=cleaned_text,
            language=_optional_text(_pick(inputs, "language")) or "en",
            word_count=len(cleaned_text.split()),
        )
        post_processed_script.id = _stable_identifier(
            workflow_run_id=context.workflow_run_id,
            source_kind=source_kind,
            text=cleaned_text,
        )
        post_processed_script.created_at = _DETERMINISTIC_CREATED_AT

        post_processed_script_payload = {
            "id": post_processed_script.id,
            "workflow_run_id": post_processed_script.workflow_run_id,
            "text": post_processed_script.text,
            "cleaned_text": cleaned_text,
            "version": post_processed_script.version,
            "language": post_processed_script.language,
            "word_count": post_processed_script.word_count,
            "created_at": post_processed_script.created_at,
            "source_kind": source_kind,
            "cleanup_rules": cleanup_rules,
            "original_script": original_script_payload,
            "normalized_segments": normalized_segments,
        }

        artifact_manifest = self._artifact_store.save_artifact(
            self._artifact_name,
            cleaned_text,
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "post_processed_script",
                "source_kind": source_kind,
                "source_artifact_name": _optional_text(source_artifact.get("name")) if source_artifact else "",
                "source_artifact_storage_key": _optional_text(source_artifact.get("storage_key")) if source_artifact else "",
                "cleanup_rules": cleanup_rules,
            },
        )

        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=self.definition.artifact_outputs,
            output={
                "post_processed_script": post_processed_script_payload,
                "artifact": artifact_manifest.to_payload(),
                "cleaned_script": cleaned_text,
                "normalized_segments": normalized_segments,
                "original_script": original_script_payload,
                "original_script_artifact": source_artifact,
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


__all__ = ["PostProcessingModule"]

"""Deterministic script generation module."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256

from app.domain.content_brief import ContentBrief
from app.domain.narrative_segment import NarrativeSegment
from app.domain.script import Script
from app.domain.types import JsonDict
from app.providers.interfaces import LLMProvider
from app.storage.artifact_store import ArtifactStore
from app.workflow.execution import ModuleExecutionContext, ModuleResult
from app.workflow.module import ModuleDefinition


_DETERMINISTIC_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)
_SOURCE_PRIORITY = ("outline", "dossier", "research", "brief")
_SEMANTIC_MAPPING_KEYS = (
    "content_brief",
    "brief",
    "outline",
    "outline_plan",
    "outline_text",
    "research",
    "research_report",
    "research_summary",
    "dossier",
    "dossier_summary",
    "topic",
    "title",
    "headline",
    "subject",
    "objective",
    "summary",
    "content",
    "narrative",
    "text",
    "sections",
    "key_points",
    "points",
    "findings",
    "highlights",
    "constraints",
    "success_criteria",
)


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
        raise ValueError(f"ScriptGenerationModule {field_name} is required.")
    return text


def _coerce_mapping(value: object | None) -> JsonDict:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _coerce_text_list(value: object | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        values: Iterable[object] = value.values()
    elif isinstance(value, str):
        values = (value,)
    elif isinstance(value, Iterable):
        values = value
    else:
        values = (value,)

    items: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            nested = _coerce_text_list(
                _pick(item, "text", "summary", "title", "heading", "content", "note")
            )
            if nested:
                items.extend(nested)
                continue
        text = _optional_text(item)
        if text:
            items.append(text)
    return items


def _dedupe_texts(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = _optional_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _text_from_brief(brief: ContentBrief) -> list[str]:
    return _dedupe_texts(
        [
            brief.topic,
            brief.objective,
            brief.audience,
            *brief.constraints,
            *brief.success_criteria,
        ]
    )


def _context_texts_from_mapping(mapping: Mapping[str, object]) -> list[str]:
    texts: list[str] = []
    for key in _SEMANTIC_MAPPING_KEYS:
        if key not in mapping:
            continue
        value = mapping[key]
        if isinstance(value, ContentBrief):
            texts.extend(_text_from_brief(value))
            continue
        texts.extend(_coerce_text_list(value))
    return _dedupe_texts(texts)


def _candidate_contexts(
    inputs: Mapping[str, object],
    module_results: Mapping[str, object],
) -> list[tuple[str, list[str]]]:
    contexts: list[tuple[str, list[str]]] = []
    for source_kind in _SOURCE_PRIORITY:
        candidate = _pick(inputs, source_kind, f"{source_kind}_result", f"{source_kind}Result")
        if candidate is None:
            candidate_result = module_results.get(source_kind)
            if candidate_result is not None:
                candidate = getattr(candidate_result, "output", None)
        if candidate is None and source_kind == "brief":
            candidate = _pick(inputs, "content_brief", "contentBrief")
            if candidate is None:
                candidate_result = module_results.get("brief")
                if candidate_result is not None:
                    candidate = getattr(candidate_result, "output", None)

        if candidate is None:
            continue
        if isinstance(candidate, ContentBrief):
            contexts.append((source_kind, _text_from_brief(candidate)))
            continue
        if isinstance(candidate, Mapping):
            nested_brief = candidate.get("content_brief") or candidate.get("contentBrief")
            if isinstance(nested_brief, ContentBrief):
                contexts.append((source_kind, _text_from_brief(nested_brief)))
                continue
            if isinstance(nested_brief, Mapping):
                contexts.append((source_kind, _context_texts_from_mapping(nested_brief)))
                continue
            contexts.append((source_kind, _context_texts_from_mapping(candidate)))
            continue
        texts = _coerce_text_list(candidate)
        if texts:
            contexts.append((source_kind, _dedupe_texts(texts)))

    return contexts


def _sentence_chunks(text: str) -> list[str]:
    normalized = " ".join(text.strip().split())
    if not normalized:
        return []
    chunks = [chunk.strip() for chunk in re.split(r"(?<=[.!?])\s+", normalized) if chunk.strip()]
    return chunks or [normalized]


def _stable_identifier(prefix: str, *, workflow_run_id: str, source_kind: str, topic: str, text: str) -> str:
    signature = sha256(
        f"{prefix}:{workflow_run_id}:{source_kind}:{topic}:{text}".encode("utf-8")
    ).hexdigest()[:12]
    return f"{prefix}_{signature}"


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _segment_title(order: int) -> str:
    return {1: "Hook", 2: "Develop", 3: "Close"}.get(order, f"Section {order}")


def _segment_role(order: int) -> str:
    return {1: "hook", 2: "body", 3: "close"}.get(order, "body")


def _segment_text(order: int, topic: str, point: str) -> str:
    if order == 1:
        return f"Open with a direct hook about {topic}: {point}"
    if order == 2:
        return f"Develop the central idea around {point}"
    if order == 3:
        return f"Close by reinforcing {point}"
    return point


def _segment_duration_estimate(text: str) -> float:
    return round(max(6.0, len(text.split()) * 1.5), 2)


def _script_prompt(*, topic: str, source_kind: str, source_summary: str) -> str:
    return (
        f"Generate a deterministic {source_kind} script about {topic}. "
        f"Source summary: {source_summary}"
    )


class ScriptGenerationModule:
    """Generate deterministic scripts and narrative segments from source context."""

    definition = ModuleDefinition(
        name="scriptGeneration",
        input_schema={
            "type": "object",
            "properties": {
                "brief": {"type": ["object", "string"]},
                "content_brief": {"type": ["object", "string"]},
                "outline": {"type": ["object", "string"]},
                "research": {"type": ["object", "string"]},
                "dossier": {"type": ["object", "string"]},
                "topic": {"type": "string"},
                "language": {"type": "string"},
                "tone": {"type": "string"},
                "script_name": {"type": "string"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "script": {"type": "object"},
                "artifact": {"type": "object"},
                "script_json": {"type": "object"},
                "script_json_artifact": {"type": "object"},
                "narrative_segments": {"type": "object"},
                "narrative_segments_artifact": {"type": "object"},
                "workflow_snapshot": {"type": "object"},
                "source_kind": {"type": "string"},
            },
        },
        config_schema={
            "type": "object",
            "properties": {
                "default_language": {"type": "string"},
                "default_tone": {"type": "string"},
                "script_name": {"type": "string"},
                "script_json_name": {"type": "string"},
                "narrative_segments_name": {"type": "string"},
            },
        },
        dependencies=(("outline", "dossier", "research", "brief"),),
        enabled_by_default=True,
        disabled_behavior="fail",
        retry_limit=1,
        artifact_outputs=("script.txt", "script.json", "narrative_segments.json"),
        error_behavior="request_missing_fields",
    )

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        artifact_store: ArtifactStore,
        default_language: str = "en",
        default_tone: str = "neutral",
        script_name: str = "script.txt",
        script_json_name: str = "script.json",
        narrative_segments_name: str = "narrative_segments.json",
    ) -> None:
        self._llm_provider = llm_provider
        self._artifact_store = artifact_store
        self._default_language = _optional_text(default_language) or "en"
        self._default_tone = _optional_text(default_tone) or "neutral"
        self._script_name = _optional_text(script_name) or "script.txt"
        self._script_json_name = _optional_text(script_json_name) or "script.json"
        self._narrative_segments_name = (
            _optional_text(narrative_segments_name) or "narrative_segments.json"
        )

    def execute(self, context: ModuleExecutionContext) -> ModuleResult:
        inputs = dict(context.inputs)
        candidate_contexts = _candidate_contexts(inputs, context.module_results)
        if not candidate_contexts:
            raise ValueError(
                "ScriptGenerationModule requires brief, outline, research or dossier input."
            )

        source_kind, primary_texts = candidate_contexts[0]
        combined_texts = _dedupe_texts(
            text for _, texts in candidate_contexts for text in texts
        )
        topic = _optional_text(_pick(inputs, "topic")) or _optional_text(primary_texts[0])
        if not topic:
            topic = _optional_text(combined_texts[0])
        if not topic:
            raise ValueError("ScriptGenerationModule topic is required.")

        language = _optional_text(_pick(inputs, "language")) or self._default_language
        tone = _optional_text(_pick(inputs, "tone")) or self._default_tone
        brief_source = _pick(inputs, "brief", "content_brief", "contentBrief")
        if brief_source is None:
            brief_result = context.module_results.get("brief")
            if brief_result is not None:
                brief_source = getattr(brief_result, "output", None)

        brief_mapping: Mapping[str, object] | None = None
        if isinstance(brief_source, ContentBrief):
            objective = _optional_text(brief_source.objective)
            audience = _optional_text(brief_source.audience)
        elif isinstance(brief_source, Mapping):
            nested_brief = brief_source.get("content_brief") or brief_source.get("contentBrief")
            if isinstance(nested_brief, ContentBrief):
                brief_mapping = {
                    "objective": nested_brief.objective,
                    "audience": nested_brief.audience,
                }
            elif isinstance(nested_brief, Mapping):
                brief_mapping = nested_brief
            else:
                brief_mapping = brief_source
            objective = _optional_text(_pick(brief_mapping, "objective")) if brief_mapping else ""
            audience = _optional_text(_pick(brief_mapping, "audience")) if brief_mapping else ""
        else:
            objective = ""
            audience = ""

        objective = _optional_text(_pick(inputs, "objective")) or objective
        audience = _optional_text(_pick(inputs, "audience")) or audience

        source_summary = " ".join(combined_texts[:4]) or topic
        generation_ref = _optional_text(
            self._llm_provider.generate_text(
                _script_prompt(topic=topic, source_kind=source_kind, source_summary=source_summary),
                context={
                    "workflow_run_id": context.workflow_run_id,
                    "workflow_config_id": context.workflow_config_id,
                    "module_name": self.definition.name,
                    "source_kind": source_kind,
                    "topic": topic,
                    "source_summary": source_summary,
                },
            )
        )

        if not generation_ref:
            generation_ref = _stable_identifier(
                "script_generation",
                workflow_run_id=context.workflow_run_id,
                source_kind=source_kind,
                topic=topic,
                text=source_summary,
            )

        segment_points = primary_texts[:3] or combined_texts[:3] or [topic]
        if len(segment_points) == 1:
            fallback_sentence = _sentence_chunks(source_summary)[0]
            if fallback_sentence not in segment_points:
                segment_points.append(fallback_sentence)
        if len(segment_points) == 2:
            segment_points.append(f"End by restating the core value of {topic}")
        segment_points = _dedupe_texts(segment_points)[:3]
        if not segment_points:
            segment_points = [topic]

        segment_models: list[NarrativeSegment] = []
        segment_payloads: list[JsonDict] = []
        for order, point in enumerate(segment_points, start=1):
            text = _segment_text(order, topic, point)
            segment = NarrativeSegment.create(
                workflow_run_id=context.workflow_run_id,
                order=order,
                title=_segment_title(order),
                text=text,
                role=_segment_role(order),
                duration_estimate=_segment_duration_estimate(text),
            )
            segment.id = _stable_identifier(
                "narrative_segment",
                workflow_run_id=context.workflow_run_id,
                source_kind=source_kind,
                topic=topic,
                text=text,
            )
            segment.created_at = _DETERMINISTIC_CREATED_AT
            segment_payload = asdict(segment)
            segment_payload.update(
                {
                    "source_kind": source_kind,
                    "generation_ref": generation_ref,
                }
            )
            segment_models.append(segment)
            segment_payloads.append(segment_payload)

        script_text = "\n\n".join(
            f"{segment['title']}: {segment['text']}" for segment in segment_payloads
        )
        word_count = len(script_text.split())
        script = Script.create(
            workflow_run_id=context.workflow_run_id,
            text=script_text,
            language=language,
            word_count=word_count,
        )
        script.id = _stable_identifier(
            "script",
            workflow_run_id=context.workflow_run_id,
            source_kind=source_kind,
            topic=topic,
            text=script_text,
        )
        script.created_at = _DETERMINISTIC_CREATED_AT

        script_payload = asdict(script)
        script_payload.update(
            {
                "source_kind": source_kind,
                "generation_ref": generation_ref,
                "topic": topic,
                "objective": objective,
                "audience": audience,
                "tone": tone,
                "source_summary": source_summary,
                "segments": segment_payloads,
            }
        )

        script_manifest = self._artifact_store.save_artifact(
            self._script_name,
            script_text,
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "script",
                "source_kind": source_kind,
                "generation_ref": generation_ref,
                "topic": topic,
            },
        )
        script_json_payload = {
            "workflow_run_id": context.workflow_run_id,
            "module_name": self.definition.name,
            "source_kind": source_kind,
            "generation_ref": generation_ref,
            "topic": topic,
            "language": language,
            "tone": tone,
            "objective": objective,
            "audience": audience,
            "script": script_payload,
            "script_text": script_text,
        }
        script_json_manifest = self._artifact_store.save_artifact(
            self._script_json_name,
            json.dumps(script_json_payload, indent=2, sort_keys=True, default=_json_default),
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "script",
                "source_kind": source_kind,
                "generation_ref": generation_ref,
                "topic": topic,
                "format": "json",
            },
        )
        narrative_segments_payload = {
            "workflow_run_id": context.workflow_run_id,
            "module_name": self.definition.name,
            "source_kind": source_kind,
            "generation_ref": generation_ref,
            "topic": topic,
            "segments": segment_payloads,
        }
        narrative_segments_manifest = self._artifact_store.save_artifact(
            self._narrative_segments_name,
            json.dumps(
                narrative_segments_payload,
                indent=2,
                sort_keys=True,
                default=_json_default,
            ),
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "narrative_segments",
                "source_kind": source_kind,
                "generation_ref": generation_ref,
                "topic": topic,
            },
        )

        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=self.definition.artifact_outputs,
            output={
                "script": script_payload,
                "artifact": script_manifest.to_payload(),
                "script_json": script_json_payload,
                "script_json_artifact": script_json_manifest.to_payload(),
                "narrative_segments": narrative_segments_payload,
                "narrative_segments_artifact": narrative_segments_manifest.to_payload(),
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

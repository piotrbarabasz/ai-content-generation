"""Deterministic research module."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from hashlib import sha256

from app.domain.content_brief import ContentBrief
from app.domain.types import JsonDict
from app.providers.interfaces import LLMProvider
from app.storage.artifact_store import ArtifactStore
from app.workflow.execution import ModuleExecutionContext, ModuleResult
from app.workflow.module import ModuleDefinition


_DETERMINISTIC_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)


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


def _flag_is_disabled(value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return not value
    return _optional_text(value).lower() in {"0", "false", "no", "off", "disabled"}


def _coerce_text(value: object | None, *, field_name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"ResearchModule {field_name} is required.")
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


def _brief_source(payload: object | None) -> JsonDict:
    if payload is None:
        return {}
    if isinstance(payload, ContentBrief):
        return {
            "project_id": payload.project_id,
            "topic": payload.topic,
            "objective": payload.objective,
            "audience": payload.audience,
            "constraints": list(payload.constraints),
            "duration_profile": payload.duration_profile.value,
            "success_criteria": list(payload.success_criteria),
        }
    if isinstance(payload, Mapping):
        return dict(payload)
    if isinstance(payload, str):
        return {"topic": payload}
    return {}


def _source_items(value: object | None) -> list[JsonDict]:
    if value is None:
        return []
    if isinstance(value, ContentBrief):
        return [
            {
                "title": value.topic,
                "summary": value.objective or value.topic,
                "source_ref": value.project_id,
                "kind": "brief",
            }
        ]
    if isinstance(value, Mapping):
        nested = _pick(value, "sources", "source_manifest", "sourceManifest", "items", "documents")
        if nested is not None:
            return _source_items(nested)
        return [dict(value)]
    if isinstance(value, str):
        text = _optional_text(value)
        if not text:
            return []
        return [{"title": text, "summary": text}]
    if isinstance(value, Iterable):
        items: list[JsonDict] = []
        for item in value:
            if isinstance(item, ContentBrief):
                items.extend(_source_items(item))
                continue
            if isinstance(item, Mapping):
                items.append(dict(item))
                continue
            text = _optional_text(item)
            if text:
                items.append({"title": text, "summary": text})
        return items
    text = _optional_text(value)
    return [{"title": text, "summary": text}] if text else []


def _topic_from_text(text: str) -> str:
    normalized = " ".join(text.strip().split())
    if not normalized:
        raise ValueError("ResearchModule topic, brief or source_manifest is required.")
    for separator in (".", "!", "?"):
        if separator in normalized:
            head = normalized.split(separator, 1)[0].strip()
            if head:
                return head[:120]
    return normalized[:120]


def _stable_identifier(
    prefix: str,
    *,
    workflow_run_id: str,
    source_kind: str,
    topic: str,
    source_summary: str,
) -> str:
    signature = sha256(
        f"{prefix}:{workflow_run_id}:{source_kind}:{topic}:{source_summary}".encode("utf-8")
    ).hexdigest()[:12]
    return f"{prefix}_{signature}"


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _source_ref(payload: Mapping[str, object], order: int, topic: str) -> str:
    explicit = _optional_text(
        _pick(payload, "source_ref", "sourceRef", "source_id", "sourceId", "id", "url")
    )
    if explicit:
        return explicit
    title = _optional_text(_pick(payload, "title", "name", "headline"))
    if title:
        return f"{topic}:{order}:{title}"
    return f"{topic}:{order}"


def _source_title(payload: Mapping[str, object], order: int, topic: str) -> str:
    title = _optional_text(_pick(payload, "title", "name", "headline"))
    if title:
        return title
    return {1: "Primary context", 2: "Supporting context", 3: "Additional context"}.get(
        order,
        f"Source {order}",
    )


def _source_summary(payload: Mapping[str, object], topic: str) -> str:
    summary = _optional_text(_pick(payload, "summary", "text", "description", "note"))
    if summary:
        return summary
    title = _optional_text(_pick(payload, "title", "name", "headline"))
    if title:
        return title
    return topic


class ResearchModule:
    """Generate deterministic research artifacts from topic and source inputs."""

    definition = ModuleDefinition(
        name="research",
        input_schema={
            "type": "object",
            "properties": {
                "allow_research": {"type": "boolean"},
                "max_sources": {"type": "integer"},
                "provider": {"type": "string"},
                "topic": {"type": "string"},
                "brief": {"type": ["object", "string"]},
                "content_brief": {"type": ["object", "string"]},
                "source_manifest": {"type": ["array", "object", "string"]},
                "sourceManifest": {"type": ["array", "object", "string"]},
                "sources": {"type": ["array", "object", "string"]},
                "workflow_run": {"type": "object"},
                "generation_job": {"type": "object"},
                "workflow_run_id": {"type": "string"},
                "generation_job_id": {"type": "string"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "research": {"type": "object"},
                "artifact": {"type": "object"},
                "workflow_snapshot": {"type": "object"},
                "source_kind": {"type": "string"},
                "workflow_run": {"type": "object"},
                "generation_job": {"type": "object"},
            },
        },
        config_schema={
            "type": "object",
            "properties": {
                "allow_research": {"type": "boolean"},
                "max_sources": {"type": "integer"},
                "provider": {"type": "string"},
            },
        },
        enabled_by_default=False,
        disabled_behavior="skip",
        retry_limit=1,
        artifact_outputs=("research.json",),
        error_behavior="request_missing_fields",
    )

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        artifact_store: ArtifactStore,
        default_provider: str = "mock",
        default_max_sources: int = 3,
    ) -> None:
        self._llm_provider = llm_provider
        self._artifact_store = artifact_store
        self._default_provider = _optional_text(default_provider) or "mock"
        self._default_max_sources = max(1, int(default_max_sources))

    def execute(self, context: ModuleExecutionContext) -> ModuleResult:
        inputs = dict(context.inputs)
        allow_research = inputs.get("allow_research")
        if _flag_is_disabled(allow_research):
            return ModuleResult(
                module_name=self.definition.name,
                status="skipped",
                skipped_reason="disabled",
            )

        brief_source = _brief_source(_pick(inputs, "brief", "content_brief", "contentBrief"))
        source_manifest = _source_items(
            _pick(
                inputs,
                "source_manifest",
                "sourceManifest",
                "sources",
                "research_sources",
                "researchSources",
            )
        )

        explicit_topic = _optional_text(_pick(inputs, "topic"))
        topic = explicit_topic or _optional_text(brief_source.get("topic"))
        source_kind = "topic"

        if topic:
            source_kind = "brief" if brief_source and not explicit_topic else "topic"
        elif source_manifest:
            topic = _topic_from_text(
                " ".join(
                    part
                    for part in (
                        _optional_text(source_manifest[0].get("title")),
                        _optional_text(source_manifest[0].get("summary")),
                        _optional_text(source_manifest[0].get("text")),
                    )
                    if part
                )
            )
            source_kind = "source_manifest"
        elif brief_source:
            topic = _topic_from_text(
                " ".join(
                    part
                    for part in (
                        _optional_text(brief_source.get("objective")),
                        _optional_text(brief_source.get("audience")),
                        _optional_text(brief_source.get("constraints")),
                    )
                    if part
                )
                or _optional_text(brief_source.get("topic"))
            )
            source_kind = "brief"
        else:
            raise ValueError("ResearchModule topic, brief or source_manifest is required.")

        normalized_sources = [_coerce_mapping(item) for item in source_manifest]
        if not normalized_sources and brief_source:
            normalized_sources = [
                {
                    "title": _optional_text(brief_source.get("topic")) or topic,
                    "summary": _optional_text(brief_source.get("objective")) or topic,
                    "source_ref": brief_source.get("project_id") or context.workflow_run_id,
                    "kind": "brief",
                }
            ]

        max_sources = inputs.get("max_sources")
        if max_sources is None:
            max_sources = self._default_max_sources
        try:
            max_sources_int = max(1, int(max_sources))
        except (TypeError, ValueError):
            max_sources_int = self._default_max_sources
        normalized_sources = normalized_sources[:max_sources_int]

        source_notes: list[JsonDict] = []
        source_summaries: list[str] = []
        for order, payload in enumerate(normalized_sources, start=1):
            title = _source_title(payload, order, topic)
            summary = _source_summary(payload, topic)
            source_summaries.append(summary)
            source_notes.append(
                {
                    "order": order,
                    "title": title,
                    "summary": summary,
                    "source_ref": _source_ref(payload, order, topic),
                    "kind": _optional_text(_pick(payload, "kind", "source_kind")) or source_kind,
                }
            )

        if not source_notes:
            fallback_summary = _optional_text(brief_source.get("objective")) or topic
            source_notes.append(
                {
                    "order": 1,
                    "title": "Primary context",
                    "summary": fallback_summary,
                    "source_ref": context.workflow_run_id,
                    "kind": source_kind,
                }
            )
            source_summaries.append(fallback_summary)

        source_summary = " ".join(_dedupe_texts(source_summaries)[:4]) or topic
        provider_name = _optional_text(_pick(inputs, "provider")) or self._default_provider
        research_ref = _optional_text(
            self._llm_provider.generate_text(
                f"Research topic: {topic}. Source summary: {source_summary}",
                context={
                    "workflow_run_id": context.workflow_run_id,
                    "workflow_config_id": context.workflow_config_id,
                    "module_name": self.definition.name,
                    "source_kind": source_kind,
                    "topic": topic,
                    "source_summary": source_summary,
                    "provider": provider_name,
                },
            )
        )
        if not research_ref:
            research_ref = _stable_identifier(
                "research",
                workflow_run_id=context.workflow_run_id,
                source_kind=source_kind,
                topic=topic,
                source_summary=source_summary,
            )

        research_id = _stable_identifier(
            "research",
            workflow_run_id=context.workflow_run_id,
            source_kind=source_kind,
            topic=topic,
            source_summary=source_summary,
        )
        dossier_context = {
            "topic": topic,
            "source_kind": source_kind,
            "source_count": len(source_notes),
            "key_findings": [note["summary"] for note in source_notes[:3]],
            "recommended_angle": source_notes[0]["summary"],
            "research_ref": research_ref,
        }

        workflow_run_payload = _coerce_mapping(_pick(inputs, "workflow_run", "workflowRun"))
        if not workflow_run_payload:
            workflow_run_payload = {
                "id": context.workflow_run_id,
                "workflow_config_id": context.workflow_config_id,
                "status": "running",
                "current_stage": self.definition.name,
            }
        workflow_run_payload.update(
            {
                "id": _optional_text(workflow_run_payload.get("id")) or context.workflow_run_id,
                "workflow_config_id": _optional_text(
                    workflow_run_payload.get("workflow_config_id")
                    or workflow_run_payload.get("workflowConfigId")
                )
                or context.workflow_config_id,
                "current_stage": _optional_text(
                    workflow_run_payload.get("current_stage")
                    or workflow_run_payload.get("currentStage")
                )
                or self.definition.name,
                "artifact_ids": list(
                    dict.fromkeys(
                        [
                            *(
                                str(value)
                                for value in _coerce_text_list(
                                    workflow_run_payload.get("artifact_ids")
                                    or workflow_run_payload.get("artifactIds")
                                )
                            ),
                            *context.artifact_ids,
                            "research.json",
                        ]
                    )
                ),
            }
        )

        generation_job_payload = _coerce_mapping(_pick(inputs, "generation_job", "generationJob"))
        if not generation_job_payload:
            generation_job_payload = {
                "id": _stable_identifier(
                    "generation_job",
                    workflow_run_id=context.workflow_run_id,
                    source_kind=source_kind,
                    topic=topic,
                    source_summary=source_summary,
                ),
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "status": "completed",
                "attempt": 1,
                "retry_count": 0,
            }
        generation_job_payload.update(
            {
                "id": _optional_text(generation_job_payload.get("id"))
                or _stable_identifier(
                    "generation_job",
                    workflow_run_id=context.workflow_run_id,
                    source_kind=source_kind,
                    topic=topic,
                    source_summary=source_summary,
                ),
                "workflow_run_id": _optional_text(
                    generation_job_payload.get("workflow_run_id")
                    or generation_job_payload.get("workflowRunId")
                )
                or context.workflow_run_id,
                "module_name": _optional_text(
                    generation_job_payload.get("module_name")
                    or generation_job_payload.get("moduleName")
                )
                or self.definition.name,
                "status": _optional_text(generation_job_payload.get("status")) or "completed",
                "attempt": int(generation_job_payload.get("attempt") or 1),
                "retry_count": int(generation_job_payload.get("retry_count") or 0),
                "output_artifact_ids": list(
                    dict.fromkeys(
                        [
                            *(
                                str(value)
                                for value in _coerce_text_list(
                                    generation_job_payload.get("output_artifact_ids")
                                    or generation_job_payload.get("outputArtifactIds")
                                )
                            ),
                            "research.json",
                        ]
                    )
                ),
            }
        )

        research_payload = {
            "research_id": research_id,
            "workflow_run_id": context.workflow_run_id,
            "workflow_config_id": context.workflow_config_id,
            "module_name": self.definition.name,
            "provider": provider_name,
            "topic": topic,
            "source_kind": source_kind,
            "source_summary": source_summary,
            "source_manifest": normalized_sources,
            "research_notes": source_notes,
            "dossier_context": dossier_context,
            "research_ref": research_ref,
            "workflow_run": workflow_run_payload,
            "generation_job": generation_job_payload,
            "created_at": _DETERMINISTIC_CREATED_AT,
        }

        artifact = self._artifact_store.save_artifact(
            "research.json",
            json.dumps(research_payload, indent=2, sort_keys=True, default=_json_default),
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "research",
                "topic": topic,
                "source_kind": source_kind,
                "research_ref": research_ref,
                "provider": provider_name,
            },
        )

        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=self.definition.artifact_outputs,
            output={
                "research": research_payload,
                "artifact": artifact.to_payload(),
                "workflow_snapshot": {
                    "workflow_run_id": context.workflow_run_id,
                    "workflow_config_id": context.workflow_config_id,
                    "module_name": context.module_name,
                    "enabled_modules": list(context.enabled_modules),
                    "disabled_modules": list(context.disabled_modules),
                },
                "source_kind": source_kind,
                "workflow_run": workflow_run_payload,
                "generation_job": generation_job_payload,
            },
        )

"""Deterministic dossier generation module."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from app.domain.content_brief import ContentBrief
from app.domain.types import JsonDict
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


def _coerce_text(value: object | None, *, field_name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"DossierModule {field_name} is required.")
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


def _flag_is_disabled(value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return not value
    return _optional_text(value).lower() in {"0", "false", "no", "off", "disabled"}


def _topic_from_text(text: str) -> str:
    normalized = " ".join(text.strip().split())
    if not normalized:
        raise ValueError("DossierModule research, dossier or brief input is required.")
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


def _source_notes(value: object | None) -> list[JsonDict]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        nested = _pick(value, "research_notes", "notes", "findings", "source_manifest", "sourceManifest")
        if nested is not None:
            return _source_notes(nested)
        return [dict(value)]
    if isinstance(value, str):
        text = _optional_text(value)
        return [{"title": text, "summary": text}] if text else []
    if isinstance(value, Iterable):
        items: list[JsonDict] = []
        for item in value:
            if isinstance(item, Mapping):
                items.append(dict(item))
                continue
            text = _optional_text(item)
            if text:
                items.append({"title": text, "summary": text})
        return items
    text = _optional_text(value)
    return [{"title": text, "summary": text}] if text else []


def _extract_people(notes: Iterable[Mapping[str, object]], source_payload: Mapping[str, object]) -> list[str]:
    explicit = _coerce_text_list(
        _pick(source_payload, "key_people", "keyPeople", "people", "people_list", "characters")
    )
    if explicit:
        return _dedupe_texts(explicit)

    inferred: list[str] = []
    for note in notes:
        for key in ("people", "key_people", "subjects", "names"):
            inferred.extend(_coerce_text_list(note.get(key)))
    return _dedupe_texts(inferred)


def _extract_places(notes: Iterable[Mapping[str, object]], source_payload: Mapping[str, object]) -> list[str]:
    explicit = _coerce_text_list(
        _pick(source_payload, "key_places", "keyPlaces", "places", "locations")
    )
    if explicit:
        return _dedupe_texts(explicit)

    inferred: list[str] = []
    for note in notes:
        for key in ("places", "key_places", "locations", "settings"):
            inferred.extend(_coerce_text_list(note.get(key)))
    return _dedupe_texts(inferred)


def _extract_facts(notes: Iterable[Mapping[str, object]], source_payload: Mapping[str, object]) -> tuple[list[str], list[str]]:
    confirmed: list[str] = []
    disputed: list[str] = []

    for field_name in ("confirmed_facts", "facts", "key_facts", "findings"):
        confirmed.extend(_coerce_text_list(_pick(source_payload, field_name)))
    for field_name in ("disputed_facts", "open_questions", "unknowns"):
        disputed.extend(_coerce_text_list(_pick(source_payload, field_name)))

    for note in notes:
        note_text = _optional_text(
            _pick(note, "summary", "text", "content", "note", "finding", "title")
        )
        if not note_text:
            continue
        marker = " ".join(
            part
            for part in (
                _optional_text(note.get("status")),
                _optional_text(note.get("confidence")),
                _optional_text(note.get("quality")),
            )
            if part
        ).lower()
        if any(token in marker for token in ("disputed", "uncertain", "question", "conflict")):
            disputed.append(note_text)
        else:
            confirmed.append(note_text)

    return _dedupe_texts(confirmed), _dedupe_texts(disputed)


def _extract_timeline(notes: Iterable[Mapping[str, object]], topic: str) -> list[JsonDict]:
    timeline: list[JsonDict] = []
    for order, note in enumerate(notes, start=1):
        title = _optional_text(_pick(note, "title", "heading", "name")) or f"Milestone {order}"
        summary = _optional_text(_pick(note, "summary", "text", "content", "note")) or title
        timeline.append(
            {
                "order": order,
                "title": title,
                "summary": summary,
                "source_ref": _optional_text(
                    _pick(note, "source_ref", "sourceRef", "source_id", "sourceId", "id", "url")
                )
                or f"{topic}:{order}",
                "status": _optional_text(_pick(note, "status")) or "confirmed",
            }
        )
    return timeline


def _extract_research_payload(inputs: Mapping[str, object], module_results: Mapping[str, object]) -> JsonDict:
    explicit = _pick(
        inputs,
        "research",
        "research_output",
        "researchOutput",
        "dossier",
        "dossier_input",
        "dossierInput",
    )
    if isinstance(explicit, ContentBrief):
        return _brief_source(explicit)
    if isinstance(explicit, Mapping):
        nested = _pick(explicit, "research", "research_output", "researchOutput", "dossier")
        if isinstance(nested, Mapping):
            return dict(nested)
        return dict(explicit)

    research_result = module_results.get("research")
    if research_result is not None:
        output = getattr(research_result, "output", {})
        if isinstance(output, Mapping):
            nested_research = _pick(output, "research", "research_output", "researchOutput")
            if isinstance(nested_research, Mapping):
                return dict(nested_research)
            return dict(output)

    return {}


class DossierModule:
    """Normalize research output into a structured dossier artifact."""

    definition = ModuleDefinition(
        name="dossier",
        input_schema={
            "type": "object",
            "properties": {
                "allow_dossier": {"type": "boolean"},
                "dossier_enabled": {"type": "boolean"},
                "enable_dossier": {"type": "boolean"},
                "brief": {"type": ["object", "string"]},
                "research": {"type": ["object", "array", "string"]},
                "research_output": {"type": ["object", "array", "string"]},
                "dossier": {"type": ["object", "array", "string"]},
                "dossier_input": {"type": ["object", "array", "string"]},
                "style_profile": {"type": "string"},
                "detail_level": {"type": "string"},
                "include_confidence": {"type": "boolean"},
                "citation_required": {"type": "boolean"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "dossier": {"type": "object"},
                "artifact": {"type": "object"},
                "workflow_snapshot": {"type": "object"},
                "source_kind": {"type": "string"},
                "workflow_run": {"type": "object"},
                "generation_job": {"type": "object"},
                "key_people": {"type": "array", "items": {"type": "string"}},
                "key_places": {"type": "array", "items": {"type": "string"}},
                "confirmed_facts": {"type": "array", "items": {"type": "string"}},
                "disputed_facts": {"type": "array", "items": {"type": "string"}},
                "timeline": {"type": "array"},
            },
        },
        config_schema={
            "type": "object",
            "properties": {
                "style_profile": {"type": "string"},
                "detail_level": {"type": "string"},
                "include_confidence": {"type": "boolean"},
                "citation_required": {"type": "boolean"},
            },
        },
        dependencies=(("research",),),
        disabled_behavior="skip",
        enabled_by_default=False,
        retry_limit=1,
        artifact_outputs=("dossier.json",),
        error_behavior="request_missing_fields",
    )

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        default_detail_level: str = "standard",
        default_style_profile: str = "neutral",
    ) -> None:
        self._artifact_store = artifact_store
        self._default_detail_level = _optional_text(default_detail_level) or "standard"
        self._default_style_profile = _optional_text(default_style_profile) or "neutral"

    def execute(self, context: ModuleExecutionContext) -> ModuleResult:
        inputs = dict(context.inputs)
        if _flag_is_disabled(
            _pick(inputs, "allow_dossier", "dossier_enabled", "enable_dossier")
        ):
            return ModuleResult(
                module_name=self.definition.name,
                status="skipped",
                skipped_reason="disabled",
            )

        research_payload = _extract_research_payload(inputs, context.module_results)
        brief_source = _brief_source(_pick(inputs, "brief", "content_brief", "contentBrief"))

        topic = _optional_text(
            _pick(
                research_payload,
                "topic",
                default=_optional_text(_pick(brief_source, "topic")),
            )
        )
        if not topic:
            topic = _topic_from_text(
                " ".join(
                    part
                    for part in (
                        _optional_text(_pick(research_payload, "source_summary", "summary")),
                        _optional_text(_pick(brief_source, "objective")),
                        _optional_text(_pick(brief_source, "audience")),
                    )
                    if part
                )
            )

        research_notes = _source_notes(
            _pick(
                research_payload,
                "research_notes",
                "notes",
                "findings",
                "source_manifest",
                "sourceManifest",
            )
        )
        if not research_notes:
            research_notes = _source_notes(
                _pick(research_payload, "dossier_context", "dossierContext")
            )

        source_kind = _optional_text(_pick(research_payload, "source_kind", "sourceKind")) or "research"
        source_summary = _optional_text(
            _pick(research_payload, "source_summary", "sourceSummary")
        )
        if not source_summary:
            source_summary = " ".join(
                _dedupe_texts(
                    _coerce_text_list(
                        _pick(research_payload, "research_notes", "notes", "findings")
                    )
                )[:4]
            )
        if not source_summary:
            source_summary = topic

        if not research_payload and not brief_source:
            raise ValueError("DossierModule research or dossier input is required.")

        detail_level = _optional_text(_pick(inputs, "detail_level", "detailLevel"))
        if not detail_level:
            detail_level = self._default_detail_level

        style_profile = _optional_text(_pick(inputs, "style_profile", "styleProfile"))
        if not style_profile:
            style_profile = self._default_style_profile

        include_confidence = _pick(inputs, "include_confidence", "includeConfidence")
        if not isinstance(include_confidence, bool):
            include_confidence = False

        citation_required = _pick(inputs, "citation_required", "citationRequired")
        if not isinstance(citation_required, bool):
            citation_required = False

        dossier_context = _coerce_mapping(_pick(research_payload, "dossier_context", "dossierContext"))
        key_people = _extract_people(research_notes, research_payload)
        key_places = _extract_places(research_notes, research_payload)
        confirmed_facts, disputed_facts = _extract_facts(research_notes, research_payload)
        timeline = _extract_timeline(research_notes, topic)

        if not confirmed_facts:
            confirmed_facts = _dedupe_texts(
                [
                    _optional_text(_pick(dossier_context, "recommended_angle")),
                    _optional_text(_pick(research_payload, "source_summary", "sourceSummary")),
                    topic,
                ]
            )

        dossier_id = _stable_identifier(
            "dossier",
            workflow_run_id=context.workflow_run_id,
            source_kind=source_kind,
            topic=topic,
            source_summary=source_summary,
        )
        research_ref = _optional_text(
            _pick(research_payload, "research_ref", "researchRef")
        ) or _stable_identifier(
            "research",
            workflow_run_id=context.workflow_run_id,
            source_kind=source_kind,
            topic=topic,
            source_summary=source_summary,
        )

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
                            "dossier.json",
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
                            "dossier.json",
                        ]
                    )
                ),
            }
        )

        dossier_record: JsonDict = {
            "dossier_id": dossier_id,
            "workflow_run_id": context.workflow_run_id,
            "workflow_config_id": context.workflow_config_id,
            "module_name": self.definition.name,
            "topic": topic,
            "source_kind": source_kind,
            "research_ref": research_ref,
            "source_summary": source_summary,
            "detail_level": detail_level,
            "style_profile": style_profile,
            "include_confidence": include_confidence,
            "citation_required": citation_required,
            "key_people": key_people,
            "key_places": key_places,
            "confirmed_facts": confirmed_facts,
            "disputed_facts": disputed_facts,
            "timeline": timeline,
            "research_notes": research_notes,
            "dossier_context": dossier_context,
            "workflow_run": workflow_run_payload,
            "generation_job": generation_job_payload,
            "created_at": _DETERMINISTIC_CREATED_AT,
        }

        artifact_payload = {
            **dossier_record,
            "dossier": {
                key: value
                for key, value in dossier_record.items()
                if key
                not in {
                    "workflow_run",
                    "generation_job",
                    "created_at",
                }
            },
        }

        artifact = self._artifact_store.save_artifact(
            "dossier.json",
            json.dumps(artifact_payload, indent=2, sort_keys=True, default=_json_default),
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "dossier",
                "topic": topic,
                "source_kind": source_kind,
                "research_ref": research_ref,
                "detail_level": detail_level,
                "style_profile": style_profile,
            },
        )

        workflow_snapshot = {
            "workflow_run_id": context.workflow_run_id,
            "workflow_config_id": context.workflow_config_id,
            "module_name": context.module_name,
            "enabled_modules": list(context.enabled_modules),
            "disabled_modules": list(context.disabled_modules),
        }

        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=self.definition.artifact_outputs,
            output={
                "dossier": artifact_payload["dossier"],
                "artifact": artifact.to_payload(),
                "workflow_snapshot": workflow_snapshot,
                "source_kind": source_kind,
                "workflow_run": workflow_run_payload,
                "generation_job": generation_job_payload,
                "key_people": key_people,
                "key_places": key_places,
                "confirmed_facts": confirmed_facts,
                "disputed_facts": disputed_facts,
                "timeline": timeline,
            },
        )

"""Deterministic long-form outline module."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from hashlib import sha256

from app.domain.content_brief import ContentBrief
from app.domain.enums import DurationProfile
from app.domain.types import JsonDict
from app.storage.artifact_store import ArtifactStore
from app.workflow.execution import ModuleExecutionContext, ModuleResult
from app.workflow.module import ModuleDefinition


_DETERMINISTIC_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)
_DEFAULT_DURATION_PROFILE = DurationProfile.EIGHT_FIFTEEN_MINUTES.value
_DEFAULT_SCENE_COUNT = 5


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
        raise ValueError(f"OutlineModule {field_name} is required.")
    return text


def _coerce_int(value: object | None, *, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return fallback


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


def _extract_payload(inputs: Mapping[str, object], module_results: Mapping[str, object]) -> tuple[str, JsonDict, str]:
    for module_name in ("dossier", "research", "brief"):
        result = module_results.get(module_name)
        if result is None:
            continue
        output = getattr(result, "output", None)
        if isinstance(output, Mapping):
            nested = _pick(output, module_name, "outline", "dossier", "research", "brief")
            if isinstance(nested, Mapping):
                return module_name, dict(nested), module_name
            return module_name, dict(output), module_name

    explicit_topic = _optional_text(_pick(inputs, "topic"))
    if explicit_topic:
        return "topic", {"topic": explicit_topic}, "topic"

    for module_name in ("dossier", "research", "brief"):
        explicit = _pick(inputs, module_name, f"{module_name}_result", f"{module_name}Result")
        if isinstance(explicit, ContentBrief):
            return "brief", _brief_source(explicit), "brief"
        if isinstance(explicit, Mapping):
            nested = _pick(explicit, "outline", "research", "dossier", "brief")
            if isinstance(nested, Mapping):
                explicit = nested
            source_kind = _source_kind_from_mapping(explicit)
            return source_kind, dict(explicit), source_kind

    return "topic", {}, "topic"


def _source_kind_from_mapping(mapping: Mapping[str, object]) -> str:
    if "dossier_id" in mapping or "disputed_facts" in mapping or "key_people" in mapping:
        return "dossier"
    if "research_id" in mapping or "research_notes" in mapping or "dossier_context" in mapping:
        return "research"
    if "brief_id" in mapping or "objective" in mapping or "success_criteria" in mapping:
        return "brief"
    return "topic"


def _topic_from_text(text: str) -> str:
    normalized = " ".join(text.strip().split())
    if not normalized:
        raise ValueError("OutlineModule topic, brief, research or dossier input is required.")
    for separator in (".", "!", "?"):
        if separator in normalized:
            head = normalized.split(separator, 1)[0].strip()
            if head:
                return head[:120]
    return normalized[:120]


def _stable_identifier(*, workflow_run_id: str, source_kind: str, topic: str, outline_text: str) -> str:
    signature = sha256(
        f"outline:{workflow_run_id}:{source_kind}:{topic}:{outline_text}".encode("utf-8")
    ).hexdigest()[:12]
    return f"outline_{signature}"


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _duration_profile_value(inputs: Mapping[str, object], source: Mapping[str, object]) -> str:
    return (
        _optional_text(_pick(inputs, "duration_profile", "durationProfile"))
        or _optional_text(_pick(source, "duration_profile", "durationProfile"))
        or _DEFAULT_DURATION_PROFILE
    )


def _scene_count_value(inputs: Mapping[str, object], duration_profile: str, source: Mapping[str, object]) -> int:
    explicit = _pick(inputs, "scene_count", "sceneCount")
    if explicit is None:
        explicit = _pick(source, "scene_count", "sceneCount")
    if explicit is not None:
        return _coerce_int(explicit, fallback=_DEFAULT_SCENE_COUNT)

    return {
        DurationProfile.SHORT_15_30S.value: 3,
        DurationProfile.SIXTY_SECONDS.value: 3,
        DurationProfile.THREE_FIVE_MINUTES.value: 4,
        DurationProfile.EIGHT_FIFTEEN_MINUTES.value: 5,
    }.get(duration_profile, _DEFAULT_SCENE_COUNT)


def _source_texts(source: Mapping[str, object], brief_source: Mapping[str, object]) -> list[str]:
    texts: list[str] = []
    for key in (
        "summary",
        "source_summary",
        "outline_text",
        "outlineText",
        "objective",
        "audience",
        "constraints",
        "success_criteria",
        "key_points",
        "keyPoints",
        "findings",
        "research_notes",
        "researchNotes",
        "confirmed_facts",
        "confirmedFacts",
        "timeline",
        "sections",
        "scene_outline",
    ):
        texts.extend(_coerce_text_list(_pick(source, key)))

    for key in ("objective", "audience", "constraints", "success_criteria"):
        texts.extend(_coerce_text_list(_pick(brief_source, key)))

    return _dedupe_texts(texts)


def _build_sections(topic: str, source_points: list[str], scene_count: int) -> tuple[list[JsonDict], list[JsonDict]]:
    templates = (
        ("Hook", "hook", "Open with a clear reason to keep watching."),
        ("Setup", "setup", "Establish the context and stakes."),
        ("Development", "body", "Develop the central idea with supporting detail."),
        ("Turning Point", "turn", "Shift toward the most important implication."),
        ("Close", "close", "End with a focused takeaway or call to action."),
        ("Follow-up", "follow_up", "Leave room for further exploration or continuation."),
    )
    if scene_count > len(templates):
        templates = templates + tuple(
            (f"Section {index}", "body", f"Develop the topic through section {index}.")
            for index in range(len(templates) + 1, scene_count + 1)
        )

    sections: list[JsonDict] = []
    scene_outline: list[JsonDict] = []
    for order in range(1, scene_count + 1):
        heading, role, fallback_focus = templates[order - 1]
        focus = source_points[order - 1] if order - 1 < len(source_points) else fallback_focus
        text = {
            1: f"Open with a direct hook about {topic}: {focus}",
            scene_count: f"Close by reinforcing {focus}",
        }.get(order, f"Develop {topic} through {focus}")
        section = {
            "order": order,
            "heading": heading,
            "title": heading,
            "role": role,
            "focus": focus,
            "summary": focus,
            "text": text,
            "duration_estimate": round(max(8.0, len(text.split()) * 1.35), 2),
        }
        sections.append(section)
        scene_outline.append(
            {
                "order": order,
                "title": heading,
                "focus": focus,
                "summary": text,
                "transition": {
                    1: "open",
                    scene_count: "close",
                }.get(order, "develop"),
                "visual_intensity": {
                    1: "high",
                    scene_count: "medium",
                }.get(order, "medium"),
                "duration_estimate": section["duration_estimate"],
            }
        )

    return sections, scene_outline


def _outline_text(topic: str, sections: list[JsonDict], duration_profile: str, source_kind: str) -> str:
    lines = [
        f"Outline for {topic}",
        f"Source kind: {source_kind}",
        f"Duration profile: {duration_profile}",
    ]
    for section in sections:
        lines.append(f"{section['order']}. {section['heading']}: {section['summary']}")
    return "\n".join(lines)


class OutlineModule:
    """Generate deterministic narrative and scene outlines for long-form workflows."""

    definition = ModuleDefinition(
        name="outline",
        input_schema={
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "brief": {"type": ["object", "string"]},
                "research": {"type": ["object", "array", "string"]},
                "dossier": {"type": ["object", "array", "string"]},
                "outline": {"type": ["object", "string"]},
                "duration_profile": {"type": "string"},
                "durationProfile": {"type": "string"},
                "scene_count": {"type": "integer"},
                "sceneCount": {"type": "integer"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "outline": {"type": "object"},
                "artifact": {"type": "object"},
                "narrative_outline": {"type": "object"},
                "scene_outline": {"type": "array"},
                "workflow_snapshot": {"type": "object"},
                "source_kind": {"type": "string"},
                "workflow_run": {"type": "object"},
                "generation_job": {"type": "object"},
            },
        },
        config_schema={
            "type": "object",
            "properties": {
                "duration_profile": {"type": "string"},
                "scene_count": {"type": "integer"},
                "outline_name": {"type": "string"},
            },
        },
        dependencies=(("dossier", "research", "brief"),),
        enabled_by_default=True,
        disabled_behavior="fail",
        retry_limit=1,
        artifact_outputs=("outline.json",),
        error_behavior="request_missing_fields",
    )

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        default_duration_profile: str = _DEFAULT_DURATION_PROFILE,
        default_scene_count: int = _DEFAULT_SCENE_COUNT,
        outline_name: str = "outline.json",
    ) -> None:
        self._artifact_store = artifact_store
        self._default_duration_profile = _optional_text(default_duration_profile) or _DEFAULT_DURATION_PROFILE
        self._default_scene_count = max(1, int(default_scene_count))
        self._outline_name = _optional_text(outline_name) or "outline.json"

    def execute(self, context: ModuleExecutionContext) -> ModuleResult:
        inputs = dict(context.inputs)
        source_name, source_payload, source_kind = _extract_payload(inputs, context.module_results)
        brief_source = _brief_source(_pick(inputs, "brief", "content_brief", "contentBrief"))

        topic = _optional_text(_pick(inputs, "topic"))
        if not topic:
            topic = _optional_text(_pick(source_payload, "topic"))
        if not topic:
            topic = _optional_text(_pick(brief_source, "topic"))
        if not topic:
            topic = _topic_from_text(
                " ".join(
                    part
                    for part in (
                        _optional_text(_pick(source_payload, "summary", "source_summary", "outline_text")),
                        _optional_text(_pick(brief_source, "objective")),
                        _optional_text(_pick(brief_source, "audience")),
                    )
                    if part
                )
            )

        topic = _coerce_text(topic, field_name="topic")
        duration_profile = _duration_profile_value(inputs, source_payload) or self._default_duration_profile
        scene_count = _scene_count_value(inputs, duration_profile, source_payload)

        source_points = _source_texts(source_payload, brief_source)
        if not source_points:
            source_points = [topic]
        source_points = _dedupe_texts([*source_points, topic])
        sections, scene_outline = _build_sections(topic, source_points, scene_count)
        outline_text = _outline_text(topic, sections, duration_profile, source_kind)
        source_summary = " ".join(_dedupe_texts(source_points)[:4]) or topic
        outline_id = _stable_identifier(
            workflow_run_id=context.workflow_run_id,
            source_kind=source_kind,
            topic=topic,
            outline_text=outline_text,
        )

        narrative_outline = {
            "outline_id": outline_id,
            "topic": topic,
            "source_kind": source_kind,
            "duration_profile": duration_profile,
            "scene_count": scene_count,
            "source_summary": source_summary,
            "summary": source_summary,
            "sections": sections,
            "outline_text": outline_text,
        }

        outline_record = {
            "outline_id": outline_id,
            "workflow_run_id": context.workflow_run_id,
            "workflow_config_id": context.workflow_config_id,
            "module_name": self.definition.name,
            "topic": topic,
            "source_kind": source_kind,
            "duration_profile": duration_profile,
            "scene_count": scene_count,
            "source_summary": source_summary,
            "outline_text": outline_text,
            "narrative_outline": narrative_outline,
            "scene_outline": scene_outline,
            "sections": sections,
            "brief": brief_source,
            "research": _coerce_mapping(source_payload if source_kind == "research" else _pick(inputs, "research")),
            "dossier": _coerce_mapping(source_payload if source_kind == "dossier" else _pick(inputs, "dossier")),
            "created_at": _DETERMINISTIC_CREATED_AT,
        }
        outline_record["outline"] = {
            key: value
            for key, value in outline_record.items()
            if key not in {"workflow_run_id", "workflow_config_id", "created_at"}
        }

        artifact = self._artifact_store.save_artifact(
            self._outline_name,
            json.dumps(outline_record, indent=2, sort_keys=True, default=_json_default),
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "outline",
                "topic": topic,
                "source_kind": source_kind,
                "duration_profile": duration_profile,
                "scene_count": scene_count,
            },
        )

        workflow_run_payload = {
            "id": context.workflow_run_id,
            "workflow_config_id": context.workflow_config_id,
            "status": "running",
            "current_stage": self.definition.name,
            "artifact_ids": list(dict.fromkeys([*context.artifact_ids, "outline.json"])),
        }
        generation_job_payload = {
            "id": _stable_identifier(
                workflow_run_id=context.workflow_run_id,
                source_kind=source_kind,
                topic=topic,
                outline_text=outline_text,
            ),
            "workflow_run_id": context.workflow_run_id,
            "module_name": self.definition.name,
            "status": "completed",
            "attempt": 1,
            "retry_count": 0,
            "output_artifact_ids": ["outline.json"],
        }

        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=self.definition.artifact_outputs,
            output={
                "outline": outline_record,
                "artifact": artifact.to_payload(),
                "narrative_outline": narrative_outline,
                "scene_outline": scene_outline,
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


__all__ = ["OutlineModule"]

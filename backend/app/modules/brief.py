"""Deterministic brief intake module."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict

from app.domain.content_brief import ContentBrief
from app.domain.enums import ContentGenre, TargetPlatform, DurationProfile
from app.domain.types import JsonDict
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


def _coerce_text(value: object | None, *, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"BriefModule {field_name} is required.")
    return " ".join(text.split())


def _optional_text(value: object | None) -> str:
    return " ".join(str(value).strip().split()) if value is not None else ""


def _coerce_list(value: object | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates: Iterable[object] = (value,)
    elif isinstance(value, Mapping):
        candidates = value.values()
    elif isinstance(value, Iterable):
        candidates = value
    else:
        candidates = (value,)
    items = [" ".join(str(item).strip().split()) for item in candidates if str(item).strip()]
    return [item for item in items if item]


def _topic_from_text(text: str) -> str:
    normalized = " ".join(text.strip().split())
    if not normalized:
        raise ValueError("BriefModule topic, brief or transcript is required.")
    for separator in (".", "!", "?"):
        if separator in normalized:
            head = normalized.split(separator, 1)[0].strip()
            if head:
                return head[:120]
    return normalized[:120]


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


def _default_objective(topic: str, source_kind: str) -> str:
    if source_kind == "transcript":
        return f"Normalize the supplied transcript into a content brief about {topic}."
    if source_kind == "brief":
        return f"Normalize the supplied brief into a content brief about {topic}."
    return f"Create a content brief about {topic}."


def _default_constraints(language: str, tone: str, duration_profile: str, source_kind: str) -> list[str]:
    return [
        f"language={language}",
        f"tone={tone}",
        f"duration_profile={duration_profile}",
        f"source_kind={source_kind}",
    ]


def _default_success_criteria(topic: str) -> list[str]:
    return [
        f"Keep the brief focused on {topic}.",
        "Preserve the user's intent in a structured format.",
    ]


def _brief_payload(brief: ContentBrief, *, language: str, tone: str, source_kind: str) -> JsonDict:
    return {
        "brief_id": brief.id,
        "project_id": brief.project_id,
        "topic": brief.topic,
        "objective": brief.objective,
        "audience": brief.audience,
        "constraints": list(brief.constraints),
        "duration_profile": brief.duration_profile.value,
        "language": language,
        "tone": tone,
        "success_criteria": list(brief.success_criteria),
        "source_kind": source_kind,
        "created_at": brief.created_at.isoformat(),
    }


class BriefModule:
    """Normalize topic, brief or transcript input into a structured brief."""

    definition = ModuleDefinition(
        name="brief",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "topic": {"type": "string"},
                "brief": {"type": ["object", "string"]},
                "transcript": {"type": "string"},
                "objective": {"type": "string"},
                "audience": {"type": "string"},
                "constraints": {"type": "array", "items": {"type": "string"}},
                "success_criteria": {"type": "array", "items": {"type": "string"}},
                "duration_profile": {"type": "string"},
                "language": {"type": "string"},
                "tone": {"type": "string"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "content_brief": {"type": "object"},
                "artifact": {"type": "object"},
                "source_kind": {"type": "string"},
                "workflow_snapshot": {"type": "object"},
            },
        },
        config_schema={
            "type": "object",
            "properties": {
                "default_language": {"type": "string"},
                "default_tone": {"type": "string"},
                "supported_genres": {"type": "array", "items": {"type": "string"}},
                "supported_platforms": {"type": "array", "items": {"type": "string"}},
            },
        },
        disabled_behavior="skip",
        enabled_by_default=True,
        retry_limit=1,
        artifact_outputs=("brief.json",),
        error_behavior="request_missing_fields",
    )

    def __init__(
        self,
        *,
        default_language: str = "en",
        default_tone: str = "neutral",
        supported_genres: tuple[str, ...] | None = None,
        supported_platforms: tuple[str, ...] | None = None,
    ) -> None:
        self.default_language = _optional_text(default_language) or "en"
        self.default_tone = _optional_text(default_tone) or "neutral"
        self.supported_genres = tuple(
            _optional_text(value)
            for value in (supported_genres or tuple(genre.value for genre in ContentGenre))
            if _optional_text(value)
        )
        self.supported_platforms = tuple(
            _optional_text(value)
            for value in (supported_platforms or tuple(platform.value for platform in TargetPlatform))
            if _optional_text(value)
        )

    def execute(self, context: ModuleExecutionContext) -> ModuleResult:
        inputs = dict(context.inputs)
        brief_source = _brief_source(_pick(inputs, "brief", "content_brief", "contentBrief"))
        transcript = _optional_text(_pick(inputs, "transcript", "transcript_text", "transcriptText"))
        explicit_topic = _optional_text(_pick(inputs, "topic"))
        source_kind = "topic"

        topic = explicit_topic or _optional_text(brief_source.get("topic"))
        if topic:
            source_kind = "brief" if brief_source and not explicit_topic else "topic"
        elif transcript:
            topic = _topic_from_text(transcript)
            source_kind = "transcript"
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
            raise ValueError("BriefModule topic, brief or transcript is required.")

        project_id = _optional_text(
            _pick(inputs, "project_id", "projectId", default=brief_source.get("project_id"))
        )
        objective = _optional_text(
            _pick(inputs, "objective", default=brief_source.get("objective"))
        ) or _default_objective(topic, source_kind)
        audience = _optional_text(_pick(inputs, "audience", default=brief_source.get("audience")))
        duration_profile = _pick(
            inputs,
            "duration_profile",
            "durationProfile",
            default=brief_source.get("duration_profile") or DurationProfile.SIXTY_SECONDS.value,
        )
        language = (
            _optional_text(_pick(inputs, "language", default=brief_source.get("language")))
            or self.default_language
        )
        tone = _optional_text(_pick(inputs, "tone", default=brief_source.get("tone"))) or self.default_tone

        constraints = _coerce_list(_pick(inputs, "constraints", default=brief_source.get("constraints")))
        if not constraints:
            constraints = _default_constraints(language, tone, str(duration_profile), source_kind)

        success_criteria = _coerce_list(
            _pick(inputs, "success_criteria", "successCriteria", default=brief_source.get("success_criteria"))
        )
        if not success_criteria:
            success_criteria = _default_success_criteria(topic)

        content_brief = ContentBrief.create(
            project_id=project_id,
            topic=_coerce_text(topic, field_name="topic"),
            objective=objective,
            audience=audience,
            constraints=constraints,
            duration_profile=str(duration_profile),
            success_criteria=success_criteria,
        )

        payload = _brief_payload(content_brief, language=language, tone=tone, source_kind=source_kind)
        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=self.definition.artifact_outputs,
            output={
                "content_brief": payload,
                "artifact": {
                    "name": "brief.json",
                    "artifact_type": "brief",
                    "module_name": self.definition.name,
                    "workflow_run_id": context.workflow_run_id,
                    "storage_ref": "brief.json",
                    "content": payload,
                },
                "source_kind": source_kind,
                "workflow_snapshot": {
                    "workflow_run_id": context.workflow_run_id,
                    "workflow_config_id": context.workflow_config_id,
                    "module_name": context.module_name,
                    "enabled_modules": list(context.enabled_modules),
                    "disabled_modules": list(context.disabled_modules),
                },
            },
        )

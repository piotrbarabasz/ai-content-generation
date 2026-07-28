"""Deterministic scene planning module for short video workflows."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from hashlib import sha256

from app.domain.render_scene import RenderScene
from app.domain.types import JsonDict
from app.storage.artifact_store import ArtifactStore
from app.workflow.execution import ModuleExecutionContext, ModuleResult
from app.workflow.module import ModuleDefinition


_DEFAULT_PLATFORM = "youtube_shorts"
_DEFAULT_ASPECT_RATIO = "9:16"


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
        raise ValueError(f"ScenePlanningModule {field_name} is required.")
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
    if isinstance(value, str):
        values: Iterable[object] = (value,)
    elif isinstance(value, Mapping):
        values = value.values()
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


def _stable_identifier(*, workflow_run_id: str, topic: str, platform: str, aspect_ratio: str, scenes: list[JsonDict]) -> str:
    signature = sha256(
        json.dumps(
            {
                "workflow_run_id": workflow_run_id,
                "topic": topic,
                "platform": platform,
                "aspect_ratio": aspect_ratio,
                "scenes": scenes,
            },
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"scene_plan_{signature}"


def _json_default(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()  # datetime-like values from domain entities
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _segment_payloads(module_results: Mapping[str, object], inputs: Mapping[str, object]) -> tuple[str, list[JsonDict]]:
    candidate = _pick(inputs, "narrative_segments", "segments")
    if candidate is None:
        script_result = module_results.get("scriptGeneration")
        if script_result is not None:
            candidate = getattr(script_result, "output", None)

    source_kind = "scriptGeneration"
    segments: list[JsonDict] = []
    if isinstance(candidate, Mapping):
        source_kind = _optional_text(candidate.get("source_kind")) or source_kind
        raw_segments = candidate.get("segments")
        if raw_segments is None and isinstance(candidate.get("narrative_segments"), Mapping):
            raw_segments = candidate["narrative_segments"].get("segments")
        if isinstance(raw_segments, Iterable):
            for segment in raw_segments:
                if isinstance(segment, Mapping):
                    segments.append(dict(segment))
    elif isinstance(candidate, Iterable):
        for segment in candidate:
            if isinstance(segment, Mapping):
                segments.append(dict(segment))

    if segments:
        return source_kind, segments

    script_text = _optional_text(_pick(inputs, "script", "script_text", "text", "narration"))
    if not script_text and candidate is not None and isinstance(candidate, Mapping):
        script_text = _optional_text(candidate.get("script_text") or candidate.get("script"))

    if not script_text:
        raise ValueError("ScenePlanningModule requires scriptGeneration output or script text.")

    derived_segments = _dedupe_texts(
        _coerce_text_list(script_text.replace("\n", ". "))
    )
    if not derived_segments:
        derived_segments = [script_text]

    fallback_segments: list[JsonDict] = []
    for index, text in enumerate(derived_segments[:3], start=1):
        fallback_segments.append(
            {
                "order": index,
                "title": {1: "Hook", 2: "Develop", 3: "Close"}.get(index, f"Scene {index}"),
                "text": text,
                "role": {1: "hook", 2: "body", 3: "close"}.get(index, "body"),
                "duration_estimate": max(4.0, round(len(text.split()) * 1.25, 2)),
            }
        )
    return source_kind, fallback_segments


def _scene_duration(segment: Mapping[str, object], order: int) -> float:
    duration_estimate = segment.get("duration_estimate")
    if isinstance(duration_estimate, (int, float)) and duration_estimate > 0:
        return float(duration_estimate)
    text = _optional_text(segment.get("text") or segment.get("summary") or segment.get("title"))
    if not text:
        return 5.0
    return max(4.0, round(len(text.split()) * 1.25, 2))


def _visual_intensity(order: int, segment: Mapping[str, object]) -> str:
    role = _optional_text(segment.get("role")).lower()
    if role == "hook" or order == 1:
        return "high"
    if role == "close" or order >= 3:
        return "medium"
    return "medium"


def _timing_hint(start_seconds: float, duration_seconds: float) -> str:
    end_seconds = start_seconds + duration_seconds
    return f"{_format_time(start_seconds)}-{_format_time(end_seconds)}"


def _format_time(total_seconds: float) -> str:
    whole_seconds = max(0, int(round(total_seconds)))
    minutes, seconds = divmod(whole_seconds, 60)
    return f"{minutes}:{seconds:02d}"


def _approval_requested(inputs: Mapping[str, object]) -> bool:
    explicit_flag = _pick(
        inputs,
        "approval_required",
        "scene_plan_approval_required",
        "requires_approval",
    )
    if isinstance(explicit_flag, bool):
        return explicit_flag
    if isinstance(explicit_flag, str):
        return explicit_flag.strip().lower() in {"1", "true", "yes", "y", "required", "pending"}

    approval_policy = _coerce_mapping(_pick(inputs, "approval_policy", "approvalPolicy"))
    scene_policy = approval_policy.get("scene_plan")
    if isinstance(scene_policy, str):
        return scene_policy.strip().lower() in {"required", "pending", "review_required"}
    if isinstance(scene_policy, bool):
        return scene_policy
    return False


class ScenePlanningModule:
    """Generate deterministic render scenes and a scene plan for short video runs."""

    definition = ModuleDefinition(
        name="scenePlanning",
        input_schema={
            "type": "object",
            "properties": {
                "scriptGeneration": {"type": ["object", "string"]},
                "narrative_segments": {"type": ["array", "object"]},
                "segments": {"type": ["array", "object"]},
                "script": {"type": "string"},
                "script_text": {"type": "string"},
                "text": {"type": "string"},
                "narration": {"type": "string"},
                "topic": {"type": "string"},
                "target_platform": {"type": "string"},
                "platform": {"type": "string"},
                "aspect_ratio": {"type": "string"},
                "approval_required": {"type": "boolean"},
                "scene_plan_approval_required": {"type": "boolean"},
                "requires_approval": {"type": "boolean"},
                "approval_policy": {"type": "object"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "scene_plan": {"type": "object"},
                "artifact": {"type": "object"},
                "render_scenes": {"type": "object"},
                "render_scenes_artifact": {"type": "object"},
                "approval_checkpoint": {"type": "object"},
                "workflow_snapshot": {"type": "object"},
                "source_kind": {"type": "string"},
            },
        },
        config_schema={
            "type": "object",
            "properties": {
                "platform": {"type": "string"},
                "aspect_ratio": {"type": "string"},
                "scene_plan_name": {"type": "string"},
                "render_scenes_name": {"type": "string"},
            },
        },
        dependencies=(("scriptGeneration",),),
        enabled_by_default=True,
        disabled_behavior="fail",
        retry_limit=1,
        artifact_outputs=("render_scenes.json", "scene_plan.json"),
        error_behavior="request_missing_fields",
    )

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        platform: str = _DEFAULT_PLATFORM,
        aspect_ratio: str = _DEFAULT_ASPECT_RATIO,
        scene_plan_name: str = "scene_plan.json",
        render_scenes_name: str = "render_scenes.json",
    ) -> None:
        self._artifact_store = artifact_store
        self._platform = _optional_text(platform) or _DEFAULT_PLATFORM
        self._aspect_ratio = _optional_text(aspect_ratio) or _DEFAULT_ASPECT_RATIO
        self._scene_plan_name = _optional_text(scene_plan_name) or "scene_plan.json"
        self._render_scenes_name = _optional_text(render_scenes_name) or "render_scenes.json"

    def execute(self, context: ModuleExecutionContext) -> ModuleResult:
        inputs = dict(context.inputs)
        source_kind, segments = _segment_payloads(context.module_results, inputs)

        topic = _optional_text(_pick(inputs, "topic"))
        if not topic:
            script_result = context.module_results.get("scriptGeneration")
            if script_result is not None:
                output = getattr(script_result, "output", {})
                if isinstance(output, Mapping):
                    topic = _optional_text(output.get("topic") or output.get("source_summary"))
        if not topic and segments:
            topic = _optional_text(segments[0].get("title") or segments[0].get("text"))
        topic = _coerce_text(topic, field_name="topic")

        platform = _optional_text(_pick(inputs, "target_platform", "targetPlatform", "platform")) or self._platform
        aspect_ratio = _optional_text(_pick(inputs, "aspect_ratio", "aspectRatio")) or self._aspect_ratio
        approval_required = _approval_requested(inputs)

        scene_plan_id = _stable_identifier(
            workflow_run_id=context.workflow_run_id,
            topic=topic,
            platform=platform,
            aspect_ratio=aspect_ratio,
            scenes=segments,
        )

        render_scene_models: list[RenderScene] = []
        render_scene_payloads: list[JsonDict] = []
        elapsed_seconds = 0.0
        for order, segment in enumerate(segments, start=1):
            duration_seconds = _scene_duration(segment, order)
            timing_hint = _timing_hint(elapsed_seconds, duration_seconds)
            render_scene = RenderScene.create(
                workflow_run_id=context.workflow_run_id,
                order=order,
                scene_plan_id=scene_plan_id,
                timing_hint=timing_hint,
                visual_intensity=_visual_intensity(order, segment),
            )
            scene_title = _optional_text(segment.get("title") or segment.get("heading")) or f"Scene {order}"
            render_scene_payload = asdict(render_scene)
            render_scene_payload.update(
                {
                    "scene_title": scene_title,
                    "visual_focus": _optional_text(segment.get("text") or segment.get("summary") or scene_title),
                    "source_narrative_segment_id": _optional_text(segment.get("id")),
                    "source_narrative_segment_order": int(segment.get("order") or order),
                    "source_kind": source_kind,
                }
            )
            render_scene_models.append(render_scene)
            render_scene_payloads.append(render_scene_payload)
            elapsed_seconds += duration_seconds

        render_scenes_payload = {
            "workflow_run_id": context.workflow_run_id,
            "module_name": self.definition.name,
            "scene_plan_id": scene_plan_id,
            "source_kind": source_kind,
            "topic": topic,
            "platform": platform,
            "aspect_ratio": aspect_ratio,
            "scenes": render_scene_payloads,
        }
        render_scenes_manifest = self._artifact_store.save_artifact(
            self._render_scenes_name,
            json.dumps(render_scenes_payload, indent=2, sort_keys=True, default=_json_default),
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "render_scenes",
                "scene_plan_id": scene_plan_id,
                "platform": platform,
                "aspect_ratio": aspect_ratio,
                "source_kind": source_kind,
            },
        )

        scene_plan_payload = {
            "workflow_run_id": context.workflow_run_id,
            "module_name": self.definition.name,
            "scene_plan_id": scene_plan_id,
            "source_kind": source_kind,
            "topic": topic,
            "platform": platform,
            "aspect_ratio": aspect_ratio,
            "approval_required": approval_required,
            "scene_count": len(render_scene_payloads),
            "scenes": render_scene_payloads,
            "render_scenes_storage_key": render_scenes_manifest.storage_key,
        }
        scene_plan_manifest = self._artifact_store.save_artifact(
            self._scene_plan_name,
            json.dumps(scene_plan_payload, indent=2, sort_keys=True, default=_json_default),
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "scene_plan",
                "scene_plan_id": scene_plan_id,
                "platform": platform,
                "aspect_ratio": aspect_ratio,
                "source_kind": source_kind,
                "approval_required": approval_required,
            },
        )

        output: JsonDict = {
            "scene_plan": scene_plan_payload,
            "artifact": scene_plan_manifest.to_payload(),
            "render_scenes": render_scenes_payload,
            "render_scenes_artifact": render_scenes_manifest.to_payload(),
            "workflow_snapshot": {
                "workflow_run_id": context.workflow_run_id,
                "workflow_config_id": context.workflow_config_id,
                "module_name": context.module_name,
                "enabled_modules": list(context.enabled_modules),
                "disabled_modules": list(context.disabled_modules),
            },
            "source_kind": source_kind,
        }
        if approval_required:
            output["approval_checkpoint"] = {
                "checkpoint_type": "scene_plan",
                "status": "pending",
                "required": True,
                "artifact_id": scene_plan_manifest.storage_key,
                "scene_plan_id": scene_plan_id,
                "next_stage": "videoRendering",
            }

        return ModuleResult(
            module_name=self.definition.name,
            status="waiting_for_approval" if approval_required else "completed",
            output_artifact_ids=self.definition.artifact_outputs,
            output=output,
        )

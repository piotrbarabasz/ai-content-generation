"""Deterministic video rendering module."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict
from hashlib import sha256

from app.domain.types import JsonDict
from app.domain.video_render import VideoRender
from app.providers.interfaces import VideoRendererProvider
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
                _pick(item, "ref", "reference", "storage_key", "storageKey", "video_ref")
            )
            if nested:
                items.extend(nested)
                continue
        text = _optional_text(item)
        if text:
            items.append(text)
    return items


def _stable_identifier(*parts: object, prefix: str) -> str:
    signature = sha256(
        ":".join(_optional_text(part) for part in parts).encode("utf-8")
    ).hexdigest()[:12]
    return f"{prefix}_{signature}"


def _json_default(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _extract_scene_plan(
    inputs: Mapping[str, object],
    module_results: Mapping[str, object],
) -> JsonDict:
    explicit_scene_plan = _pick(inputs, "scene_plan", "scenePlan")
    if isinstance(explicit_scene_plan, Mapping):
        return dict(explicit_scene_plan)
    if isinstance(explicit_scene_plan, str):
        return {"scene_plan_ref": _optional_text(explicit_scene_plan)}

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


def _extract_voiceover(
    inputs: Mapping[str, object],
    module_results: Mapping[str, object],
) -> JsonDict:
    explicit_voiceover = _pick(inputs, "voiceover", "voiceover_result")
    if isinstance(explicit_voiceover, Mapping):
        return dict(explicit_voiceover)
    if isinstance(explicit_voiceover, str):
        return {
            "audio_ref": _optional_text(explicit_voiceover),
            "audio_storage_key": _optional_text(explicit_voiceover),
        }

    voiceover_result = module_results.get("voiceover")
    if voiceover_result is not None:
        output = getattr(voiceover_result, "output", {})
        if isinstance(output, Mapping):
            voiceover = _coerce_mapping(output.get("voiceover"))
            if voiceover:
                return voiceover
            if isinstance(output.get("voiceover"), str):
                voiceover_ref = _optional_text(output.get("voiceover"))
                return {
                    "audio_ref": voiceover_ref,
                    "audio_storage_key": voiceover_ref,
                }
            artifact = _coerce_mapping(output.get("artifact"))
            if artifact:
                return {
                    "provider": _optional_text(output.get("provider")),
                    "audio_ref": _optional_text(output.get("audio_ref")),
                    "audio_storage_key": _optional_text(artifact.get("storage_key")),
                }

    return {}


def _extract_captions(
    inputs: Mapping[str, object],
    module_results: Mapping[str, object],
) -> JsonDict:
    explicit_captions = _pick(inputs, "captions", "captions_result", "captions_output")
    if isinstance(explicit_captions, Mapping):
        return dict(explicit_captions)
    if isinstance(explicit_captions, str):
        return {
            "caption_storage_key": _optional_text(explicit_captions),
            "captions_ref": _optional_text(explicit_captions),
        }

    captions_result = module_results.get("captions")
    if captions_result is not None:
        output = getattr(captions_result, "output", {})
        if isinstance(output, Mapping):
            captions = _coerce_mapping(output.get("captions"))
            if captions:
                return captions
            if isinstance(output.get("captions"), str):
                captions_ref = _optional_text(output.get("captions"))
                return {
                    "caption_storage_key": captions_ref,
                    "captions_ref": captions_ref,
                }
            artifact = _coerce_mapping(output.get("artifact"))
            if artifact:
                return {
                    "provider": _optional_text(output.get("provider")),
                    "caption_storage_key": _optional_text(artifact.get("storage_key")),
                }

    return {}


def _extract_assets(
    inputs: Mapping[str, object],
    module_results: Mapping[str, object],
) -> list[str]:
    explicit_assets = _pick(inputs, "assets", "asset_refs", "assetRefs")
    assets = _coerce_text_list(explicit_assets)
    if assets:
        return assets

    assets_result = module_results.get("assets")
    if assets_result is not None:
        output = getattr(assets_result, "output", {})
        if isinstance(output, Mapping):
            payload = output.get("assets") or output.get("asset_refs") or output.get("assetRefs")
            return _coerce_text_list(payload)

    return []


def _parse_timecode(value: str) -> float:
    cleaned = value.strip()
    if not cleaned:
        return 0.0
    if "-" in cleaned:
        _, cleaned = cleaned.rsplit("-", 1)
    pieces = cleaned.split(":")
    if len(pieces) != 2:
        return 0.0
    minutes, seconds = pieces
    if not minutes.isdigit() or not seconds.isdigit():
        return 0.0
    return float(int(minutes) * 60 + int(seconds))


def _scene_duration_seconds(scene_plan: Mapping[str, object]) -> float:
    scenes = scene_plan.get("scenes")
    if not isinstance(scenes, list):
        return 0.0

    total = 0.0
    for scene in scenes:
        if not isinstance(scene, Mapping):
            continue
        duration = scene.get("duration_seconds")
        if isinstance(duration, (int, float)) and duration >= 0:
            total += float(duration)
            continue
        duration = scene.get("duration_estimate")
        if isinstance(duration, (int, float)) and duration >= 0:
            total += float(duration)
            continue
        total += _parse_timecode(_optional_text(scene.get("timing_hint")).rsplit("-", 1)[-1])
    return round(total, 3)


def _normalise_duration(*, scene_plan: Mapping[str, object], voiceover: Mapping[str, object]) -> float:
    durations: list[float] = []
    scene_duration = _scene_duration_seconds(scene_plan)
    if scene_duration > 0:
        durations.append(scene_duration)
    voiceover_duration = voiceover.get("duration_seconds")
    if isinstance(voiceover_duration, (int, float)) and voiceover_duration >= 0:
        durations.append(float(voiceover_duration))
    if durations:
        return round(max(durations), 3)
    return 0.0


def _scene_plan_summary(scene_plan: Mapping[str, object]) -> JsonDict:
    scenes = scene_plan.get("scenes")
    scene_count = len(scenes) if isinstance(scenes, list) else 0
    return {
        "scene_plan_id": _optional_text(scene_plan.get("scene_plan_id")),
        "scene_count": scene_count,
        "platform": _optional_text(scene_plan.get("platform")),
        "aspect_ratio": _optional_text(scene_plan.get("aspect_ratio")),
    }


class VideoRenderingModule:
    """Generate deterministic video render metadata and artifact references."""

    definition = ModuleDefinition(
        name="videoRendering",
        input_schema={
            "type": "object",
            "properties": {
                "scene_plan": {"type": ["object", "string"]},
                "scenePlan": {"type": ["object", "string"]},
                "voiceover": {"type": ["object", "string"]},
                "voiceover_result": {"type": ["object", "string"]},
                "captions": {"type": ["object", "string"]},
                "captions_result": {"type": ["object", "string"]},
                "assets": {"type": ["array", "object", "string"]},
                "asset_refs": {"type": ["array", "object", "string"]},
                "assetRefs": {"type": ["array", "object", "string"]},
                "resolution": {"type": "string"},
                "fps": {"type": ["number", "integer"]},
                "codec": {"type": "string"},
                "format": {"type": "string"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "video_render": {"type": "object"},
                "artifact": {"type": "object"},
                "render_request": {"type": "object"},
                "scene_plan": {"type": "object"},
                "voiceover": {"type": "object"},
                "captions": {"type": "object"},
                "assets": {"type": "array"},
                "workflow_snapshot": {"type": "object"},
            },
        },
        config_schema={
            "type": "object",
            "properties": {
                "default_resolution": {"type": "string"},
                "default_fps": {"type": ["number", "integer"]},
                "default_codec": {"type": "string"},
                "artifact_name": {"type": "string"},
            },
        },
        dependencies=(("scenePlanning",),),
        enabled_by_default=False,
        disabled_behavior="skip",
        retry_limit=1,
        artifact_outputs=("render.mp4",),
        error_behavior="request_missing_fields",
    )

    def __init__(
        self,
        *,
        video_renderer_provider: VideoRendererProvider,
        artifact_store: ArtifactStore,
        default_resolution: str = "1080x1920",
        default_fps: int = 30,
        default_codec: str = "h264",
        artifact_name: str = "render.mp4",
    ) -> None:
        self._video_renderer_provider = video_renderer_provider
        self._artifact_store = artifact_store
        self._default_resolution = _optional_text(default_resolution) or "1080x1920"
        self._default_fps = int(default_fps) if int(default_fps) > 0 else 30
        self._default_codec = _optional_text(default_codec) or "h264"
        self._artifact_name = _optional_text(artifact_name) or "render.mp4"

    def execute(self, context: ModuleExecutionContext) -> ModuleResult:
        inputs = dict(context.inputs)
        scene_plan = _extract_scene_plan(inputs, context.module_results)
        if not scene_plan:
            raise ValueError("VideoRenderingModule requires a scene plan.")

        voiceover = _extract_voiceover(inputs, context.module_results)
        captions = _extract_captions(inputs, context.module_results)
        assets = _extract_assets(inputs, context.module_results)

        resolution = _optional_text(_pick(inputs, "resolution")) or self._default_resolution
        fps_value = _pick(inputs, "fps")
        if isinstance(fps_value, (int, float)) and fps_value > 0:
            fps = int(fps_value)
        else:
            fps = self._default_fps
        codec = _optional_text(_pick(inputs, "codec")) or self._default_codec
        render_format = _optional_text(_pick(inputs, "format")) or "mp4"

        audio_ref = _optional_text(
            _pick(
                voiceover,
                "audio_ref",
                "audioRef",
                "audio_storage_ref",
                "audio_storage_key",
            )
        )
        captions_ref = _optional_text(
            _pick(
                captions,
                "caption_storage_key",
                "captions_ref",
                "captionsRef",
                "storage_key",
            )
        )

        render_payload = self._video_renderer_provider.render(
            scene_plan,
            audio_ref or None,
            captions_ref or None,
        )
        video_ref = _optional_text(render_payload.get("video_ref"))
        if not video_ref:
            raise ValueError("VideoRenderingModule video renderer provider did not return a video_ref.")

        scene_summary = _scene_plan_summary(scene_plan)
        duration_seconds = _normalise_duration(scene_plan=scene_plan, voiceover=voiceover)
        render_id = _stable_identifier(
            context.workflow_run_id,
            scene_summary["scene_plan_id"],
            video_ref,
            audio_ref,
            captions_ref,
            resolution,
            fps,
            codec,
            render_format,
            prefix="video_render",
        )

        render_record = {
            "workflow_run_id": context.workflow_run_id,
            "module_name": self.definition.name,
            "provider": self._video_renderer_provider.provider_name,
            "video_ref": video_ref,
            "scene_plan": scene_summary,
            "audio_ref": audio_ref,
            "captions_ref": captions_ref,
            "asset_refs": list(assets),
            "resolution": resolution,
            "fps": fps,
            "codec": codec,
            "format": render_format,
            "duration_seconds": duration_seconds,
            "render_id": render_id,
            "provider_payload": render_payload,
        }

        artifact = self._artifact_store.save_artifact(
            self._artifact_name,
            video_ref,
            metadata={
                "workflow_run_id": context.workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "video_render",
                "provider": self._video_renderer_provider.provider_name,
                "scene_plan_id": scene_summary["scene_plan_id"],
                "audio_ref": audio_ref,
                "captions_ref": captions_ref,
                "resolution": resolution,
                "fps": fps,
                "codec": codec,
                "format": render_format,
                "duration_seconds": duration_seconds,
                "render_id": render_id,
            },
        )

        video_render = VideoRender.create(
            workflow_run_id=context.workflow_run_id,
            render_storage_key=artifact.storage_key,
            duration_seconds=duration_seconds,
            format=render_format,
        )

        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=self.definition.artifact_outputs,
            output={
                "video_render": asdict(video_render),
                "artifact": artifact.to_payload(),
                "render_request": render_record,
                "scene_plan": scene_plan,
                "voiceover": {
                    "provider": _optional_text(voiceover.get("provider")),
                    "audio_ref": audio_ref,
                    "audio_storage_key": _optional_text(
                        _pick(voiceover, "audio_storage_key", "audioStorageKey")
                    ),
                },
                "captions": {
                    "provider": _optional_text(captions.get("provider")),
                    "caption_storage_key": captions_ref,
                },
                "assets": list(assets),
                "workflow_snapshot": {
                    "workflow_run_id": context.workflow_run_id,
                    "workflow_config_id": context.workflow_config_id,
                    "module_name": context.module_name,
                    "enabled_modules": list(context.enabled_modules),
                    "disabled_modules": list(context.disabled_modules),
                },
            },
        )

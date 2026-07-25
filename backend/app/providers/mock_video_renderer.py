"""Deterministic mock video renderer provider."""

from __future__ import annotations

from app.domain.enums import ProviderType
from app.domain.types import JsonDict

from .interfaces import VideoRendererProvider, _stable_signature, _slugify


class MockVideoRendererProvider(VideoRendererProvider):
    provider_type = ProviderType.VIDEO_RENDERER

    def __init__(self, provider_name: str = "mock") -> None:
        self.provider_name = provider_name

    def render(
        self,
        scene_plan: JsonDict,
        audio_ref: str | None = None,
        captions_ref: str | None = None,
    ) -> JsonDict:
        signature = _stable_signature(
            {
                "provider": self.provider_name,
                "scene_plan": scene_plan,
                "audio_ref": audio_ref,
                "captions_ref": captions_ref,
            }
        )
        scenes = scene_plan.get("scenes") if isinstance(scene_plan, dict) else None
        scene_count = len(scenes) if isinstance(scenes, list) else 0
        return {
            "provider": self.provider_name,
            "video_ref": f"mock://video/{signature[:12]}.mp4",
            "status": "completed",
            "scene_count": scene_count,
            "scene_plan_label": _slugify(scene_plan.get("title", "scene-plan")) if isinstance(scene_plan, dict) else "scene-plan",
            "audio_ref": audio_ref,
            "captions_ref": captions_ref,
        }

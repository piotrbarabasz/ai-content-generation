"""Provider-free scene and whole-film preview coordination."""

from dataclasses import dataclass
from pathlib import Path

from app.domain.dependencies import content_fingerprint
from app.domain.timeline import TimelineRevision


def proxy_cache_key(timeline, renderer_identity):
    if not isinstance(timeline, TimelineRevision) or not isinstance(renderer_identity, dict):
        raise ValueError("Preview cache identity requires a timeline and renderer identity.")
    return content_fingerprint({"version": 1, "kind": "film_proxy",
                                "timeline": timeline.to_payload(), "renderer": renderer_identity})


@dataclass(frozen=True)
class ScenePreview:
    timeline_id: str
    scene_id: str
    image_path: Path
    audio_path: Path
    sample_rate: int
    frame_count: int


@dataclass(frozen=True)
class FilmPreview:
    cache_key: str
    timeline_id: str
    path: Path
    current: bool
    cached: bool


class PreviewService:
    """Build previews from an exact D018 snapshot and reject stale presentation."""

    def __init__(self, current_edit, media, renderer):
        self.current_edit, self.media, self.renderer = current_edit, media, renderer
        self._serial = 0
        self._cancel_requested = False

    def _is_current(self, timeline):
        edit = self.current_edit()
        if edit is None or edit.timeline != timeline:
            return False
        try:
            self.media.current(timeline)
        except (ValueError, FileNotFoundError):
            return False
        return True

    def is_current(self, timeline):
        if not isinstance(timeline, TimelineRevision):
            return False
        return self._is_current(timeline)

    def scene(self, timeline, scene_id):
        if not self._is_current(timeline):
            raise ValueError("Timeline media changed; refresh the timeline before previewing this scene.")
        result = self.media.scene(timeline, scene_id)
        if not self._is_current(timeline):
            raise ValueError("Timeline media changed while preparing the scene preview.")
        return result

    async def proxy(self, timeline, progress=lambda *_: None):
        if not self._is_current(timeline):
            raise ValueError("Timeline media changed; refresh the timeline before rendering a proxy.")
        self._serial += 1
        serial = self._serial
        self._cancel_requested = False
        identity = self.renderer.identity()
        key = proxy_cache_key(timeline, identity)
        cached = self.media.cached_proxy(key, timeline)
        if cached is not None:
            return FilmPreview(key, timeline.id, cached, self._is_current(timeline), True)

        def canceled():
            return self._cancel_requested or serial != self._serial

        root = self.media.stage(timeline)
        result = await self.renderer.render(timeline, root, canceled=canceled, progress=progress)
        path = self.media.publish_proxy(key, timeline, root, result, identity)
        current = not canceled() and self._is_current(timeline)
        return FilmPreview(key, timeline.id, path, current, False)

    def cancel(self):
        self._cancel_requested = True


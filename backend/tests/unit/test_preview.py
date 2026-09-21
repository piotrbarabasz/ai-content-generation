"""D046 snapshot/cache identity and stale-result coordination."""

import asyncio
from hashlib import sha256
import subprocess
import sys
from types import SimpleNamespace

from app.application.preview import PreviewService, proxy_cache_key
from app.domain.render_result import RenderedVideo
from tests.unit.test_timeline import compile_values, media


class PreviewMedia:
    def __init__(self, root):
        self.root = root
        self.cache = {}
        self.allowed = True
        self.stages = 0

    def current(self, timeline):
        if not self.allowed:
            raise ValueError("changed selection")

    def cached_proxy(self, key, timeline):
        return self.cache.get(key)

    def stage(self, timeline):
        self.stages += 1
        root = self.root / f"{timeline.id}-{self.stages}"
        root.mkdir()
        return root

    def publish_proxy(self, key, timeline, root, result, identity):
        path = root / "render.mp4"
        self.cache[key] = path
        return path


class Renderer:
    def __init__(self, changed=lambda: None):
        self.calls = 0
        self.changed = changed

    def identity(self):
        return {"provider": "fixture", "profile": "proxy-v1"}

    async def render(self, timeline, root, *, canceled, progress):
        self.calls += 1
        path = root / "render.mp4"
        path.write_bytes(b"validated proxy")
        self.changed()
        digest = sha256(path.read_bytes()).hexdigest()
        return RenderedVideo(timeline.id, digest, path.stat().st_size, timeline.total_frames,
                             timeline.video_duration, timeline.duration)


class CancelingRenderer(Renderer):
    def __init__(self):
        super().__init__()
        self.service = None

    async def render(self, timeline, root, *, canceled, progress):
        self.calls += 1
        self.service.cancel()
        if canceled():
            raise RuntimeError("fixture render canceled")
        raise AssertionError("Cancellation callback was not forwarded to the renderer.")


def test_cache_key_pins_full_timeline_and_renderer_identity():
    first = compile_values(media())
    changed = compile_values(media("b"))
    identity = {"provider": "fixture", "profile": "proxy-v1"}
    assert proxy_cache_key(first, identity) == proxy_cache_key(first, dict(identity))
    assert proxy_cache_key(first, identity) != proxy_cache_key(changed, identity)
    assert proxy_cache_key(first, identity) != proxy_cache_key(first, identity | {"profile": "proxy-v2"})


def test_proxy_reuses_cache_and_late_result_is_never_current(tmp_path):
    timeline = compile_values(media())
    active = SimpleNamespace(value=SimpleNamespace(timeline=timeline))
    storage = PreviewMedia(tmp_path)
    renderer = Renderer()
    service = PreviewService(lambda: active.value, storage, renderer)
    first = asyncio.run(service.proxy(timeline))
    second = asyncio.run(service.proxy(timeline))
    assert first.current and not first.cached
    assert second.current and second.cached and second.path == first.path
    assert renderer.calls == 1

    other = compile_values(media("b"))
    renderer.changed = lambda: setattr(active, "value", SimpleNamespace(timeline=other))
    storage.cache.clear()
    late = asyncio.run(service.proxy(timeline))
    assert not late.current and renderer.calls == 2


def test_changed_selected_media_rejects_cache_hit(tmp_path):
    timeline = compile_values(media())
    storage = PreviewMedia(tmp_path)
    renderer = Renderer()
    service = PreviewService(lambda: SimpleNamespace(timeline=timeline), storage, renderer)
    asyncio.run(service.proxy(timeline))
    storage.allowed = False
    try:
        asyncio.run(service.proxy(timeline))
    except ValueError as exc:
        assert "changed" in str(exc)
    else:
        raise AssertionError("Changed media must invalidate the cached preview.")
    assert renderer.calls == 1


def test_cancel_reaches_renderer_and_does_not_publish_cache(tmp_path):
    timeline = compile_values(media())
    storage = PreviewMedia(tmp_path)
    renderer = CancelingRenderer()
    service = PreviewService(lambda: SimpleNamespace(timeline=timeline), storage, renderer)
    renderer.service = service
    try:
        asyncio.run(service.proxy(timeline))
    except RuntimeError as exc:
        assert "canceled" in str(exc)
    else:
        raise AssertionError("Canceled render must not succeed.")
    assert renderer.calls == 1 and storage.cache == {}


def test_application_import_does_not_load_concrete_media_storage_or_qt():
    command = """
import sys
from app.application.preview import PreviewService
for name in ('app.providers', 'app.storage', 'app.runtime', 'sqlite3', 'PIL', 'PySide6'):
    assert not any(m == name or m.startswith(name + '.') for m in sys.modules), name
"""
    result = subprocess.run([sys.executable, "-c", command], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

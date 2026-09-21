"""D046 Qt controls bind current previews and suppress stale film playback."""

import asyncio
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.application.preview import FilmPreview
from app.desktop.preview_panel import PreviewPanel
from tests.unit.test_timeline import compile_values, media


@pytest.fixture
def qt():
    return QApplication.instance() or QApplication([])


class Services:
    def __init__(self, path, current):
        self.path, self.current = path, current

    def is_current(self, timeline):
        return True

    async def proxy(self, timeline, progress):
        progress("encoding", 1, 1)
        await asyncio.sleep(0)
        return FilmPreview("a" * 64, timeline.id, self.path, self.current, False)

    def cancel(self):
        pass


@pytest.mark.parametrize("current", [True, False])
def test_proxy_control_only_loads_current_result(qt, tmp_path, current):
    path = tmp_path / "proxy.mp4"
    path.write_bytes(b"fixture")
    timeline = compile_values(media())
    panel = PreviewPanel()
    panel.bind(Services(path, current))
    panel.timeline_changed(SimpleNamespace(timeline=timeline))
    panel.show()
    try:
        QTest.mouseClick(panel.buttons["Render/play film proxy"], Qt.LeftButton)
        while panel.busy:
            panel._tick()
        if current:
            assert panel.player.source().toLocalFile().replace("\\", "/") == str(path).replace("\\", "/")
            assert "current film proxy" in panel.status.text()
            panel.timeline_changed(SimpleNamespace(timeline=timeline))
            assert panel.player.source().isEmpty() and panel.video.isHidden()
        else:
            assert panel.player.source().isEmpty()
            assert "stale" in panel.status.text()
    finally:
        panel.close()

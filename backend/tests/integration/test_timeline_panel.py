"""Qt commands round-trip real project timeline snapshots without providers."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.desktop.timeline_panel import TimelinePanel
from app.desktop.timeline_composition import compose_timeline
from app.desktop.editor import ProjectEditor
from app.desktop.__main__ import LocalProjects
from tests.integration.test_timeline import prepared, setup  # noqa: F401


@pytest.fixture
def qt():
    return QApplication.instance() or QApplication([])


def test_commands_display_persisted_order_offsets_and_reject_bad_range(qt, setup, prepared):
    service = compose_timeline(setup[0])
    panel = TimelinePanel()
    panel.bind(service)
    panel.show()
    try:
        for _ in prepared[-1]:
            QTest.mouseClick(panel.buttons["Add selected clip"], Qt.LeftButton)
        persisted = service.current()
        assert panel.table.rowCount() == len(persisted.timeline.clips)
        panel.table.selectRow(0)
        pinned = persisted.timeline.clips[0].media
        QTest.mouseClick(panel.buttons["Move later"], Qt.LeftButton)
        persisted = service.current()
        assert persisted.timeline.clips[1].media == pinned
        for row, clip in enumerate(persisted.timeline.clips):
            assert panel.table.item(row, 0).text() == clip.media.scene_id
            assert panel.table.item(row, 2).text() == str(clip.audio_offset)
            assert panel.table.item(row, 3).text() == str(clip.duration)
        panel.end.setCurrentIndex(panel.start.currentIndex())
        QTest.mouseClick(panel.apply, Qt.LeftButton)
        assert "boundaries" in panel.status.text()
        assert service.current() == persisted
        panel.bind(compose_timeline(setup[0]))
        assert panel.edit == persisted
        QTest.mouseClick(panel.buttons["Remove clip"], Qt.LeftButton)
        assert panel.table.rowCount() == len(persisted.timeline.clips) - 1
    finally:
        panel.close()


def test_editor_composition_and_project_switch_clear_timeline(qt, tmp_path):
    window = ProjectEditor(LocalProjects(), timeline_factory=compose_timeline)
    try:
        window.load_project(tmp_path / "one", create=True)
        first = window.timeline.services.project_id
        window.load_project(tmp_path / "two", create=True)
        assert window.timeline.services.project_id != first
        assert window.timeline.edit is None and window.timeline.table.rowCount() == 0
        assert window.timeline.buttons["Add selected clip"].isEnabled()
    finally:
        window.close()

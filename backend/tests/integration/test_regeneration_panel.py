"""Qt regeneration commands enforce project lifetime and report actual outcomes."""

import asyncio
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from app.application.regeneration import RegenerationService, RegenerationStep, StageState
from app.desktop.editor import ProjectEditor
from app.desktop.__main__ import LocalProjects


def test_editor_guards_drafts_and_busy_project_lifetime(tmp_path):
    qt = QApplication.instance() or QApplication([])
    async def execute():
        await asyncio.sleep(0)
    service = RegenerationService(lambda: (
        RegenerationStep("output", (), lambda: StageState("review", "Select a retained variant"), execute),),
        lambda: "revision")
    window = ProjectEditor(LocalProjects(), regeneration_factory=lambda *args: service)
    try:
        window.load_project(tmp_path / "first", create=True)
        window.text.setPlainText("Unsaved draft")
        window.regeneration.start()
        assert not window.regeneration.busy
        assert "Save" in window.regeneration.status.text()
        window.discard()
        window.regeneration.start()
        assert window.regeneration.busy
        assert all(not window.tabs.isTabEnabled(index) for index in range(5))
        assert window.tabs.isTabEnabled(5) and window.regeneration.isEnabled()
        assert not window.close()
        old_session = window.session
        window.load_project(tmp_path / "second", create=True)
        assert window.session is old_session
        for _ in range(20):
            if not window.regeneration.busy:
                break
            window.regeneration.tick()
        assert not window.regeneration.busy and window.centralWidget().isEnabled()
        assert "review" in window.regeneration.status.text()
    finally:
        window.dirty = False
        window.close()


def test_default_composition_and_cancel_before_first_tick(tmp_path):
    from app.desktop.regeneration_composition import compose_regeneration
    from app.desktop.timeline_composition import compose_timeline
    qt = QApplication.instance() or QApplication([])
    window = ProjectEditor(LocalProjects(), timeline_factory=compose_timeline,
                           regeneration_factory=compose_regeneration)
    try:
        window.load_project(tmp_path / "project", create=True)
        assert window.regeneration.services is not None
        window.regeneration.start()
        window.regeneration.cancel()
        for _ in range(20):
            if not window.regeneration.busy:
                break
            window.regeneration.tick()
        assert not window.regeneration.busy
        assert "Canceled" in window.regeneration.status.text()
        assert window.centralWidget().isEnabled()
    finally:
        window.close()

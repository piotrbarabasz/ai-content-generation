"""Offline Qt user flows with fake ports and actual temporary SQLite projects."""

import os
import threading
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="Install the desktop extra for Qt editor tests")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDockWidget, QMessageBox

from app.application.script_generation import ScriptGenerationService
from app.desktop.__main__ import LocalProjects
from app.desktop.editor import ProjectEditor
from app.domain.script import ScriptRevision


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor(qt, tmp_path):
    window = ProjectEditor(LocalProjects())
    window.show()
    window.load_project(tmp_path / "project", create=True)
    yield window
    if window.worker:
        assert window.worker.wait(5000)
        qt.processEvents()
    window.dirty = False
    window.close()


def click(window, name):
    QTest.mouseClick(window.buttons[name], Qt.LeftButton)


def append(window, title, text):
    click(window, "New section")
    window.title.setText(title)
    window.text.setPlainText(text)
    click(window, "Save")
    assert not window.dirty, window.status.text()


def wait_for_generation(window, qt):
    for _ in range(500):
        qt.processEvents()
        if window.worker is None:
            return
        QTest.qWait(10)
    pytest.fail("Generation did not finish")


def test_create_edit_split_merge_reorder_and_reopen(editor, tmp_path, qt):
    append(editor, "A", "Alpha")
    append(editor, "B", "Bravo Charlie")
    original = editor.snapshot
    editor.text.setPlainText("Bravo Delta")
    click(editor, "Save")
    edited = editor.snapshot
    assert edited.sections[1].section_id == original.sections[1].section_id
    cursor = editor.text.textCursor()
    cursor.setPosition(6)
    editor.text.setTextCursor(cursor)
    click(editor, "Split at cursor")
    assert [s.text for s in editor.snapshot.sections] == ["Alpha", "Bravo ", "Delta"]
    editor.sections.item(1).setSelected(True)
    editor.sections.item(2).setSelected(True)
    click(editor, "Merge selected")
    assert [s.text for s in editor.snapshot.sections] == ["Alpha", "Bravo \n\nDelta"]
    before = editor.snapshot
    click(editor, "Move up")
    assert editor.snapshot.sections == tuple(reversed(before.sections))
    final = editor.snapshot
    editor.close()
    reopened = ProjectEditor(LocalProjects())
    reopened.load_project(tmp_path / "project")
    try:
        assert reopened.snapshot == final
        assert reopened.session.repository.get_script(original.id) == original
        assert reopened.session.repository.get_script(edited.id) == edited
    finally:
        reopened.close()


def test_dirty_selection_close_project_switch_and_discard(editor, tmp_path):
    append(editor, "A", "Alpha")
    append(editor, "B", "Bravo")
    original = editor.snapshot
    editor.text.setPlainText("Unsaved B")
    editor.sections.setCurrentRow(0)
    assert editor.sections.currentRow() == 1
    assert editor.text.toPlainText() == "Unsaved B"
    assert not editor.close()
    editor.load_project(tmp_path / "other", create=True)
    assert not (tmp_path / "other").exists()
    assert editor.snapshot == original
    click(editor, "Move up")
    assert editor.snapshot == original
    click(editor, "Discard draft")
    assert editor.text.toPlainText() == "Bravo"
    assert not editor.dirty


def test_invalid_save_and_stale_snapshot_preserve_draft(editor):
    append(editor, "A", "Alpha")
    original = editor.snapshot
    editor.text.setPlainText(" ")
    click(editor, "Save")
    assert editor.dirty and editor.text.toPlainText() == " "
    assert editor.session.active_script == original
    editor.text.setPlainText("My unsaved edit")
    external = editor.session.edit_section(original.sections[0].section_id, text="External edit")
    click(editor, "Save")
    assert editor.session.active_script == external
    assert editor.text.toPlainText() == "My unsaved edit"
    assert editor.dirty and "changed" in editor.status.text()


def test_invalid_merge_and_split_preserve_saved_selection(editor):
    for name in "ABC":
        append(editor, name, name * 2)
    original = editor.snapshot
    editor.sections.setCurrentRow(0)
    editor.sections.item(2).setSelected(True)
    click(editor, "Merge selected")
    assert editor.snapshot == original
    assert "adjacent" in editor.status.text()
    cursor = editor.text.textCursor()
    cursor.setPosition(0)
    editor.text.setTextCursor(cursor)
    click(editor, "Split at cursor")
    assert editor.snapshot == original
    assert "boundary" in editor.status.text()


def test_unicode_cursor_split_uses_python_boundary(editor):
    append(editor, "Unicode", "A\U0001f600 B")
    cursor = editor.text.textCursor()
    cursor.setPosition(3)  # A + surrogate pair
    editor.text.setTextCursor(cursor)
    click(editor, "Split at cursor")
    assert [s.text for s in editor.snapshot.sections] == ["A\U0001f600", " B"]


def test_source_crlf_survives_clean_save_and_split(editor):
    revision = ScriptGenerationService(editor.session).append_text(
        "First\r\nSecond", title="Lines", expected_active_revision_id=editor.snapshot.id)
    editor.discard()
    click(editor, "Save")
    assert editor.session.active_script == revision
    cursor = editor.text.textCursor()
    cursor.setPosition(6)
    editor.text.setTextCursor(cursor)
    click(editor, "Split at cursor")
    assert [s.text for s in editor.snapshot.sections] == ["First\r\n", "Second"]


def test_widget_import_does_not_load_storage_or_providers():
    source = """
import sys
from app.desktop.editor import ProjectEditor
for module in ('app.providers', 'app.storage', 'app.tts', 'torch', 'fastapi'):
    assert module not in sys.modules, module
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    result = subprocess.run([sys.executable, "-c", source], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


class Provider:
    def __init__(self, payload=None):
        self.payload = payload or {"sections": [{"title": "Generated", "role": "body", "text": "Story"}]}
        self.entered, self.release = threading.Event(), threading.Event()

    def generate_structured(self, prompt, schema):
        self.thread = threading.get_ident()
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("Test provider timed out")
        return self.payload


@pytest.mark.parametrize("outcome", ["success", "invalid", "stale"])
def test_background_generation_commits_on_gui_thread_and_preserves_errors(editor, qt, outcome):
    provider = Provider({"bad": True} if outcome == "invalid" else None)
    editor.provider = provider
    editor.request.setPlainText("Generate a story")
    original = editor.snapshot
    editor.generate()
    assert provider.entered.wait(2)
    try:
        qt.processEvents()
        assert editor.worker is not None and not editor.text.isEnabled()
        assert not editor.close()
        if outcome == "stale":
            ScriptGenerationService(editor.session).append_text("External", title="Other",
                                                               expected_active_revision_id=original.id)
    finally:
        provider.release.set()
    wait_for_generation(editor, qt)
    assert provider.thread != threading.get_ident()
    assert editor.text.isEnabled()
    assert editor.request.toPlainText() == "Generate a story"
    if outcome == "success":
        assert editor.snapshot.sections[0].text == "Story"
        assert editor.session.repository.get_script(original.id) == original
    elif outcome == "invalid":
        assert editor.session.active_script == original
        assert "sections" in editor.status.text()
    else:
        assert editor.session.active_script.sections[0].text == "External"
        assert "changed" in editor.status.text()


def test_fake_application_port_reports_save_and_open_failures_without_losing_draft(qt):
    class Session:
        active_script = ScriptRevision.create(project_id="fake")
        project = SimpleNamespace(name="Fake", language="en")
        def save_script(self, revision, **kwargs):
            raise OSError("Disk unavailable")
        def close(self):
            pass

    class Projects:
        def open(self, path):
            if path == "bad":
                raise OSError("Cannot open project")
            return Session()

    window = ProjectEditor(Projects())
    window.load_project("ok")
    snapshot = window.snapshot
    window.title.setText("Draft")
    window.text.setPlainText("Keep this")
    window.save()
    assert window.dirty and window.text.toPlainText() == "Keep this"
    assert window.snapshot == snapshot and window.status.text() == "Disk unavailable"
    window.discard()
    window.load_project("bad")
    assert window.snapshot == snapshot and window.status.text() == "Cannot open project"
    window.close()


def test_generation_replacement_requires_confirmation(editor, monkeypatch):
    append(editor, "A", "Alpha")
    editor.provider = Provider()
    editor.request.setPlainText("Replacement")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    editor.generate()
    assert editor.worker is None and editor.snapshot.sections[0].text == "Alpha"


def test_stage_tabs_own_existing_workflow_panels(editor):
    assert [editor.tabs.tabText(i) for i in range(editor.tabs.count())] == [
        "Script", "Voice", "Scenes", "Visuals", "Timeline", "Export",
    ]
    assert editor.script_tab.isAncestorOf(editor.sections)
    assert editor.voice_tab.isAncestorOf(editor.audio)
    assert editor.tabs.widget(2) is editor.scene_plans
    assert editor.visuals_tab.isAncestorOf(editor.visuals)
    assert editor.timeline_tab.isAncestorOf(editor.timeline)
    assert editor.timeline_tab.isAncestorOf(editor.preview)
    assert editor.export_tab.isAncestorOf(editor.regeneration)
    assert editor.findChildren(QDockWidget) == []


def test_shared_section_navigation_updates_all_stages_and_blocks_dirty_change(editor):
    append(editor, "Opening", "First")
    append(editor, "Body", "Second")
    editor.section_choice.setCurrentIndex(0)
    selected = editor.snapshot.sections[0]
    assert editor.selected_id == selected.section_id
    assert editor.title.text() == "Opening"
    assert editor.audio.section == selected
    assert editor.scene_plans.section == selected
    assert editor.visuals.section == selected
    assert "Opening" in editor.voice_section.text() and "Opening" in editor.visuals_section.text()

    editor.text.setPlainText("Unsaved")
    editor.section_choice.setCurrentIndex(1)
    assert editor.selected_id == selected.section_id
    assert editor.section_choice.currentData() == selected.section_id
    assert editor.text.toPlainText() == "Unsaved"
    assert "Save or discard" in editor.status.text()
    editor.discard()


def test_project_load_binds_one_scene_service_to_planning_and_visuals(qt, tmp_path):
    services = object()
    window = ProjectEditor(LocalProjects(), scene_factory=lambda session: services)
    try:
        window.load_project(tmp_path / "project", create=True)
        assert window.scene_plans.services is services
        assert window.visuals.services is services
    finally:
        window.close()

"""D021 offline Qt tests: responsiveness, cancellation and retained playback."""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from app.desktop.audio_panel import AudioPanel
from app.desktop.audio_services import AudioChoice, PlaybackAudio
from app.desktop.editor import ProjectEditor
from app.desktop.__main__ import LocalProjects
from tests.unit.test_t086 import _wav


class Services:
    def __init__(self):
        self.canceled = False
        self.calls = []
        self.release = False

    def choices(self, language):
        return (AudioChoice("Example voice", "mock", "v3", "builtin", language),)

    async def preview(self, choice, text):
        self.calls.append(("preview", choice, text))
        while not self.release:
            await asyncio.sleep(.01)
        return PlaybackAudio(_wav(), False, "Preview")

    async def generate(self, section, choice):
        self.calls.append(("generate", choice, section))
        while not self.release and not self.canceled:
            await asyncio.sleep(.01)
        return "canceled" if self.canceled else "completed"

    def cancel(self):
        self.canceled = True

    def progress(self):
        return "synthesis: 1/3"

    def playback(self, section, variant, choice):
        self.calls.append(("playback", variant, section, choice))
        return PlaybackAudio(_wav(), True, "STALE — retained recording")


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def wait_until(predicate):
    for _ in range(400):
        if predicate():
            return
        QTest.qWait(10)
    pytest.fail("Qt operation did not complete")


@pytest.fixture
def panel(qt):
    value = AudioPanel()
    service = Services()
    value.bind(service, "en")
    value.select_section(SimpleNamespace(id="B1", section_id="B"))
    yield value, service
    service.release = True
    if value.busy:
        value.cancel()
        wait_until(lambda: not value.busy)
    value.stop()
    value.close()


def test_preview_and_production_capture_same_voice_and_do_not_block_qt(panel):
    widget, service = panel
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    widget.preview()
    wait_until(lambda: len(ticks) > 3 and bool(service.calls))
    assert widget.busy and not widget.voices.isEnabled()
    service.release = True
    wait_until(lambda: not widget.busy)
    widget.generate()
    wait_until(lambda: not widget.busy)
    timer.stop()
    assert service.calls[0][1] == service.calls[1][1]
    assert widget.status.text() == "completed"


def test_cancel_waits_for_preview_cleanup_and_does_not_autoplay(panel):
    widget, service = panel
    widget.preview()
    widget.cancel()
    QTest.qWait(50)
    assert widget.busy and widget.buffer is None
    service.release = True
    wait_until(lambda: not widget.busy)
    assert widget.status.text() == "Canceled" and widget.buffer is None


def test_generation_cancel_and_explicit_processed_stale_playback(panel):
    widget, service = panel
    widget.generate()
    wait_until(lambda: bool(service.calls))
    widget.cancel()
    wait_until(lambda: not widget.busy)
    assert widget.status.text() == "Canceled"
    widget.variant.setCurrentIndex(1)
    widget.play()
    assert service.calls[-1][1] == "processed"
    assert "STALE" in widget.status.text() and widget.buffer is not None


def test_missing_variant_error_never_falls_back(panel):
    widget, service = panel
    def missing(*args):
        raise ValueError("No selected processed audio")
    service.playback = missing
    widget.variant.setCurrentIndex(1)
    widget.play()
    assert widget.buffer is None and "No selected processed" in widget.status.text()


def test_generation_keeps_editor_editable_and_blocks_project_close(qt, tmp_path):
    service = Services()
    window = ProjectEditor(LocalProjects(), audio_factory=lambda session: service)
    window.load_project(tmp_path / "project", create=True)
    window.title.setText("A")
    window.text.setPlainText("Original")
    window.save()
    window.audio.generate()
    wait_until(lambda: bool(service.calls))
    try:
        assert window.text.isEnabled()
        window.text.setPlainText("Edited during audio generation")
        window.save()
        assert window.session.active_script.sections[0].text == "Edited during audio generation"
        assert service.calls[0][2].text == "Original"
        assert not window.close()
        window.load_project(tmp_path / "other", create=True)
        assert not (tmp_path / "other").exists()
    finally:
        window.audio.cancel()
        wait_until(lambda: not window.audio.busy)
        window.close()


def test_dirty_editor_does_not_generate_from_unsaved_text(qt, tmp_path):
    service = Services()
    window = ProjectEditor(LocalProjects(), audio_factory=lambda session: service)
    window.load_project(tmp_path, create=True)
    window.title.setText("Draft")
    window.text.setPlainText("Unsaved")
    window.audio.generate()
    assert not window.audio.busy
    window.discard()
    window.close()


def test_async_process_io_keeps_qt_timer_alive(panel):
    widget, service = panel
    async def subprocess_audio(section, choice):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(.15); print('finished')",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        assert process.returncode == 0 and stdout.strip() == b"finished", stderr
        return "completed"
    service.generate = subprocess_audio
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    widget.generate()
    wait_until(lambda: not widget.busy)
    timer.stop()
    assert widget.status.text() == "completed" and len(ticks) > 3


def test_reference_intake_approval_and_rejection_refresh_voice_selection(qt, tmp_path, monkeypatch):
    class ReferenceServices(Services):
        def __init__(self):
            super().__init__()
            self.source = None
            self.decision = None

        def reference_entries(self):
            return () if self.source is None else ((self.source, self.decision),)

        def import_reference(self, path):
            self.source = SimpleNamespace(artifact_id="reference_1", source_name="speaker.wav",
                                          duration_seconds=1.0)
            return self.source

        def approve_reference(self, artifact_id, label):
            assert artifact_id == "reference_1"
            self.decision = SimpleNamespace(status="approved", label=label)

        def reject_reference(self, artifact_id, label):
            assert artifact_id == "reference_1"
            self.decision = SimpleNamespace(status="rejected", label=label)

        def choices(self, language):
            choices = list(super().choices(language))
            if self.decision is not None and self.decision.status == "approved":
                choices.append(AudioChoice("Approved reference", "mock", "v3", "reference", language,
                                           "reference_1", "a" * 64, self.decision.label))
            return tuple(choices)

    path = tmp_path / "speaker.wav"
    path.write_bytes(_wav())
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), "WAV audio (*.wav)"))
    widget, service = AudioPanel(), ReferenceServices()
    try:
        widget.bind(service, "en")
        widget.import_reference()
        assert widget.references.count() == 1 and widget.voices.count() == 1
        widget.approval_label.setText("speaker-consent")
        widget.approve_reference()
        assert widget.voices.count() == 2 and "approved" in widget.references.currentText()
        widget.approval_label.setText("consent-withdrawn")
        widget.reject_reference()
        assert widget.voices.count() == 1 and "rejected" in widget.references.currentText()
    finally:
        widget.close()

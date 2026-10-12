"""Offscreen deliberate review flows, retained metadata and independent ZIP/SRT copy."""

import asyncio
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QFileDialog

from app.desktop.publish_panel import PublishPanel
from tests.integration.test_publishing_package import publishing, setup, prepared, approved, add_alignment, branded_source


@pytest.fixture(scope="module")
def qt(): return QApplication.instance() or QApplication([])


def test_no_automatic_generation_edit_approval_and_candidate_loading_are_deliberate(publishing, qt):
    f = publishing; s = f.service
    panel = PublishPanel(); panel.bind(s)
    assert panel.source.count() == 1 and not s.artifacts.history("candidates")
    assert not panel.alignment_button.isEnabled()
    asyncio.run(panel.generate())
    assert "not AI" in panel.candidates.currentText()
    panel.description.setPlainText("Manual retained description"); panel.edited()
    panel.use_chapters.setChecked(False); panel.approved.setChecked(True); panel.save()
    saved = panel.revision
    asyncio.run(panel.generate())
    assert panel.description.toPlainText() == "Manual retained description" and panel.revision == saved
    panel.load_candidate()
    assert not panel.approved.isChecked() and panel.dirty
    panel.deleteLater()


def test_reopen_selected_thumbnail_zip_save_copy_and_srt_preview(publishing, qt, tmp_path, monkeypatch):
    f = publishing; s = f.service; add_alignment(f)
    revision = approved(f, subtitles=True)
    panel = PublishPanel(); panel.bind(s)
    assert panel.revision == revision and panel.approved.isChecked()
    assert panel.thumbnail_choices.currentData() == panel.draft["thumbnail_id"]
    assert panel.headline.text() == "Known project facts" and panel.preview.pixmap() is not None
    from PySide6.QtGui import QPixmap
    from PySide6.QtCore import Qt
    expected = QPixmap(); expected.loadFromData(s.verify_thumbnail(s.artifacts.one(panel.draft["thumbnail_id"], "thumbnail")))
    assert panel.preview.pixmap().toImage() == expected.scaled(480, 270, Qt.KeepAspectRatio, Qt.SmoothTransformation).toImage()
    assert "First two three" in panel.subtitle_preview.toPlainText()
    panel.build_package(); original = panel.package
    destination = tmp_path / "copy.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(destination), ""))
    assert panel.save_package_copy() == destination and destination.read_bytes() == s.artifacts.bytes(original)
    srt = tmp_path / "copy.srt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(srt), ""))
    assert panel.export_srt() == srt and srt.read_bytes().decode("utf-8") == panel.subtitle_preview.toPlainText().replace("\n", "\r\n")
    panel2 = PublishPanel(); panel2.bind(s)
    assert panel2.verified_package() == original and panel2.description.toPlainText() == panel.description.toPlainText()
    panel.deleteLater(); panel2.deleteLater()


def test_source_change_preserves_approved_edits_but_blocks_old_package_until_explicit_review(publishing, qt):
    f = publishing; s = f.service; approved(f); branded_source(f)
    panel = PublishPanel(); panel.bind(s)
    before = panel.description.toPlainText()
    panel.source.setCurrentIndex(panel.source.findData("branded"))
    assert panel.description.toPlainText() == before and "STALE" in panel.status.text()
    with pytest.raises(ValueError, match="STALE"): panel.save()
    with pytest.raises(ValueError, match="STALE"): panel.validate()
    panel.rebase()
    assert not panel.approved.isChecked() and panel.draft["subtitle_id"] is None and panel.description.toPlainText() == before
    panel.deleteLater()


def test_thumbnail_controls_generate_real_previews_and_require_selection(publishing, qt):
    f = publishing; s = f.service; approved(f)
    panel = PublishPanel(); panel.bind(s)
    panel.headline.setText("A different headline"); panel.thumbnail_edited()
    assert panel.thumbnail_dirty and not panel.approved.isChecked()
    panel.generate_thumbnails()
    assert panel.thumbnail_choices.count() == 3 and panel.preview.pixmap().width() > 0
    panel.select_thumbnail(); panel.approved.setChecked(True); panel.save(); panel.validate()
    assert not panel.thumbnail_dirty and "READY" in panel.status.text()
    panel.deleteLater()


def test_provider_errors_visible_and_old_edits_survive(publishing, qt):
    f = publishing; s = f.service; approved(f)
    def error(*_): raise RuntimeError("provider unavailable")
    from types import SimpleNamespace
    s.llm = SimpleNamespace(generate_structured=error)
    panel = PublishPanel(); panel.bind(s); before = panel.description.toPlainText()
    panel.start(panel.generate())
    import time
    deadline = time.monotonic() + 5
    while panel.busy and time.monotonic() < deadline: qt.processEvents(); time.sleep(.005)
    assert not panel.busy and "provider unavailable" in panel.status.text()
    assert panel.description.toPlainText() == before
    panel.deleteLater()


def test_editor_deferred_binding_preserves_drafts_without_verifying_sources_or_calling_providers(publishing, qt):
    f = publishing; s = f.service; revision = approved(f)
    calls = []
    real = s.sources
    s.sources = lambda: calls.append("sources") or real()
    panel = PublishPanel(); panel.bind(s, defer_sources=True)
    assert calls == [] and panel.description.toPlainText() == s.artifacts.read(revision)["metadata"]["description"]
    panel.activate()
    assert calls == ["sources"] and panel.source.count() == 1
    panel.deleteLater()


def test_loading_metadata_candidate_preserves_unsaved_chapter_and_thumbnail_edits(publishing, qt):
    f = publishing; s = f.service; approved(f)
    panel = PublishPanel(); panel.bind(s)
    panel.chapter_editor.setPlainText("00:00 Manual chapter\n00:10 Another\n00:20 Last")
    panel.headline.setText("Unsaved thumbnail headline"); panel.thumbnail_edited()
    old_selection = panel.draft["thumbnail_id"]
    asyncio.run(panel.generate()); panel.load_candidate()
    assert panel.chapter_editor.toPlainText().startswith("00:00 Manual chapter")
    assert panel.headline.text() == "Unsaved thumbnail headline" and panel.thumbnail_dirty
    assert panel.draft["thumbnail_id"] == old_selection and not panel.approved.isChecked()
    panel.deleteLater()

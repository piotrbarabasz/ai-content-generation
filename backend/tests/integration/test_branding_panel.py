import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from pathlib import Path

import pytest
pytest.importorskip("PySide6")
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from app.desktop.branding_panel import BrandingPanel
from tests.unit.test_branding import finishing


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def wait(widget):
    for _ in range(600):
        if not widget.busy:
            return
        QTest.qWait(10)
    pytest.fail("Finishing UI did not complete")


def test_panel_build_play_copy_and_restore_retained_preferences(qt, finishing, tmp_path, monkeypatch):
    f = finishing
    f.branding.save(f.config)
    panel = BrandingPanel(); panel.bind(f.service); panel.group.setChecked(True)
    assert panel.channel_name.text() == "Atlas"
    assert not panel.placement.model().item(1).isEnabled()
    assert "missing" in panel.mapping.text()
    assert "Narrative duration: 4.00s" in panel.details.text()
    assert "Estimated branded delivery: 22.00s" in panel.details.text()
    panel.build(); wait(panel)
    assert "ready" in panel.status.text().lower() and panel.play_button.isEnabled()
    opened = []
    monkeypatch.setattr("app.desktop.branding_panel.QDesktopServices.openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    assert panel.play()
    assert Path(opened[0]) == f.store._artifact_path(panel.output.storage_key)
    destination = tmp_path / "copy.mp4"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(destination), "MP4"))
    assert panel.save_copy() == destination
    assert destination.read_bytes() == f.store.read_artifact(panel.output.storage_key)
    panel.cta.setText("Discover more"); panel.save()
    assert not panel.play_button.isEnabled() and f.branding.current().cta == "Discover more"
    restored = BrandingPanel(); restored.bind(f.service)
    assert restored.cta.text() == "Discover more" and restored.logo_id == f.config.logo_id
    assert f.selected[0] == f.source
    panel.close(); restored.close()


def test_finishing_ui_cancel_preserves_original_and_no_published_output(qt, finishing):
    f = finishing; f.branding.save(f.config)
    panel = BrandingPanel(); panel.bind(f.service)
    panel.build(); panel.cancel(); wait(panel)
    assert "canceled" in panel.status.text().lower()
    assert not f.branding.history("branded_video") and f.selected[0] == f.source
    panel.close()


def test_branded_copy_cannot_replace_immutable_original(qt, finishing, monkeypatch):
    f = finishing; f.branding.save(f.config)
    panel = BrandingPanel(); panel.bind(f.service); panel.build(); wait(panel)
    original = f.store.read_artifact(f.source.storage_key)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(f.store._artifact_path(f.source.storage_key)), "MP4"))
    assert panel.save_copy() is None
    assert "outside the immutable artifact directory" in panel.status.text()
    assert f.store.read_artifact(f.source.storage_key) == original
    panel.close()

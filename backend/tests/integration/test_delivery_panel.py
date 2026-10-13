import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from pathlib import Path

import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QFileDialog
from PySide6.QtTest import QTest

from app.desktop.delivery_panel import DeliveryPanel
from app.domain.delivery import DeliveryConfig
from tests.unit.test_delivery_export import delivery, loudness
from tests.unit.test_branding import finishing


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def wait(panel):
    for _ in range(600):
        if not panel.busy: return
        QTest.qWait(10)
    pytest.fail("Delivery UI did not complete")


def test_delivery_build_report_play_copy_and_preferences(qt, delivery, tmp_path, monkeypatch):
    d = delivery; panel = DeliveryPanel(); panel.bind(d.service)
    assert panel.preset.currentData() == "youtube_hq" and panel.normalize.isChecked()
    assert panel.crf.value() == 18 and panel.lufs.value() == -16 and panel.peak.value() == -2
    assert "Verified original" in panel.source_details.text()
    assert not panel.source.model().item(1).isEnabled()
    panel.build(); wait(panel)
    assert "PASS" in panel.status.text() and "Actual loudness: -16.0" in panel.report.text()
    opened = []
    monkeypatch.setattr("app.desktop.delivery_panel.QDesktopServices.openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    assert panel.play() and Path(opened[0]) == d.f.store._artifact_path(panel.output.storage_key)
    target = tmp_path / "delivery-copy.mp4"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(target), "MP4"))
    assert panel.save_copy() == target
    assert target.read_bytes() == d.f.store.read_artifact(panel.output.storage_key)
    panel.crf.setValue(20)
    assert not panel.play_button.isEnabled() and d.artifacts.current().crf == 20
    restored = DeliveryPanel(); restored.bind(d.service)
    assert restored.crf.value() == 20
    panel.close(); restored.close()


def test_review_and_cancel_are_visible_without_losing_source(qt, delivery):
    d = delivery; d.process.after = loudness(-19, -.5)
    panel = DeliveryPanel(); panel.bind(d.service); panel.build(); wait(panel)
    assert "REVIEW" in panel.status.text() and "true-peak" in panel.report.text()
    retained = panel.output
    panel.crf.setValue(20); panel.build(); panel.cancel(); wait(panel)
    assert "canceled" in panel.status.text().lower()
    assert d.artifacts.verify(retained) == retained and d.f.selected[0] == d.f.source
    panel.close()


def test_source_copy_controls_and_branded_selector(qt, delivery):
    d = delivery
    import asyncio
    asyncio.run(d.f.service.build(d.f.config))
    panel = DeliveryPanel(); panel.bind(d.service)
    assert panel.source.model().item(1).isEnabled()
    panel.source.setCurrentIndex(1); panel.preset.setCurrentIndex(panel.preset.findData("source"))
    assert not panel.normalize.isEnabled() and not panel.crf.isEnabled()
    panel.build(); wait(panel)
    assert "SOURCE_COPY" in panel.status.text() and "source copy" in panel.report.text()
    assert panel.output.metadata["delivery"]["settings"]["source"] == "branded"
    panel.close()


def test_retained_mastering_settings_survive_widget_rebinding(qt, delivery):
    d = delivery; config = DeliveryConfig(target_lufs=-18, target_peak=-3, target_lra=8, crf=22)
    d.artifacts.save(config)
    panel = DeliveryPanel(); panel.bind(d.service)
    assert panel.config() == config
    panel.close()

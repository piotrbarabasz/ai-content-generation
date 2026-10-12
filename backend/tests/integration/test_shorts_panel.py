import asyncio
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication
from app.desktop.shorts_panel import ShortsPanel
from tests.unit.test_shorts import service, analyzed, ready

@pytest.fixture
def panel(service):
    app=QApplication.instance() or QApplication([])
    p=ShortsPanel();p.bind(service)
    service.publishing.sources=lambda:[('original',service.fixture_source,service.publishing.source('original')[1])]
    yield p
    p.player.stop();p.close()


def test_workspace_restore_crop_controls_and_edit_revision(panel):
    s=panel.service;values=analyzed(s);panel.activate()
    assert panel.choices.count()==3 and panel.begin.count()==10
    panel.mode.setCurrentText('fill');panel.focal.setValue(.2);panel.hook.setText('Known fact');panel.hook_on.setChecked(True)
    panel.save();panel.decision(True)
    revision=panel.revision
    panel.bind(s);panel.activate()
    assert panel.revision.artifact_id==revision.artifact_id and panel.mode.currentText()=='fill'
    assert panel.focal.value()==.2 and panel.hook.text()=='Known fact' and panel.hook_on.isChecked()
    assert s.accepted(values[0]['id'])


def test_busy_cancel_and_dirty_export_guard(panel):
    analyzed(panel.service);panel.activate();panel.title.setText('Manual title')
    with pytest.raises(ValueError,match='Save'):panel.output()
    async def pending():
        while not panel.canceled:await asyncio.sleep(.01)
    panel.start(pending());assert panel.busy and not panel.body.isEnabled()
    panel.cancel()
    for _ in range(10):
        panel.tick()
        if not panel.busy:break
    assert not panel.busy and panel.body.isEnabled()


def test_vertical_preview_reserves_full_height_without_overlapping_controls(panel):
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QPushButton
    analyzed(panel.service);panel.activate();panel.resize(1050,850);panel.show()
    pixmap=QPixmap(270,480);pixmap.fill()
    panel.preview.setPixmap(pixmap);panel.preview.setMinimumSize(270,480)
    for _ in range(3):QApplication.processEvents()
    following=next(b for b in panel.findChildren(QPushButton) if b.text()=='Preview vertical crop / captions / hook')
    assert panel.preview.height()>=480 and following.y()>panel.preview.geometry().bottom()


def test_source_info_follows_verified_source_choice(panel):
    from dataclasses import replace
    s=panel.service;original=s.fixture_source;branded=replace(original,artifact_id='verified-brand')
    e=s.publishing.source('original')[1];source=s.publishing.source
    s.publishing.source=lambda kind:(branded,*source(kind)[1:]) if kind=='branded' else source(kind)
    s.publishing.sources=lambda:[('original',original,e),('branded',branded,e)]
    panel.activate();panel.source.setCurrentIndex(1)
    assert 'verified-brand' in panel.info.text() and 'VALID branded' in panel.info.text()

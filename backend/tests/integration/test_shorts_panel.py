import asyncio
import os
import threading
import time
from types import SimpleNamespace as NS
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtTest import QTest
from PySide6.QtCore import QTimer, Qt
from app.application.shorts import ShortsService
from app.providers.openai_llm import OpenAILLMProvider
from app.storage.local_store import LocalArtifactStore
from app.desktop.shorts_panel import ShortsPanel
from tests.unit.test_shorts import service, analyzed, ready

@pytest.fixture
def panel(service):
    app=QApplication.instance() or QApplication([])
    p=ShortsPanel();p.bind(service)
    service.publishing.sources=lambda:[('original',service.fixture_source,service.publishing.source('original')[1])]
    yield p
    p.player.stop();p.close()


@pytest.fixture(autouse=True)
def no_paid_requests(monkeypatch):
    def forbidden(*_, **__):pytest.fail("Live OpenAI requests are forbidden in Shorts tests")
    monkeypatch.setattr(OpenAILLMProvider, "generate_structured", forbidden)


def wait_until(condition, timeout=5):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        QApplication.processEvents()
        if condition():return
        QTest.qWait(5)
    pytest.fail("Shorts action did not finish")


def click_analyze(panel):
    QTest.mouseClick(panel.analyze_button,Qt.LeftButton)


def mock_llm(service, response=None):
    calls=[]
    def generate(prompt,schema):
        calls.append(prompt)
        if response is not None:return response()
        ids=schema['properties']['candidates']['items']['properties']['interval_id']['enum']
        return {'candidates':[{'interval_id':x,'title':f'Excerpt {i}',
            'hook_assessment':'Review opening','reason':'Complete explanation','headline':'Known fact'}
            for i,x in enumerate(ids[:3])]}
    service.publishing.llm=NS(generate_structured=generate)
    return calls


def test_analyze_click_timer_persistence_refresh_and_no_duplicate_paid_request(panel):
    s=panel.service;calls=mock_llm(s);panel.activate()
    phases=[];set_text=panel.status.setText
    panel.status.setText=lambda text:(phases.append(text),set_text(text))[-1]
    owner=threading.get_ident();save=s.artifacts.save;save_threads=[]
    def saving(*args,**kwargs):
        save_threads.append(threading.get_ident());return save(*args,**kwargs)
    s.artifacts.save=saving
    click_analyze(panel)
    assert panel.busy and panel.timer.isActive() and panel.status.text().startswith('Checking source')
    click_analyze(panel)  # Disabled body; also exercise the direct dispatch guard.
    panel.start(panel.analyze())
    wait_until(lambda:not panel.busy)
    assert len(calls)==1 and save_threads==[owner]
    assert panel.choices.count()==3 and panel.choices.currentIndex()==0 and panel.value
    assert not panel.dirty and panel.status.text().startswith('Completed: 3')
    assert all(any(p.startswith(phase) for p in phases) for phase in
               ('Checking source','Finding excerpts','AI analysis','Saving candidates','Completed'))
    from fractions import Fraction
    values=s.candidates()
    assert all(30<=Fraction(*p['duration'])<=60 for p in values)
    assert [Fraction(*p['start']) for p in values]==[0,30,60]
    click_analyze(panel);wait_until(lambda:not panel.busy)
    assert len(calls)==1 and len(s.artifacts.history('analysis'))==1
    s.publishing.store=LocalArtifactStore(s.store.root)
    reopened=ShortsService(s.publishing,s.renderer)
    panel.bind(reopened);panel.activate()
    assert [p['id'] for p in reopened.candidates()]==[p['id'] for p in values]
    assert panel.choices.count()==3 and panel.value['candidate_id']==values[0]['id']
    panel.choices.clear()
    refresh=next(b for b in panel.findChildren(QPushButton)
                 if b.text()=='Refresh verified source / retained Shorts')
    QTest.mouseClick(refresh,Qt.LeftButton)
    assert panel.choices.count()==3 and panel.value and not panel.dirty


def test_no_intervals_is_actionable_and_empty_controls_never_become_dirty(panel):
    s=panel.service;calls=mock_llm(s)
    s.publishing.renders.media.boundaries=lambda m:()
    panel.activate();panel.title.setText('No selected candidate');panel.mode.setCurrentText('fill')
    assert not panel.dirty and 'Unsaved' not in panel.status.text()
    click_analyze(panel);wait_until(lambda:not panel.busy)
    message=panel.status.text()
    assert 'No valid 30-60' in message and 'Timeline' in message and 'Finding excerpts' in message
    assert not calls and not s.artifacts.history('analysis') and not panel.value
    panel.title.setText('Another signal');panel.changed();panel.load()
    assert panel.status.text()==message and not panel.dirty


def test_slow_discovery_keeps_qt_timer_responsive_and_blocks_duplicate_dispatch(panel,monkeypatch):
    s=panel.service;calls=mock_llm(s);panel.activate()
    started=threading.Event();release=threading.Event();owner=threading.get_ident();pool=s.pool
    def slow(*args):
        assert threading.get_ident()!=owner
        started.set()
        assert release.wait(3), 'GUI failed to process heartbeat during discovery'
        return pool(*args)
    monkeypatch.setattr(s,'pool',slow)
    heartbeat=[];timer=QTimer(panel)
    def beat():
        if started.is_set():
            heartbeat.append(panel.status.text());release.set();timer.stop()
    timer.timeout.connect(beat);timer.start(5)
    click_analyze(panel);panel.start(panel.analyze())
    try:wait_until(lambda:not panel.busy)
    finally:release.set();timer.stop()
    assert heartbeat and heartbeat[0].startswith('Finding excerpts')
    assert len(calls)==1 and panel.choices.count()==3


@pytest.mark.parametrize('failure', ['provider','shape','fields','unknown','duplicate'])
def test_analysis_failure_is_visible_and_preserves_retained_candidates(panel,failure):
    s=panel.service;analyzed(s);panel.activate();previous=s.candidates()
    def response():
        if failure=='provider':raise RuntimeError('Mock LLM unavailable')
        if failure=='shape':return {'candidates':[]}
        if failure=='fields':return {'candidates':[{'bad':'value'}]*3}
        item={'interval_id':'foreign','title':'Title','hook_assessment':'Review','reason':'Coverage','headline':'Fact'}
        if failure=='duplicate':item['interval_id']=previous[0]['id']
        return {'candidates':[dict(item) for _ in range(3)]}
    calls=mock_llm(s,response)
    click_analyze(panel);wait_until(lambda:not panel.busy)
    message=panel.status.text()
    assert 'failed during AI analysis' in message and 'Previous candidates retained' in message
    assert ('unavailable' in message if failure=='provider' else
            any(t in message for t in ('Invalid','unknown','repeat')))
    assert len(calls)==1 and s.candidates()==previous and len(s.artifacts.history('analysis'))==1
    panel.title.setText('Manual edit after failure');panel.choices.setCurrentIndex(1);panel.changed()
    QApplication.processEvents()
    assert panel.status.text()==message and panel.choices.count()==3


def test_publication_failure_keeps_previous_analysis_and_selection(panel,monkeypatch):
    s=panel.service;analyzed(s);panel.activate();previous=panel.value['candidate_id']
    mock_llm(s)
    def fail(*_,**__):raise OSError('Fixture disk unavailable')
    monkeypatch.setattr(s.artifacts,'save',fail)
    click_analyze(panel);wait_until(lambda:not panel.busy)
    assert 'Saving candidates' in panel.status.text() and 'disk unavailable' in panel.status.text()
    assert panel.choices.count()==3 and panel.value['candidate_id']==previous
    assert len(s.artifacts.history('analysis'))==1


def test_success_selects_first_current_candidate_and_keeps_stale_history(panel):
    from dataclasses import replace
    s=panel.service;old_candidates=analyzed(s);panel.activate()
    source=s.publishing.source
    s.publishing.source=lambda kind:(replace(source(kind)[0],checksum='c'*64),*source(kind)[1:])
    click_analyze(panel);wait_until(lambda:not panel.busy)
    assert panel.status.text().startswith('Completed: 3')
    assert panel.choices.count()==6 and panel.choices.currentIndex()==3
    assert panel.value['candidate_id'] not in {p['id'] for p in old_candidates}
    assert 'STALE' in panel.choices.itemText(0) and len(s.artifacts.history('analysis'))==2


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

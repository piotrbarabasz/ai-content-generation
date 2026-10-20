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
    assert len(calls)==1 and save_threads==[owner,owner]
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


@pytest.mark.parametrize('failure', ['provider','shape','fields','unknown'])
def test_analysis_failure_is_visible_and_preserves_retained_candidates(panel,failure):
    s=panel.service;analyzed(s);panel.activate();previous=s.candidates()
    def response():
        if failure=='provider':raise RuntimeError('Mock LLM unavailable')
        if failure=='shape':return {'candidates':[]}
        if failure=='fields':return {'candidates':[{'bad':'value'}]*3}
        item={'interval_id':'foreign','title':'Title','hook_assessment':'Review','reason':'Coverage','headline':'Fact'}
        return {'candidates':[dict(item) for _ in range(3)]}
    calls=mock_llm(s,response)
    click_analyze(panel);wait_until(lambda:not panel.busy)
    message=panel.status.text()
    assert 'failed during AI analysis' in message and 'Previous candidates retained' in message
    assert ('unavailable' in message if failure=='provider' else
            any(t in message for t in ('Invalid','unknown','repeat')))
    assert len(calls)==1 and s.candidates()==previous and len(s.artifacts.history('analysis'))==1
    QTest.mouseClick(panel.cards[1].select_button,Qt.LeftButton)
    assert panel.status.text()==message
    panel.title.setText('Manual edit after failure');panel.choices.setCurrentIndex(1);panel.changed()
    QApplication.processEvents()
    assert panel.status.text()==message and panel.choices.count()==3


def test_publication_failure_keeps_previous_analysis_and_selection(panel,monkeypatch):
    s=panel.service;analyzed(s);panel.activate();previous=panel.value['candidate_id']
    mock_llm(s)
    save=s.artifacts.save
    def fail(kind,*args,**kwargs):
        if kind=='analysis':raise OSError('Fixture disk unavailable')
        return save(kind,*args,**kwargs)
    monkeypatch.setattr(s.artifacts,'save',fail)
    click_analyze(panel);wait_until(lambda:not panel.busy)
    assert 'Saving candidates' in panel.status.text() and 'disk unavailable' in panel.status.text()
    assert panel.choices.count()==3 and panel.value['candidate_id']==previous
    assert len(s.artifacts.history('analysis'))==1


def test_conflicting_preferences_display_exact_count_provenance_and_first_selection(panel):
    s=panel.service;pool=s.pool()
    def repeated():
        return {'candidates':[{'interval_id':pool[0]['id'],'title':'AI title',
            'hook_assessment':'AI assessment','reason':'AI reason','headline':'AI headline'}]*3}
    calls=mock_llm(s,repeated);panel.activate()
    click_analyze(panel);wait_until(lambda:not panel.busy)
    assert panel.status.text()==('Completed: 3 candidates. 1 AI-selected, '
        '2 verified fallback selections. Editorial review required.')
    assert panel.choices.count()==3 and panel.choices.currentIndex()==0 and not panel.dirty
    assert 'AI-selected' in panel.details.text()
    panel.choices.setCurrentIndex(1)
    assert 'Verified fallback' in panel.details.text() and 'not AI-ranked' in panel.details.text()
    assert panel.title.text()==pool[1]['title'] and 'AI assessment' not in panel.details.text()
    click_analyze(panel);wait_until(lambda:not panel.busy)
    assert len(calls)==1 and panel.choices.currentIndex()==0


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
    panel.advanced_toggle.setChecked(True)
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
    assert 'branded' in panel.info.text() and 'Verified source' in panel.info.text()
    assert 'verified-brand' in panel.info.toolTip() and 'VALID branded' in panel.info.toolTip()


def test_simple_empty_and_verified_source_states_do_not_dispatch(panel):
    from app.desktop.shorts_panel import ShortsState
    p=panel
    assert p.state is ShortsState.SOURCE_NOT_READY
    assert not p.analyze_button.isEnabled() and p.analyze_button.toolTip()
    assert p.step2.isHidden() and p.step3.isHidden() and p.advanced.isHidden()
    assert p.production_mode.currentText()=='Original Excerpt'
    p.activate()
    assert p.state is ShortsState.READY_TO_ANALYZE and p.analyze_button.isEnabled()
    assert p.analyze_button.text()=='Find 3 Shorts' and p.target.value()==45
    assert '90s' in p.info.text() and 'Verified source' in p.info.text()
    assert 'Local heuristic' in p.provider_info.text()
    assert not p.service.artifacts.history('analysis')
    p.count.setValue(2)
    assert p.analyze_button.text()=='Find 2 Shorts'


def test_cards_selection_preview_and_no_implicit_acceptance(panel,monkeypatch):
    from pathlib import Path
    p=panel;p.activate();click_analyze(p);wait_until(lambda:not p.busy)
    assert len(p.cards)==3 and not p.step2.isHidden()
    assert all('30s' in card.summary.text() and 'Topic' in card.summary.text()
               and 'Review needed' in card.summary.text() for card in p.cards)
    played=[]
    set_source=p.player.setSource
    monkeypatch.setattr(p.player,'setSource',lambda url:(played.append(url.toLocalFile()),set_source(url))[-1])
    monkeypatch.setattr(p.player,'play',lambda:None)
    QTest.mouseClick(p.cards[1].play_button,Qt.LeftButton)
    assert p.value['candidate_id']==p.cards[1].identity and p.cards[1].select_button.isChecked()
    assert played[-1]
    assert {Path(path) for path in played if path}=={p.service.store._artifact_path(p.service.fixture_source.storage_key)}
    assert p.play_end==60000
    QTest.mouseClick(p.cards[1].select_button,Qt.LeftButton)
    assert p.cards[1].select_button.isChecked()
    assert not p.create_button.isEnabled() and 'Editorial review required' in p.review_reason.text()
    with pytest.raises(ValueError,match='Explicitly accept'):p.create_short()
    assert not p.service.artifacts.history('decision') and not p.service.renderer.calls


def test_create_saves_acceptance_renders_and_exports_verified_mp4(panel,monkeypatch,tmp_path):
    from PySide6.QtWidgets import QFileDialog
    from app.desktop.shorts_panel import ShortsState
    p=panel;analyzed(p.service);p.activate();p.title.setText('A reviewed title')
    calls=mock_llm(p.service)  # Rendering must not dispatch the configured LLM.
    p.accept_check.setChecked(True)
    assert not p.service.artifacts.history('decision')
    QTest.mouseClick(p.create_button,Qt.LeftButton)
    assert p.busy and p.cancel_button.isEnabled() and 'Rendering' in p.status.text()
    wait_until(lambda:not p.busy)
    assert p.state is ShortsState.SHORT_READY and not p.step3.isHidden()
    assert '1080 x 1920' in p.output_info.text() and '30s' in p.output_info.text()
    assert p.value['title']=='A reviewed title' and not p.dirty
    assert p.service.accepted(p.value['candidate_id']) and len(p.service.renderer.calls)==1
    assert 'Rendered' in p.cards[0].summary.text() and 'Accepted' in p.cards[0].summary.text()
    destination=tmp_path/'saved.mp4'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *_:(str(destination),'MP4'))
    QTest.mouseClick(p.export_button,Qt.LeftButton)
    assert destination.read_bytes()==p.service.artifacts.records.verified_bytes(p.output())
    assert not p.status_error
    assert not calls


def test_create_reuses_saved_revision_and_retains_history(panel):
    p=panel;candidate=analyzed(p.service)[0];ready(p.service,candidate);p.activate()
    previous=p.revision
    p.edit_selected();QTest.mouseClick(p.create_button,Qt.LeftButton);wait_until(lambda:not p.busy)
    assert p.revision==previous and len(p.service.artifacts.history('edit'))==1
    assert len(p.service.artifacts.history('decision'))==1 and len(p.service.renderer.calls)==1
    p.edit_selected();p.title.setText('New revision');p.create_short();wait_until(lambda:not p.busy)
    assert p.revision.artifact_id!=previous.artifact_id
    assert p.value['parent_revision_id']==previous.artifact_id
    assert len(p.service.artifacts.history('edit'))==2 and len(p.service.renderer.calls)==2
    assert p.service.artifacts.read(previous)['title']==candidate['title']


def test_switching_cards_preserves_invalid_unsaved_drafts_and_refresh(panel):
    p=panel;values=analyzed(p.service);p.activate()
    p.title.setText('Unfinished first title');p.mode.setCurrentText('fill');p.focal.setValue(.25)
    p.hook_on.setChecked(True);p.hook.setText('Draft hook');p.hook_on.setChecked(False)
    p.accent.setText('FF')  # A partial RGB value must survive navigation too.
    QTest.mouseClick(p.cards[1].select_button,Qt.LeftButton)
    assert p.value['candidate_id']==values[1]['id'] and not p.dirty
    p.title.setText('Second draft')
    QTest.mouseClick(p.cards[0].select_button,Qt.LeftButton)
    assert p.title.text()=='Unfinished first title' and p.mode.currentText()=='fill'
    assert p.focal.value()==.25 and p.accent.text()=='FF' and p.dirty
    assert p.hook.text()=='Draft hook' and not p.hook_on.isChecked()
    assert p.draft()['style']['hook']==''
    p.activate()
    assert p.title.text()=='Unfinished first title' and p.accent.text()=='FF' and p.dirty
    assert not p.service.artifacts.history('edit')
    p.accent.setText('FFFFFF');p.accept_check.setChecked(True);p.create_short()
    wait_until(lambda:not p.busy)
    QTest.mouseClick(p.cards[1].select_button,Qt.LeftButton)
    assert p.title.text()=='Second draft' and p.dirty and not p.accept_check.isChecked()


def test_invalid_create_and_render_failure_remain_actionable(panel):
    p=panel;analyzed(p.service);p.activate();p.accept_check.setChecked(True)
    p.title.clear();p.run(p.create_short)
    assert 'Invalid Short edit' in p.status.text() and not p.service.artifacts.history('decision')
    assert not p.busy and not p.service.renderer.calls
    p.title.setText('Valid title');p.service.renderer.failure=True;p.run(p.create_short)
    wait_until(lambda:not p.busy)
    assert 'encoder failure' in p.status.text() and p.status_error
    assert p.service.accepted(p.value['candidate_id']) and p.revision
    assert p.step3.isHidden() and not p.export_button.isEnabled()
    p.title.setText('Changed after error')
    assert 'encoder failure' in p.status.text()


def test_changed_boundaries_need_fresh_review_after_switching_cards(panel):
    p=panel;values=analyzed(p.service);p.service.decide(values[0]['id'],True);p.activate()
    assert p.accept_check.isChecked()
    p.end.setCurrentIndex(p.end.findData([40,1]))
    assert not p.accept_check.isChecked() and p.dirty
    p.select_card(values[1]['id']);p.select_card(values[0]['id'])
    assert p.end.currentData()==[40,1] and not p.accept_check.isChecked()
    assert not p.create_button.isEnabled()
    assert p.service.accepted(values[0]['id'])  # Historical decision is preserved.


def test_render_progress_cancel_and_project_switch_guard(panel,monkeypatch):
    from app.runtime.media_process import RenderCanceled
    p=panel;analyzed(p.service);p.activate();p.accept_check.setChecked(True)
    started=[]
    async def pending(*args,**kwargs):
        kwargs['progress']('Encoding selected Short',.2);started.append(True)
        while not kwargs['canceled']():await asyncio.sleep(.001)
        raise RenderCanceled('Short canceled before publication')
    monkeypatch.setattr(p.service.renderer,'render',pending)
    p.create_short();wait_until(lambda:bool(started))
    assert 'Encoding selected Short' in p.status.text() and not p.create_button.isEnabled()
    with pytest.raises(ValueError,match='cleanup'):p.bind(p.service)
    p.cancel();wait_until(lambda:not p.busy)
    assert 'canceled' in p.status.text() and not p.cancel_button.isEnabled()
    assert p.service.accepted(p.value['candidate_id']) and p.revision
    assert not p.service.artifacts.history('selection') and p.create_button.isEnabled()


def test_reopen_restores_render_and_stale_export_is_blocked(panel,monkeypatch,tmp_path):
    from dataclasses import replace
    from PySide6.QtWidgets import QFileDialog
    p=panel;s=p.service;candidate=analyzed(s)[0];ready(s,candidate);p.activate()
    revision=p.revision
    p.bind(ShortsService(s.publishing,s.renderer));p.activate()
    assert p.revision==revision and p.accept_check.isChecked() and not p.step3.isHidden()
    source=s.publishing.source
    target=tmp_path/'must-not-exist.mp4'
    def dialog(*_):
        s.publishing.source=lambda kind:(replace(source(kind)[0],checksum='e'*64),*source(kind)[1:])
        return str(target),'MP4'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',dialog)
    p.run(lambda:p.copy('video'))
    assert p.status_error and 'STALE' in p.status.text() and not target.exists()


def test_advanced_shared_settings_invalidate_preview_and_output(panel,monkeypatch):
    from PySide6.QtGui import QPixmap
    p=panel;candidate=analyzed(p.service)[0];ready(p.service,candidate);p.activate()
    assert p.advanced.isHidden()
    p.advanced_toggle.setChecked(True)
    assert not p.advanced.isHidden()
    p.preview.setPixmap(QPixmap(270,480));p.font.setValue(40)
    assert p.dirty and p.draft()['style']['font_size']==40 and p.preview.pixmap().isNull()
    assert p.step3.isHidden() and 'refresh' in p.caption_preview.text()
    p.advanced_toggle.setChecked(False)
    assert p.advanced.isHidden() and p.font.value()==40
    p.mode.setCurrentText('fill');p.hook.setText('Known fact');p.hook_on.setChecked(True)
    p.advanced_toggle.setChecked(True)
    assert p.draft()['style']['mode']=='fill' and p.draft()['style']['hook']=='Known fact'
    assert not p.caps.isEnabled() and not p.external_captions.isEnabled()
    assert 'alignment' in p.captions_reason.text()
    # Availability is verified through prepare against this exact source and range.
    prepare=p.service.prepare
    def with_captions(candidate,edit):
        copy={**edit,'style':{**edit['style'],'captions':False,'burn':False}}
        return prepare(candidate,copy)
    monkeypatch.setattr(p.service,'prepare',with_captions);p.check_captions()
    assert p.caps.isEnabled() and p.external_captions.isEnabled()
    p.external_captions.setChecked(True)
    assert p.caps.isChecked() and p.draft()['style']['captions']
    p.burn.setChecked(True);p.caps.setChecked(False)
    assert not p.external_captions.isChecked() and not p.burn.isChecked()
    p.caps.setChecked(True);p.burn.setChecked(True)
    monkeypatch.setattr(p.service,'prepare',prepare);p.check_captions()
    assert not p.caps.isEnabled() and not p.remove_captions_button.isHidden()
    QTest.mouseClick(p.remove_captions_button,Qt.LeftButton)
    assert not p.caps.isChecked() and not p.burn.isChecked() and p.remove_captions_button.isHidden()


def test_advanced_director_entry_does_not_dispatch_generation(panel):
    p=panel;calls=mock_llm(p.service);analyzed(p.service);p.activate();calls.clear()
    p.advanced_toggle.setChecked(True);p.production_mode.setCurrentIndex(1)
    assert not p.director.isHidden() and p.original_scroll.isHidden() and not p.return_button.isHidden()
    assert p.director.service is None and not calls and not p.service.renderer.calls
    QTest.mouseClick(p.return_button,Qt.LeftButton)
    assert p.director.isHidden() and not p.original_scroll.isHidden() and not calls


def test_empty_analysis_result_cannot_report_success(panel,monkeypatch):
    p=panel;analyzed(p.service);p.activate();previous=[c.identity for c in p.cards]
    manifest=p.service.artifacts.save('analysis',{'candidates':[],'origin':'empty fixture'})
    async def empty(*_,**__):return manifest
    monkeypatch.setattr(p.service,'analyze',empty)
    click_analyze(p);wait_until(lambda:not p.busy)
    assert p.status_error and 'No candidates returned' in p.status.text()
    assert [c.identity for c in p.cards]==previous


@pytest.mark.parametrize('size',[(1360,800),(1050,850)])
@pytest.mark.parametrize('stage',['review','output'])
def test_simple_layout_primary_actions_fit_desktop_viewport(panel,size,stage):
    from pathlib import Path
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QFont, QFontDatabase
    # Windows' offscreen platform can have no default fonts. Load one explicitly
    # for readable visual evidence without changing the application's font.
    font_path=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/segoeui.ttf'
    if font_path.exists():
        font_id=QFontDatabase.addApplicationFont(str(font_path))
        assert font_id>=0
        panel.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0],9))
    p=panel;values=analyzed(p.service)
    if stage=='output':ready(p.service,values[0])
    p.activate();p.resize(*size);p.show()
    for _ in range(3):QApplication.processEvents()
    primary=p.export_button if stage=='output' else p.create_button
    viewport=p.original_scroll.viewport()
    top=primary.mapTo(viewport,QPoint(0,0))
    assert top.y()>=0 and top.y()+primary.height()<=viewport.height()
    assert p.original_scroll.horizontalScrollBar().maximum()==0
    assert p.original_scroll.verticalScrollBar().maximum()==0
    if stage=='review':
        assert len(p.cards)==3
    assert p.card_scroll.verticalScrollBar().maximum()==0
    directory=os.environ.get('D073_SCREENSHOT_DIR')
    if directory:
        root=Path(directory);root.mkdir(parents=True,exist_ok=True)
        assert p.grab().save(str(root/f'{stage}-{size[0]}x{size[1]}.png'))

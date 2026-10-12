import asyncio
import os
import time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from app.desktop.shorts_panel import ShortsPanel
from tests.integration.test_shorts_director import directed, excerpt_fixture, scripted, boarded

@pytest.fixture
def panel(directed):
    app=QApplication.instance() or QApplication([])
    p=ShortsPanel();p.bind(directed.shorts)
    p.choices.addItem("Payments",directed.candidate_id)
    p.director_factory=lambda *_:directed
    p.director.create()
    yield p
    if p.director.busy:
        p.director.cancel()
        while p.director.busy:p.director.tick();time.sleep(.01)
    p.director.discard_drafts();p.director.close_service();p.close()

def test_open_and_mode_switch_do_not_generate(panel):
    p=panel;assert not p.director.service.fixture.llm.calls
    p.production_mode.setCurrentIndex(1);p.production_mode.setCurrentIndex(0)
    assert not p.director.service.scope.audio.calls and not p.director.service.image_provider.calls
    assert p.production_mode.currentText()=="Original Excerpt"

def test_manual_parts_sync_and_unsaved_script_guards(panel):
    p=panel.director;scripted(p.service);p.populate_script()
    p.parts['development'].setPlainText('Cash remains useful. Local services may continue.')
    assert p.dirty and 'Cash remains useful.' in p.full.toPlainText()
    with pytest.raises(ValueError,match='discard'):p.create()
    with pytest.raises(ValueError,match='discard'):p.close_service()
    p.save_script();assert not p.dirty
    assert p.service.script()[1]['development']=='Cash remains useful. Local services may continue.'
    p.target.setValue(46);assert p.dirty
    p.discard_drafts();assert p.target.value()==45 and not p.dirty

def test_scene_edits_block_switch_and_render_then_persist(panel):
    p=panel.director;boarded(p.service);p.populate_script();p.board_revision,p.board=p.service.storyboard();p.populate_board()
    original=p.scenes.currentData();p.focal.setValue(.25)
    p.scenes.setCurrentIndex(1)
    assert p.scene_dirty and p.scenes.currentData()==original
    with pytest.raises(ValueError,match='discard'):asyncio.run(p.render())
    p.save_scene();assert not p.scene_dirty
    assert p.service.storyboard()[1]['scenes'][0]['focal']==.25
    p.scenes.setCurrentIndex(1);assert p.scenes.currentData()!=original
    p.prompt.setPlainText('A manual revised visual prompt.');p.discard_drafts()
    assert not p.scene_dirty and p.prompt.toPlainText()!='A manual revised visual prompt.'

def test_generation_is_responsive_and_cancel_preserves_working_text(panel):
    p=panel.director;scripted(p.service);p.populate_script();before=p.service.script()[0]
    original=p.service.fixture.llm.generate_structured
    def slow(*args):time.sleep(.1);return original(*args)
    p.service.fixture.llm.generate_structured=slow
    p.start(p.generate_script());p.tick();assert p.busy and not p.tabs.isEnabled()
    p.cancel()
    deadline=time.monotonic()+5
    while p.busy and time.monotonic()<deadline:
        QApplication.processEvents();time.sleep(.01)
    assert not p.busy and p.tabs.isEnabled() and p.service.script()[0].artifact_id==before.artifact_id
    assert 'canceled' in p.status.text().lower()

def test_reopen_restores_script_duration_voice_and_framing(panel):
    p=panel.director;boarded(p.service);p.populate_script();p.board_revision,p.board=p.service.storyboard();p.populate_board()
    p.focal.setValue(.25);p.save_scene()
    # Re-create through the real SQLite scope, retaining all owned artifacts.
    from app.application.shorts_director import ShortsDirectorService
    old=p.service;fixture=old.fixture;parent=old.shorts;candidate=old.candidate_id
    p.owner.director_factory=lambda *_:ShortsDirectorService(parent,candidate,fixture.scope,old.renderer,language='en',image_provider=old.image_provider)
    p.create()
    assert p.focal.value()==.25 and '36.000s' in p.duration.text()
    assert p.parts['hook'].toPlainText()==old.script()[1]['hook'] and p.voices.currentData().voice=='one'

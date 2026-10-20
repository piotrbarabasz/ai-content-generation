import asyncio
import os
import time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from app.desktop.shorts_panel import ShortsPanel
from tests.integration.test_shorts_director import directed, excerpt_fixture, scripted, boarded
from tests.shorts_playback_support import fake_players

def settle(panel,timeout=20):
    from PySide6.QtTest import QTest
    deadline=time.monotonic()+timeout
    owner=getattr(panel,'owner',panel)
    while owner.busy and time.monotonic()<deadline:
        QApplication.processEvents();QTest.qWait(5)
    assert not owner.busy,panel.status.text()

@pytest.fixture
def panel(directed,fake_players):
    app=QApplication.instance() or QApplication([])
    p=ShortsPanel();p.bind(directed.shorts)
    p.choices.addItem("Payments",directed.candidate_id)
    settle(p)
    p.director_factory=lambda *_:directed
    p.director.create()
    settle(p)
    yield p
    if p.director.busy:
        p.director.cancel()
        while p.director.busy:p.director.tick();time.sleep(.01)
    p.director.discard_drafts();settle(p);p.director.close_service();p.close()

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
    p.save_script();settle(p);assert not p.dirty
    assert p.service.script()[1]['development']=='Cash remains useful. Local services may continue.'
    p.target.setValue(46);assert p.dirty
    p.discard_drafts();settle(p);assert p.target.value()==45 and not p.dirty

def test_scene_edits_block_switch_and_render_then_persist(panel):
    p=panel.director;boarded(p.service);p.populate_script();p.board_revision,p.board=p.service.storyboard();p.populate_board()
    original=p.scenes.currentData();p.focal.setValue(.25)
    p.scenes.setCurrentIndex(1)
    assert p.scene_dirty and p.scenes.currentData()==original
    with pytest.raises(ValueError,match='discard'):asyncio.run(p.render())
    p.save_scene();settle(p);assert not p.scene_dirty
    assert p.service.storyboard()[1]['scenes'][0]['focal']==.25
    p.scenes.setCurrentIndex(1);assert p.scenes.currentData()!=original
    p.prompt.setPlainText('A manual revised visual prompt.');p.discard_drafts();settle(p)
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
    p.focal.setValue(.25);p.save_scene();settle(p)
    # Re-create through the real SQLite scope, retaining all owned artifacts.
    from app.application.shorts_director import ShortsDirectorService
    old=p.service;fixture=old.fixture;parent=old.shorts;candidate=old.candidate_id
    p.owner.director_factory=lambda *_:ShortsDirectorService(parent,candidate,fixture.scope,old.renderer,language='en',image_provider=old.image_provider)
    p.create();settle(p)
    assert p.focal.value()==.25 and '36.000s' in p.duration.text()
    assert p.parts['hook'].toPlainText()==old.script()[1]['hook'] and p.voices.currentData().voice=='one'


def test_render_replaces_original_with_verified_directed_media(panel):
    from pathlib import Path
    from app.desktop.shorts_playback import DisplayedMedia
    p=panel.director;boarded(p.service)
    p.play_source();settle(p)
    assert p.playback.kind is DisplayedMedia.ORIGINAL_EXCERPT and p.end_position is not None
    assert 'Original excerpt' in p.media_label.text()
    asyncio.run(p.render());video=p.service.current_output()
    assert p.playback.kind is DisplayedMedia.DIRECTED_SHORT and p.end_position is None
    assert Path(p.player.source().toLocalFile())==p.service.records.store._artifact_path(video.storage_key)
    assert 'Directed Short ready' in p.status.text() and 'Verified directed Short' in p.media_label.text()
    assert p.video.width()*16==p.video.height()*9 and p.play_output_button.isEnabled()


@pytest.mark.parametrize('control',['burn','font','hook_overlay','hook_seconds','brand'])
def test_finishing_drafts_guard_play_export_and_discard_preserves_output(panel,control):
    p=panel.director;s=p.service;boarded(s);asyncio.run(p.render());video=s.current_output()
    saved=s.finish();field=getattr(p,control)
    if control in ('burn','brand'):field.setChecked(not field.isChecked())
    elif control=='hook_overlay':field.setText('Reviewed finishing headline')
    else:field.setValue(field.value()+1)
    assert p.finish_dirty and p.unsaved and 'Previous verified MP4 retained' in p.status.text()
    assert not p.play_output_button.isEnabled() and not p.copy_buttons['video'].isEnabled()
    assert 'ready' not in p.finish_readiness.text().lower()
    with pytest.raises(ValueError,match='finishing'):p.play_output()
    with pytest.raises(ValueError,match='finishing'):p.copy('video')
    assert s.current_output().artifact_id==video.artifact_id and s.finish()==saved
    p.discard_drafts();settle(p)
    assert not p.unsaved and p.play_output_button.isEnabled() and s.current_output().artifact_id==video.artifact_id


def test_finishing_rerender_retains_history_without_audio_or_image_regeneration(panel):
    p=panel.director;s=p.service;boarded(s);asyncio.run(p.render());old=s.current_output()
    audio_calls=len(s.scope.audio.calls);image_calls=len(s.image_provider.calls)
    p.hook_overlay.setText('New saved headline');p.save_finish();settle(p)
    assert not p.finish_dirty and not p.play_output_button.isEnabled()
    with pytest.raises(ValueError,match='STALE'):s.current_output()
    asyncio.run(p.render());new=s.current_output()
    assert new.artifact_id!=old.artifact_id and len(s.records.history('video'))==2
    assert s.records.verify_video(old)==old
    assert len(s.scope.audio.calls)==audio_calls and len(s.image_provider.calls)==image_calls
    p.hook_overlay.setText('Render directly from draft');asyncio.run(p.render())
    assert not p.finish_dirty and s.finish()['style']['hook']=='Render directly from draft'


@pytest.mark.parametrize('code',['ResourceError','FormatError'])
def test_director_decoder_error_survives_refresh_and_valid_render_remains(panel,code):
    from PySide6.QtMultimedia import QMediaPlayer
    p=panel.director;s=p.service;boarded(s);asyncio.run(p.render());video=s.current_output()
    p.player.errorOccurred.emit(getattr(QMediaPlayer,code),'Decoder unavailable')
    message=p.status.text();assert 'directed short' in message and 'still saved' in message
    p.refresh();settle(p);p.progress('READY')
    assert p.status.text()==message and p.media_error.text()==message
    assert s.current_output().artifact_id==video.artifact_id
    p.play_output();settle(p);assert not p.playback.error_message and p.media_error.isHidden()


def test_finishing_edit_during_export_dialog_does_not_copy_stale_settings(panel,monkeypatch,tmp_path):
    from PySide6.QtWidgets import QFileDialog
    p=panel.director;boarded(p.service);asyncio.run(p.render());destination=tmp_path/'no-copy.mp4'
    def choose(*_):
        p.hook_overlay.setText('Changed while dialog open')
        return str(destination),''
    monkeypatch.setattr(QFileDialog,'getSaveFileName',choose)
    p.copy('video');settle(p)
    assert 'finishing' in p.status.text()
    assert not destination.exists()

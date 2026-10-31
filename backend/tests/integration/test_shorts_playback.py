"""Real local Qt decoding, no providers or user media; plus controlled load races."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.application.shorts import ShortsService
from app.desktop.shorts_panel import ShortsPanel
from app.desktop.shorts_playback import ShortsPlayback, DisplayedMedia
from tests.shorts_playback_support import FakePlayer
from tests.unit.test_shorts import service, analyzed


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def until(condition, timeout=8):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        QApplication.processEvents()
        if condition():return
        QTest.qWait(5)
    pytest.fail('Qt media condition did not complete')


@pytest.fixture(scope='module')
def synthetic_media(tmp_path_factory):
    ffmpeg=shutil.which('ffmpeg')
    assert ffmpeg, 'Real FFmpeg is required for offline Shorts seek acceptance'
    root=tmp_path_factory.mktemp('shorts-playback')
    for name,size,duration in [('parent','160x90',90),('portrait','108x192',30)]:
        subprocess.run([ffmpeg,'-nostdin','-v','error','-n','-f','lavfi','-i',f'testsrc2=s={size}:r=25:d={duration}',
            '-f','lavfi','-i',f'sine=frequency=440:sample_rate=48000:duration={duration}',
            '-t',str(duration),'-c:v','libx264','-preset','ultrafast','-g','25','-threads','2',
            '-pix_fmt','yuv420p','-c:a','aac',str(root/f'{name}.mp4')],check=True,capture_output=True)
    return root


@pytest.fixture
def native_panel(qt_app, service, synthetic_media):
    s=service;_,e,t=s.publishing.source('original')
    e={**e,'width':160,'height':90}
    source=s.store.save_artifact('parent.mp4',(synthetic_media/'parent.mp4').read_bytes(),
        {'artifact_type':'video_render','project_id':'project','render':e})
    s.publishing.source=lambda _:(source,e,t)
    s.publishing.sources=lambda:[('original',source,e)]
    values=analyzed(s);p=ShortsPanel();p.bind(s);p.audio.setMuted(True)
    sink=QVideoSink(p);p.player.setVideoSink(sink)
    frames=[]
    def frame(value):
        if value.isValid() and p.player.playbackState()==QMediaPlayer.PlayingState and not p.playback.pending:
            frames.append((value.startTime(),value.width(),value.height()))
    sink.videoFrameChanged.connect(frame)
    p.activate();until(lambda:not p.busy)
    yield p,values,frames
    p.playback.clear();p.director.close_service();p.close()


def test_native_candidate_starts_at_thirty_seconds_stops_at_end_and_replays(native_panel):
    p,values,frames=native_panel
    p.select_card(values[1]['id'],True)
    until(lambda:bool(frames))
    assert 30_000_000<=frames[0][0]<30_080_000,frames
    assert p.playback.kind is DisplayedMedia.ORIGINAL_EXCERPT
    assert p.play_end==60000 and '30.000–60.000' in p.media_label.text()
    p.player.setPosition(59960)
    until(lambda:p.player.playbackState()==QMediaPlayer.PausedState)
    assert 60000<=p.player.position()<60200
    p.playback.stop();frames.clear();p.play_source()
    until(lambda:bool(frames))
    assert 30_000_000<=frames[0][0]<30_080_000,frames


def test_native_rapid_selection_stop_and_project_switch(native_panel):
    p,values,frames=native_panel
    for index in (2,0,2,1):p.select_card(values[index]['id'],True)
    until(lambda:bool(frames))
    assert p.value['candidate_id']==values[1]['id'] and frames[0][0]>=30_000_000
    assert frames[0][0]<30_080_000
    p.playback.clear();p.select_card(values[2]['id'],True);until(lambda:not p.busy);p.source_stop_button.click()
    until(lambda:p.player.mediaStatus()==QMediaPlayer.LoadedMedia)
    assert p.player.playbackState()==QMediaPlayer.StoppedState
    frames.clear();p.play_source();until(lambda:bool(frames))
    assert 60_000_000<=frames[0][0]<60_080_000
    p.bind(ShortsService(p.service.publishing,p.service.renderer))
    assert p.playback.kind is DisplayedMedia.NO_MEDIA and p.player.source().isEmpty()
    assert not p.playback.pending and p.player.playbackState()==QMediaPlayer.StoppedState


def test_native_original_to_portrait_and_reopen_saved_copy(qt_app,synthetic_media,tmp_path):
    player=QMediaPlayer();sink=QVideoSink();player.setVideoSink(sink)
    playback=ShortsPlayback(player,lambda:('project','candidate'))
    frames=[]
    sink.videoFrameChanged.connect(lambda f:frames.append((f.startTime(),f.width(),f.height()))
        if f.isValid() and player.playbackState()==QMediaPlayer.PlayingState and not playback.pending else None)
    playback.load(synthetic_media/'parent.mp4',DisplayedMedia.ORIGINAL_EXCERPT,('project','candidate'),start=30000,end=60000,autoplay=True)
    until(lambda:bool(frames));assert frames[0][0]>=30_000_000 and frames[0][1:]==(160,90)
    frames.clear()
    playback.load(synthetic_media/'portrait.mp4',DisplayedMedia.VERIFIED_SHORT,('project','candidate'),autoplay=True)
    until(lambda:any(f[1:]==(108,192) for f in frames))
    assert playback.kind is DisplayedMedia.VERIFIED_SHORT and playback.request.end is None
    from app.storage.local_store import LocalArtifactStore
    from app.desktop.artifact_export import copy_artifact_async
    import asyncio
    from PySide6.QtWidgets import QFileDialog
    from unittest.mock import patch
    store=LocalArtifactStore(tmp_path/'artifacts');raw=(synthetic_media/'portrait.mp4').read_bytes()
    m=store.save_artifact('short.mp4',raw,{'artifact_type':'shorts_video','project_id':'test'})
    destination=tmp_path/'saved.mp4'
    with patch.object(QFileDialog,'getSaveFileName',return_value=(str(destination),'')):
        asyncio.run(copy_artifact_async(None,store,m,title='Save Short',suggested='short.mp4',revalidate=lambda:m))
    assert destination.read_bytes()==raw
    frames.clear();playback.load(destination,DisplayedMedia.VERIFIED_SHORT,('project','candidate'),autoplay=True)
    until(lambda:any(f[1:]==(108,192) for f in frames));assert not playback.error_message
    playback.clear()


def test_native_director_original_seek(native_panel):
    p,values,_=native_panel;d=p.director
    # Isolate the playback port; the retained candidate has verified 30–60 s bounds.
    d.service=SimpleNamespace(candidate_id=values[1]['id'],source=lambda:values[1],close=lambda:None)
    async def read(operation,**_):
        return operation(SimpleNamespace(source=lambda:values[1],shorts=p.service))
    d.service.read_async=read
    d.audio_output.setMuted(True);sink=QVideoSink(d);d.player.setVideoSink(sink);frames=[]
    sink.videoFrameChanged.connect(lambda f:frames.append(f.startTime()) if f.isValid()
        and d.player.playbackState()==QMediaPlayer.PlayingState and not d.playback.pending else None)
    d.play_source();until(lambda:bool(frames))
    assert 30_000_000<=frames[0]<30_080_000,frames
    assert d.end_position==60000 and 'Original excerpt' in d.media_label.text()
    until(lambda:not d.busy);d.playback.clear();d.close_service()
    assert d.playback.kind is DisplayedMedia.NO_MEDIA and d.player.source().isEmpty()
    assert not d.output_ready and not d.play_output_button.isEnabled()
    assert 'Explicitly create' in d.status.text()


@pytest.mark.parametrize('content',[None,b'not an MP4'])
def test_real_missing_or_corrupt_media_reports_persistent_error(qt_app,tmp_path,content,synthetic_media):
    path=tmp_path/'bad.mp4'
    if content is not None:path.write_bytes(content)
    player=QMediaPlayer();sink=QVideoSink();player.setVideoSink(sink)
    playback=ShortsPlayback(player,lambda:('project','candidate'))
    playback.load(path,DisplayedMedia.VERIFIED_SHORT,('project','candidate'),autoplay=True,retry=True)
    until(lambda:bool(playback.error_message))
    assert playback.kind is DisplayedMedia.PLAYBACK_ERROR
    assert 'verified short' in playback.error_message
    assert ('missing or inaccessible' if content is None else 'still saved') in playback.error_message
    # Restoring the same file must make an explicit retry usable, without changing URL.
    path.write_bytes((synthetic_media/'portrait.mp4').read_bytes())
    frames=[]
    sink.videoFrameChanged.connect(lambda f:frames.append(f.startTime()) if f.isValid()
        and player.playbackState()==QMediaPlayer.PlayingState and not playback.pending else None)
    playback.load(path,DisplayedMedia.VERIFIED_SHORT,('project','candidate'),autoplay=True,retry=True)
    until(lambda:bool(frames))
    assert not playback.error_message and playback.kind is DisplayedMedia.VERIFIED_SHORT
    playback.clear()


def test_readiness_and_seek_confirmation_are_required_and_old_context_cannot_play(qt_app,tmp_path):
    player=FakePlayer();player.auto_ready=False;context=['project','first']
    playback=ShortsPlayback(player,lambda:tuple(context))
    playback.load(tmp_path/'source.mp4',DisplayedMedia.ORIGINAL_EXCERPT,tuple(context),start=30000,end=60000,autoplay=True)
    assert playback.pending and not player.played
    # Loaded alone is insufficient: the source must be seekable, with a confirmed position.
    player._status=QMediaPlayer.LoadedMedia;player.mediaStatusChanged.emit(player._status)
    assert not player.played
    set_position=player.setPosition;player.setPosition=lambda _:None
    player._seekable=True;player.seekableChanged.emit(True)
    assert playback.pending and not player.played
    context[1]='second';set_position(30000)
    assert not player.played
    playback.clear()

import os,sys,asyncio,time,json,threading
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'backend'))
os.environ['QT_QPA_PLATFORM']='offscreen'
import pytest
from PySide6.QtCore import QUrl,QTimer
from PySide6.QtWidgets import QApplication,QFileDialog
from app.desktop.shorts_panel import ShortsPanel
from tests.unit.test_shorts import service,analyzed,ready
from tests.integration.test_shorts_director import directed,excerpt_fixture,boarded

@pytest.fixture
def app():return QApplication.instance() or QApplication([])

def test_ready_card_play_displays_parent_in_finish_step(app,service):
    p=ShortsPanel();p.bind(service);service.publishing.sources=lambda:[('original',service.fixture_source,service.publishing.source('original')[1])]
    candidate=analyzed(service)[0];video=ready(service,candidate);p.activate()
    assert p.verified_output.artifact_id==video.artifact_id
    assert Path(p.player.source().toLocalFile())==service.store._artifact_path(video.storage_key)
    p.select_card(candidate['id'],True)
    assert not p.step3.isHidden() and p.export_button.isEnabled()
    assert Path(p.player.source().toLocalFile())==service.store._artifact_path(service.fixture_source.storage_key)
    assert p.play_end==30000 and p.output().artifact_id==video.artifact_id
    p.play_short();assert p.play_end is None
    assert Path(p.player.source().toLocalFile())==service.store._artifact_path(video.storage_key)
    p.player.stop();p.close()

def test_media_errors_are_not_shown(app,service):
    p=ShortsPanel();p.bind(service);p.status.setText('Short ready. Preview, then Save MP4.')
    p.player.errorOccurred.emit(p.player.Error.ResourceError,'Synthetic decoder cannot open file')
    assert p.status.text()=='Short ready. Preview, then Save MP4.' and not p.status_error
    p.director.status.setText('RENDER: READY')
    p.director.player.errorOccurred.emit(p.player.Error.FormatError,'Synthetic decoder failure')
    assert p.director.status.text()=='RENDER: READY'
    p.close()

def test_save_mp4_uses_verified_output_even_after_source_play(app,service,monkeypatch,tmp_path):
    p=ShortsPanel();p.bind(service);service.publishing.sources=lambda:[('original',service.fixture_source,service.publishing.source('original')[1])]
    candidate=analyzed(service)[0];video=ready(service,candidate);p.activate();p.select_card(candidate['id'],True)
    dest=tmp_path/'export.mp4';monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *_:(str(dest),''))
    assert p.copy('video')==dest
    assert dest.read_bytes()==service.store.read_artifact(video.storage_key)
    assert dest.read_bytes()!=service.store.read_artifact(service.fixture_source.storage_key)
    p.player.stop();p.close()

def test_director_render_keeps_source_loaded_and_finish_edits_not_guarded(app,directed):
    p=ShortsPanel();p.bind(directed.shorts);p.choices.addItem('Candidate',directed.candidate_id);p.director_factory=lambda *_:directed
    p.director.create();boarded(directed);d=p.director;d.populate_script();d.board_revision,d.board=directed.storyboard();d.populate_board()
    d.play_source();original=d.player.source();asyncio.run(d.render())
    assert d.player.source()==original and 'READY' in d.status.text()
    old=directed.current_output();d.hook_overlay.setText('Changed finishing headline')
    assert not d.unsaved and directed.current_output().artifact_id==old.artifact_id
    assert directed.finish()['style']['hook']!='Changed finishing headline'
    d.player.stop();d.close_service();p.close()

def test_staging_runs_on_gui_thread_and_blocks_heartbeat(app,service,monkeypatch):
    p=ShortsPanel();p.bind(service);service.publishing.sources=lambda:[('original',service.fixture_source,service.publishing.source('original')[1])]
    candidate=analyzed(service)[0];p.activate();owner=threading.get_ident();observed=[]
    original=service.stage
    def slow(source):
        assert threading.get_ident()==owner
        time.sleep(.2);return original(source)
    monkeypatch.setattr(service,'stage',slow)
    async def preview(*_,**__):return b'not-png'
    monkeypatch.setattr(service.renderer,'preview',preview,raising=False)
    timer=QTimer(p);timer.setInterval(5);timer.timeout.connect(lambda:observed.append(time.monotonic()));timer.start()
    before=time.monotonic();p.start(p.vertical_preview());p.tick();after=time.monotonic()
    assert after-before>=.2 and not observed
    timer.stop();p.cancel()
    while p.busy:p.tick()
    p.close()

def test_director_application_previews_selected_nonfirst_scene(directed,monkeypatch):
    from app.providers.ffmpeg_director import FFmpegDirectorRenderer
    from io import BytesIO
    from PIL import Image
    import shutil
    boarded(directed);_,board=directed.storyboard();selected=board['scenes'][-1]
    renderer=FFmpegDirectorRenderer(shutil.which('ffmpeg'),shutil.which('ffprobe'))
    directed.renderer=renderer;calls=[];actual=renderer.preview
    async def recorded(root,local,*args,**kwargs):
        assert len(local['scenes'])==1 and local['scenes'][0]['id']==selected['id']
        assert local['scenes'][0]['start_frame']==0
        return await actual(root,local,*args,**kwargs)
    monkeypatch.setattr(renderer,'preview',recorded)
    def selected_image(scene):
        calls.append(scene['id']);out=BytesIO();Image.new('RGB',(640,360),'blue').save(out,format='PNG')
        return directed.fixture.image,out.getvalue(),{'audit':'synthetic-selected-image'}
    monkeypatch.setattr(directed,'image',selected_image)
    raw=asyncio.run(directed.preview_scene(selected['id']))
    image=Image.open(BytesIO(raw));assert image.size==(1080,1920)
    r,g,b=image.convert('RGB').getpixel((540,900));assert b>200 and r<30 and g<30
    assert calls==[selected['id']]

def test_card_source_seek_before_loaded_starts_at_zero(app,service,tmp_path):
    import subprocess,shutil
    from PySide6.QtMultimedia import QVideoSink
    root=Path(__file__).parent;source=tmp_path/'ninety-seconds.mp4'
    subprocess.run([shutil.which('ffmpeg'),'-v','error','-n','-stream_loop','2','-i',str(root/'landscape/source.mp4'),'-t','90','-c','copy',str(source)],check=True)
    manifest=service.store.save_artifact('source-real.mp4',source.read_bytes(),{'artifact_type':'video_render','project_id':'project'})
    _,e,t=service.publishing.source('original');e={**e,'width':1920,'height':1080,'fps':5,'frame_count':450}
    service.publishing.source=lambda _:(manifest,e,t)
    service.publishing.sources=lambda:[('original',manifest,e)]
    candidates=analyzed(service);p=ShortsPanel();p.bind(service);p.audio.setMuted(True);p.activate()
    sink=QVideoSink();p.player.setVideoSink(sink);frames=[]
    sink.videoFrameChanged.connect(lambda f:frames.append(f.startTime()) if f.isValid() else None)
    p.select_card(candidates[1]['id'],True)
    expected=int(service.prepare(candidates[1],p.draft())[3]*1000);assert expected==30000
    deadline=time.monotonic()+1.5
    while time.monotonic()<deadline:app.processEvents();time.sleep(.01)
    initial={'expected_start_ms':expected,'position_ms':p.player.position(),'frames_us':frames[:8]}
    assert frames and frames[0]<1000000 and p.player.position()<3000
    frames.clear();p.play_source();deadline=time.monotonic()+.8
    while time.monotonic()<deadline:app.processEvents();time.sleep(.01)
    assert p.player.position()>=30000
    initial['retry_position_ms']=p.player.position();initial['retry_frames_us']=frames[:8]
    (root/'panel-immediate-seek.json').write_text(json.dumps(initial,indent=2))
    p.player.stop();p.close()

"""Offline file workers, owner gates, retention and cancellation contracts."""
import asyncio
from hashlib import sha256
from pathlib import Path
from threading import Event,get_ident

import pytest

from app.desktop.artifact_export import copy_artifact_async
from app.runtime.media_process import RenderCanceled
from app.storage.local_store import LocalArtifactStore
from app.storage.media_snapshot import MediaSnapshotStore
from tests.unit.test_shorts import service,analyzed,ready
from tests.integration.test_shorts_analysis_snapshot import indexed_shorts
from tests.integration.test_section_audio import setup
from tests.integration.test_timeline import prepared
from tests.integration.test_video_render import render,claim
from tests.shorts_playback_support import fake_players


def test_source_reads_are_detached_and_receipts_do_not_survive_operations(indexed_shorts,monkeypatch):
    s=indexed_shorts;owner=get_ident();source=s.publishing.renders.selected();calls=[]
    original=MediaSnapshotStore.open_artifact_id
    def opening(store,identity):
        if identity==source.artifact_id:calls.append(get_ident())
        return original(store,identity)
    monkeypatch.setattr(MediaSnapshotStore,'open_artifact_id',opening)
    async def read():
        def verify(reader):
            assert get_ident()!=owner
            first=reader.source();assert reader.source()==first
            reader.publishing.timing('original')
            return first
        return await s.read_async(verify)
    asyncio.run(read())
    assert len(calls)==1 and calls[0]!=owner
    path=s.store._artifact_path(source.storage_key);raw=path.read_bytes()
    path.write_bytes(bytes([raw[0]^1])+raw[1:])
    with pytest.raises(ValueError,match='checksum'):asyncio.run(read())
    assert len(calls)==2


def test_source_changed_after_worker_verification_is_rejected(indexed_shorts):
    s=indexed_shorts;started=Event();release=Event()
    source=s.publishing.renders.selected();path=s.store._artifact_path(source.storage_key)
    def verify(reader):
        value=reader.source();started.set()
        assert release.wait(5)
        return value
    async def run():
        task=asyncio.create_task(s.read_async(verify))
        while not started.is_set():await asyncio.sleep(.001)
        path.write_bytes(path.read_bytes()+b'changed')
        release.set()
        with pytest.raises(ValueError,match='changed'):await task
    try:asyncio.run(run())
    finally:release.set()


def test_qt_heartbeat_and_cancel_continue_while_indexed_source_is_checked(indexed_shorts,fake_players,monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from app.application.shorts import ShortsService
    from app.desktop.shorts_panel import ShortsPanel
    import time
    app=QApplication.instance() or QApplication([]);owner=get_ident();entered=Event();release=Event();beats=[]
    source=ShortsService.source
    def slow(reader,*args):
        if isinstance(reader.store,MediaSnapshotStore):
            assert get_ident()!=owner;entered.set();assert release.wait(5)
        return source(reader,*args)
    monkeypatch.setattr(ShortsService,'source',slow)
    panel=ShortsPanel();panel.bind(indexed_shorts)
    timer=QTimer()
    def beat():
        if entered.is_set():
            beats.append(panel.status.text());panel.cancel();release.set();timer.stop()
    timer.timeout.connect(beat);timer.start(5)
    try:
        panel.activate();assert panel.busy and 'Checking source' in panel.status.text()
        with pytest.raises(ValueError,match='cleanup'):panel.bind(None)
        deadline=time.monotonic()+10
        while panel.busy and time.monotonic()<deadline:
            QApplication.processEvents();QTest.qWait(5)
        assert beats and not panel.busy and 'Canceled' in panel.status.text()
        assert panel.verified_output is None and not panel.export_button.isEnabled()
    finally:
        release.set();timer.stop();panel.close()


def test_async_import_hashes_off_thread_and_registers_only_on_sqlite_owner(indexed_shorts,tmp_path,monkeypatch):
    store=indexed_shorts.store;owner=get_ident();threads=[]
    path=tmp_path/'disposable.mp4';raw=b'video'*(1024*1024);path.write_bytes(raw)
    stage=LocalArtifactStore._stage_stream;register=store._index.register
    def staging(*args,**kwargs):
        assert get_ident()!=owner;threads.append('transfer')
        return stage(*args,**kwargs)
    def registering(manifest):
        assert get_ident()==owner;threads.append('register')
        return register(manifest)
    monkeypatch.setattr(LocalArtifactStore,'_stage_stream',staging)
    monkeypatch.setattr(store._index,'register',registering)
    result=asyncio.run(store.import_file_async('video.mp4',path,{'artifact_type':'test'},checksum=sha256(raw).hexdigest(),size_bytes=len(raw)))
    assert threads==['transfer','register'] and store.read_artifact(result.storage_key)==raw
    before=store.list_artifacts()
    with pytest.raises(ValueError,match='changed'):
        asyncio.run(store.import_file_async('bad.mp4',path,{},checksum='0'*64,size_bytes=len(raw)))
    assert store.list_artifacts()==before


def test_staging_reuses_verified_bytes_and_rejects_corrupt_reuse(service):
    s=service;source=s.fixture_source
    first=asyncio.run(s.stage_async(source));second=asyncio.run(s.stage_async(source))
    assert (first/'source.mp4').samefile(second/'source.mp4')
    assert first!=second and s.store._artifact_path(source.storage_key).read_bytes()==b'immutable parent'
    (second/'source.mp4').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):asyncio.run(s.stage_async(source))
    assert (first/'source.mp4').exists()  # Retain the failed evidence.


def test_staging_checked_copy_fallback_without_hardlinks(service,monkeypatch):
    import app.application.shorts as module
    monkeypatch.setattr(module.os,'link',lambda *_:(_ for _ in ()).throw(OSError('Hard links unavailable')))
    source=service.fixture_source
    first=asyncio.run(service.stage_async(source));second=asyncio.run(service.stage_async(source))
    assert not (first/'source.mp4').samefile(second/'source.mp4')
    assert (first/'source.mp4').read_bytes()==(second/'source.mp4').read_bytes()==b'immutable parent'


def test_staging_cancel_after_copy_begins_keeps_previous_output(service,monkeypatch):
    s=service;p=analyzed(s)[0];previous=ready(s,p)
    source=s.store.save_artifact('big.mp4',b'x'*(4*1024*1024),{'artifact_type':'test_source'})
    started=Event();release=Event();cancel=Event();opening=MediaSnapshotStore.open_artifact_id;stop_checks=[]
    def open_slow(store,identity):
        stream=opening(store,identity)
        if identity!=source.artifact_id:return stream
        stop_checks.append(store.canceled)
        read=stream.read;count=[0]
        def slow(n=-1):
            count[0]+=1
            if count[0]==2:
                started.set();assert release.wait(5)
            return read(n)
        stream.read=slow
        return stream
    monkeypatch.setattr(MediaSnapshotStore,'open_artifact_id',open_slow)
    async def run():
        task=asyncio.create_task(s.stage_async(source,canceled=cancel.is_set))
        while not started.is_set():await asyncio.sleep(.001)
        cancel.set()
        while not stop_checks[0]():await asyncio.sleep(.001)
        release.set()
        with pytest.raises(RenderCanceled):await task
    try:asyncio.run(run())
    finally:release.set()
    assert s.current_output(p['id'])==previous
    partial=[path for path in (s.session.repository.workspace/'work/shorts').glob('*/source.mp4') if path.stat().st_size==1024*1024]
    assert partial


@pytest.mark.parametrize('failure',['source','write','cancel','revision'])
def test_export_failure_retains_destination_and_removes_private_copy(service,tmp_path,monkeypatch,failure):
    from PySide6.QtWidgets import QApplication,QFileDialog
    app=QApplication.instance() or QApplication([])
    s=service;source=s.fixture_source;destination=tmp_path/'saved.mp4';destination.write_bytes(b'previous')
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *_:(str(destination),''))
    calls=[];cancel=Event()
    async def verify():
        calls.append(True)
        if failure=='revision' and len(calls)==2:raise ValueError('Revision changed')
        return source
    if failure=='source':s.store._artifact_path(source.storage_key).write_bytes(b'damaged')
    if failure=='write':
        import app.desktop.artifact_export as module
        monkeypatch.setattr(module.tempfile,'NamedTemporaryFile',lambda **_:(_ for _ in ()).throw(OSError('Disk full')))
    if failure=='cancel':cancel.set()
    with pytest.raises((ValueError,OSError,RenderCanceled)):
        asyncio.run(copy_artifact_async(None,s.store,source,title='Test',suggested='short.mp4',revalidate=verify,canceled=cancel.is_set))
    assert destination.read_bytes()==b'previous'
    assert not list(tmp_path.glob('.saved.mp4.*.tmp'))


def test_cached_lookup_does_not_verify_unrelated_packages(service,monkeypatch):
    s=service;candidates=analyzed(s);videos=[ready(s,p) for p in candidates];calls=[]
    completed=s.artifacts.completed
    def verify(selection):calls.append(selection.artifact_id);return completed(selection)
    monkeypatch.setattr(s.artifacts,'completed',verify)
    assert s.artifacts.cached(videos[0].metadata['fingerprint'])==videos[0]
    assert len(calls)==1


def test_corrupt_package_never_becomes_selected_ui_output(service,fake_players):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from app.desktop.shorts_panel import ShortsPanel
    import time
    app=QApplication.instance() or QApplication([]);s=service;p=analyzed(s)[0];previous=ready(s,p)
    selection=s.artifacts.read(s.artifacts.latest('selection',p['id']))
    package=s.artifacts.one(selection['package_id'],'package')
    s.store._artifact_path(package.storage_key).write_bytes(b'damaged ZIP')
    s.publishing.sources=lambda:[('original',*s.publishing.source('original')[:2])]
    panel=ShortsPanel();panel.bind(s);panel.activate()
    try:
        deadline=time.monotonic()+10
        while panel.busy and time.monotonic()<deadline:
            QApplication.processEvents();QTest.qWait(5)
        assert not panel.busy and panel.value['candidate_id']==p['id']
        assert panel.verified_output is None and not panel.export_button.isEnabled()
        assert 'verification failed' in panel.status.text()
        assert s.store._artifact_path(previous.storage_key).exists()
    finally:panel.close()


def test_duplicate_render_is_rejected_and_cancellation_retains_selection(service,monkeypatch):
    s=service;p=analyzed(s)[0];previous=ready(s,p)
    edit=s.edit(p['id'])[1];edit['title']='New title';s.save_edit(edit,s.artifacts.latest('edit',p['id']).artifact_id)
    started=asyncio.Event();cancel=Event()
    async def encoding(*args,**kwargs):
        started.set()
        while not kwargs['canceled']():await asyncio.sleep(.001)
        raise RenderCanceled('Canceled during FFmpeg')
    monkeypatch.setattr(s.renderer,'render',encoding)
    async def run():
        task=asyncio.create_task(s.render(p['id'],canceled=cancel.is_set))
        await started.wait()
        with pytest.raises(ValueError,match='already running'):await s.render(p['id'])
        cancel.set()
        with pytest.raises(RenderCanceled):await task
    asyncio.run(run())
    assert s.artifacts.selected(p['id'])==previous and len(s.artifacts.history('selection'))==1

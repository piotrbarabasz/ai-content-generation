"""Disposable, offline Shorts I/O benchmark. Run with PYTHONPATH=backend.

The large payload is an I/O surrogate, not a production MP4. The separate
vertical-preview measurement uses an actual tiny FFmpeg-generated MP4.
"""
import asyncio
from hashlib import file_digest
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import shutil
import subprocess
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.desktop import artifact_export
from app.providers.ffmpeg_shorts import FFmpegShortsRenderer
from app.runtime.media_process import MediaProcess
from tests.unit.test_shorts import service, analyzed, ready


def measure(app, action, pending=lambda:False):
    beats = [time.perf_counter()]
    result = {}
    action_started = [False]
    heartbeat = QTimer()
    def beat():
        now=time.perf_counter();beats.append(now)
        if action_started[0] and 'responsive_status_ms' not in result:result['responsive_status_ms']=(now-start)*1000
    heartbeat.timeout.connect(beat)
    heartbeat.start(2)
    loop = asyncio.new_event_loop()
    task = None
    def begin():
        nonlocal task
        result['status_ms'] = (time.perf_counter()-start)*1000
        async def invoke():
            action_started[0]=True
            value = action()
            if asyncio.iscoroutine(value):value=await value
            while pending():await asyncio.sleep(.005)
            return value
        task = loop.create_task(invoke())
    pump = QTimer()
    def tick():
        loop.call_soon(loop.stop)
        loop.run_forever()
        if task is not None and task.done():
            beats.append(time.perf_counter())
            result['seconds'] = time.perf_counter()-start
            result.setdefault('responsive_status_ms',result['seconds']*1000)
            pump.stop(); heartbeat.stop(); app.quit()
    pump.timeout.connect(tick)
    pump.start(2)
    start = time.perf_counter()
    QTimer.singleShot(0, begin)
    app.exec()
    task.result()
    loop.run_until_complete(loop.shutdown_default_executor())
    loop.close()
    result['max_heartbeat_gap_ms'] = max(b-a for a,b in zip(beats, beats[1:]))*1000
    return result


def main():
    app = QApplication.instance() or QApplication([])
    output = {'payload_mib': 256, 'retained_outputs': 3, 'fixture': 'disposable/offline; no providers'}
    with tempfile.TemporaryDirectory(prefix='shorts-media-bench-') as temporary:
        root = Path(temporary)
        s = service.__wrapped__(root)
        candidates = analyzed(s)
        video = ready(s, candidates[0])
        # Retain unrelated outputs to measure history traversal as well.
        ready(s, candidates[1]); ready(s, candidates[2])
        from app.desktop.shorts_panel import ShortsPanel
        s.publishing.sources = lambda:[('original', *s.publishing.source('original')[:2])]
        import importlib.util
        spec=importlib.util.spec_from_file_location('benchmark_fake_player',Path(__file__).resolve().parents[1]/'backend/tests/shorts_playback_support.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);FakePlayer=module.FakePlayer
        with patch('app.desktop.shorts_panel.QMediaPlayer',FakePlayer),patch('app.desktop.director_panel.QMediaPlayer',FakePlayer):
            panel = ShortsPanel(); panel.bind(s)
        output['activate'] = measure(app, panel.activate,lambda:panel.busy)
        output['switch_card'] = measure(app, lambda:panel.choices.setCurrentIndex(1),lambda:panel.busy)
        panel.player.stop(); panel.close()
        path = root/'large-source.mp4'
        with path.open('wb') as stream:
            for _ in range(256): stream.write(b'x'*(1024*1024))
        source=s.store.import_file('large-source.mp4',path,{'artifact_type':'benchmark_source'})
        output['staging'] = measure(app, lambda: s.stage(source)) if not hasattr(s, 'stage_async') else measure(app, lambda: s.stage_async(source))
        output['staging']['bytes_copied'] = source.size_bytes
        if hasattr(s,'stage_async'):
            output['reused_staging']=measure(app,lambda:s.stage_async(source))
            output['reused_staging']['bytes_copied']=0
        from app.runtime.media_process import RenderCanceled
        canceled=[False]
        cancel_timer=QTimer();cancel_timer.setSingleShot(True)
        cancel_timer.timeout.connect(lambda:canceled.__setitem__(0,True))
        async def cancellation():
            cancel_timer.start(20)
            try:
                if hasattr(s,'stage_async'):await s.stage_async(source,canceled=lambda:canceled[0])
                else:s.stage(source)
            except RenderCanceled:output['cancel_honored']=True
        output['cancellation']=measure(app,cancellation)
        cancel_timer.stop()
        output.setdefault('cancel_honored',False)
        # Lookup the original retained fixture output; the source stub provides
        # stable domain identity and does not model production source hashing.
        calls = {'completed': 0, 'verify': 0, 'lookups': 0}
        for name, owner in [('completed', s.artifacts), ('verify', s.artifacts), ('lookups', s.store)]:
            method = 'manifest_by_id' if name == 'lookups' else name
            original = getattr(owner, method)
            def counted(*args, _name=name, _original=original, **kwargs):
                calls[_name] += 1
                return _original(*args, **kwargs)
            setattr(owner, method, counted)
        if hasattr(s,'read_async'):
            def lookup(reader):
                for name,owner,method in [('completed',reader.artifacts,'completed'),('verify',reader.artifacts,'verify'),('lookups',reader.store,'manifest_by_id')]:
                    original=getattr(owner,method)
                    def counted(*args,_name=name,_original=original,**kwargs):
                        calls[_name]+=1
                        return _original(*args,**kwargs)
                    setattr(owner,method,counted)
                return reader.artifacts.cached(video.metadata['fingerprint'])
            output['history_lookup']=measure(app,lambda:s.read_async(lookup))
        else:output['history_lookup'] = measure(app, lambda: s.artifacts.cached(video.metadata['fingerprint']))
        output['history_lookup']['calls'] = dict(calls)
        destination = root/'export.mp4'
        with patch('app.desktop.artifact_export.QFileDialog.getSaveFileName', return_value=(str(destination), '')):
            copy=getattr(artifact_export,'copy_artifact_async',artifact_export.copy_artifact)
            output['export'] = measure(app, lambda: copy(None, s.store, source, title='benchmark', suggested='copy.mp4', revalidate=lambda:source))
        ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
        if ffmpeg and ffprobe:
            media = root/'synthetic.mp4'
            subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=25:d=90',
                '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=90',
                '-c:v','libx264','-preset','ultrafast','-threads','2','-c:a','aac','-shortest',str(media)],check=True)
            preview_service = service.__wrapped__(root/'preview')
            _, evidence, timeline = preview_service.publishing.source('original')
            evidence = {**evidence, 'width':160, 'height':90}
            manifest = preview_service.store.import_file('source.mp4', media, {'artifact_type':'video_render', 'project_id':'project','render':evidence})
            preview_service.publishing.source = lambda _:(manifest,evidence,timeline)
            preview_service.renderer = FFmpegShortsRenderer(ffmpeg, ffprobe, process=MediaProcess())
            candidate = analyzed(preview_service)[0]
            edit = preview_service.edit(candidate['id'])[1]
            output['vertical_preview'] = measure(app, lambda:preview_service.preview(candidate['id'],edit))
    Path(sys.argv[1]).write_text(json.dumps(output, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(output, indent=2))


if __name__ == '__main__': main()

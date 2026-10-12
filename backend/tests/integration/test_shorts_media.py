"""Real CPU FFmpeg checks, no GPU/provider/network dependencies."""
import asyncio
from fractions import Fraction as F
from hashlib import sha256
import shutil
import subprocess
import wave
from array import array
import pytest
from app.domain.caption_track import validate_caption_segments
from app.domain.shorts import ShortStyle, ratio
from app.providers.ffmpeg_shorts import FFmpegShortsRenderer


@pytest.mark.parametrize('mode,fps',[('fill',25),('fit',30)])
def test_real_vertical_excerpt_decode_audio_sync_overlays_and_source_preserved(tmp_path,mode,fps):
    ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
    assert ffmpeg and ffprobe
    subprocess.run([ffmpeg,'-nostdin','-v','error','-f','lavfi','-i',f'testsrc2=s=640x360:r={fps}:d=32',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=32','-af',
        "volume=enable='not(between(t,6,7)+between(t,21,22))':volume=0",'-t','32','-c:v','libx264',
        '-preset','ultrafast','-pix_fmt','yuv420p','-threads','2','-c:a','aac',str(tmp_path/'source.mp4')],check=True)
    original=sha256((tmp_path/'source.mp4').read_bytes()).hexdigest()
    r=FFmpegShortsRenderer(ffmpeg,ffprobe)
    source=asyncio.run(r.probe(tmp_path,'source.mp4',lambda:False))
    evidence={**source,'fully_decoded':True,'video_duration':ratio(source['video_duration']),
        'audio_duration':ratio(source['audio_duration']),'fps':str(source['fps'])}
    segments=validate_caption_segments([{'start_ms':5000,'end_ms':6000,'text':'Original words.'}])
    style=ShortStyle(mode=mode,focal_points=(('left',0),('right',1)),captions=True,burn=True,hook='Known source fact')
    shots=[{'scene_id':'left','start':[0,1],'end':[15,1]},{'scene_id':'right','start':[15,1],'end':[30,1]}]
    report=asyncio.run(r.render(tmp_path,evidence,F(1),F(31),shots,style,segments,canceled=lambda:False,progress=lambda *_:None))
    assert (report['width'],report['height'],report['frame_count'],report['fps'],report['sample_rate'])==(1080,1920,30*fps,str(fps),48000)
    assert F(*report['video_duration'])==30 and abs(F(*report['audio_duration'])-30)<=F(1024,48000)
    assert report['fully_decoded'] and report['packet_timestamps_verified']
    assert sha256((tmp_path/'source.mp4').read_bytes()).hexdigest()==original
    raw=asyncio.run(r.preview(tmp_path,evidence,F(2),shots,style,segments,canceled=lambda:False,relative=1))
    from PIL import Image
    from io import BytesIO
    assert Image.open(BytesIO(raw)).size==(1080,1920)
    subprocess.run([ffmpeg,'-nostdin','-v','error','-i',str(tmp_path/'short.mp4'),'-vn','-c:a','pcm_s16le',str(tmp_path/'audio.wav')],check=True)
    with wave.open(str(tmp_path/'audio.wav'),'rb') as w:
        pcm=array('h',w.readframes(w.getnframes()));rate=w.getframerate()
    def energy(a,b):return sum(x*x for x in pcm[int(a*rate):int(b*rate)])
    # Source beeps at 6/21 seconds must occur at 5/20 seconds after the trim.
    assert energy(5.1,5.9)>1000*max(1,energy(3,3.8))
    assert energy(20.1,20.9)>1000*max(1,energy(18,18.8))

"""Real bounded FFmpeg media, decoded color/arrow markers and display contracts."""
import asyncio
from copy import deepcopy
from fractions import Fraction as F
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import wave

from PIL import Image,ImageDraw
import pytest

from app.domain.shorts import ShortStyle,ratio
from app.providers.ffmpeg_shorts import FFmpegShortsRenderer
from app.providers.ffmpeg_director import FFmpegDirectorRenderer
from app.runtime.media_process import MediaProcess
from app.tts.assembly import inspect_pcm_wav


FF=shutil.which('ffmpeg');FP=shutil.which('ffprobe')


def run(*args):return subprocess.check_output([str(a) for a in args],stderr=subprocess.PIPE)


def probe(path):return json.loads(run(FP,'-v','error','-show_streams','-of','json',path))['streams'][0]


def frame(path,time=0):
    raw=run(FF,'-v','error','-ss',time,'-i',path,'-frames:v','1','-threads','1','-f','image2pipe','-vcodec','png','-')
    return Image.open(BytesIO(raw)).convert('RGB')


def marker(path,size):
    w,h=size;image=Image.new('RGB',size);draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,w//2,h//2),fill='red');draw.rectangle((w//2,0,w,h//2),fill='yellow')
    draw.rectangle((0,h//2,w//2,h),fill='green');draw.rectangle((w//2,h//2,w,h),fill='blue')
    for x in (w//4,3*w//4):draw.polygon([(x,h//20),(x-w//25,h//5),(x+w//25,h//5)],fill='white')
    image.save(path)


def color(image,point,expected):
    actual=image.getpixel(point)
    assert max(abs(a-b) for a,b in zip(actual,expected))<45,(point,actual,expected)


@pytest.fixture(scope='module')
def media(tmp_path_factory):
    assert FF and FP,'Offline FFmpeg/FFprobe required'
    root=tmp_path_factory.mktemp('shorts-geometry')
    for name,size in [('landscape',(320,180)),('portrait',(108,192))]:
        marker(root/f'{name}.png',size)
        run(FF,'-v','error','-loop','1','-framerate','1','-i',root/f'{name}.png',
            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=32','-t','32',
            '-c:v','libx264','-preset','ultrafast','-threads','2','-pix_fmt','yuv420p','-c:a','aac',root/f'{name}.mp4')
    for angle in (90,270):
        run(FF,'-v','error','-display_rotation',angle,'-i',root/'portrait.mp4','-c','copy',root/f'rotation-{angle}.mp4')
    return root


class Recorded(MediaProcess):
    def __init__(self):super().__init__();self.commands=[]
    async def run(self,args,**kwargs):
        self.commands.append([str(arg) for arg in args])
        return await super().run(args,**kwargs)


@pytest.mark.parametrize('source',['landscape','portrait'])
@pytest.mark.parametrize('mode',['fit','fill'])
def test_real_fit_fill_display_and_upright_markers(media,tmp_path,source,mode):
    shutil.copyfile(media/f'{source}.mp4',tmp_path/'source.mp4')
    renderer=FFmpegShortsRenderer(FF,FP)
    info=asyncio.run(renderer.probe(tmp_path,'source.mp4',lambda:False))
    evidence={**info,'fully_decoded':True,'fps':str(info['fps']),
        'video_duration':ratio(info['video_duration']),'audio_duration':ratio(info['audio_duration'])}
    shots=[{'scene_id':'left','start':[0,1],'end':[15,1]},{'scene_id':'right','start':[15,1],'end':[30,1]}]
    style=ShortStyle(mode=mode,focal_points=(('left',0),('right',1)))
    report=asyncio.run(renderer.render(tmp_path,evidence,F(1),F(31),shots,style,(),canceled=lambda:False,progress=lambda *_:None))
    assert report['fully_decoded'] and report['packet_timestamps_verified']
    assert report['frame_count']==30 and report['video_duration']==[30,1]
    assert report['source_start']==[1,1] and report['source_end']==[31,1]
    assert (report['sar'],report['dar'],report['field_order'])==('1:1','9:16','progressive')
    native=probe(tmp_path/'short.mp4')
    assert (native['width'],native['height'],native['sample_aspect_ratio'],native['display_aspect_ratio'])==(1080,1920,'1:1','9:16')
    assert not native.get('tags',{}).get('rotate') and not native.get('side_data_list')
    image=frame(tmp_path/'short.mp4',2)
    if directory:=os.environ.get('SHORTS_GEOMETRY_EVIDENCE_DIR'):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        image.save(directory/f'{source}-{mode}.png')
    if source=='landscape' and mode=='fill':
        color(image,(540,480),(255,0,0));color(image,(540,1440),(0,128,0))
        later=frame(tmp_path/'short.mp4',17)
        color(later,(540,480),(255,255,0));color(later,(540,1440),(0,0,255))
    else:
        upper,lower=(800,1120) if source=='landscape' else (480,1440)
        color(image,(270,upper),(255,0,0));color(image,(810,upper),(255,255,0))
        color(image,(270,lower),(0,128,0));color(image,(810,lower),(0,0,255))
    preview=Image.open(BytesIO(asyncio.run(renderer.preview(tmp_path,evidence,F(3),shots,style,(),canceled=lambda:False,relative=2)))).convert('RGB')
    assert preview.size==image.size
    # Preview and independently decoded output agree on upright scene coverage.
    for point in [(540,480),(540,1440)]:
        assert max(abs(a-b) for a,b in zip(preview.getpixel(point),image.getpixel(point)))<40
    assert sha256((tmp_path/'source.mp4').read_bytes()).hexdigest()==sha256((media/f'{source}.mp4').read_bytes()).hexdigest()


@pytest.mark.parametrize('angle',[90,270])
def test_rotation_rejected_before_crop_and_real_decoded_raster_differs(media,tmp_path,angle):
    shutil.copyfile(media/f'rotation-{angle}.mp4',tmp_path/'source.mp4')
    native=probe(tmp_path/'source.mp4')
    assert (native['width'],native['height'])==(108,192)
    assert any(s.get('rotation')%360==angle for s in native['side_data_list'])
    decoded=frame(tmp_path/'source.mp4');assert decoded.size==(192,108)
    # Native autorotation moves top-left red to a different decoded corner.
    if angle==90:color(decoded,(144,27),(0,0,255))
    else:color(decoded,(144,27),(255,0,0))
    process=Recorded();renderer=FFmpegShortsRenderer(FF,FP,process=process)
    for action in ('preview','render'):
        process.commands.clear()
        with pytest.raises(ValueError,match='Normalize a separate upright MP4'):
            if action=='preview':asyncio.run(renderer.preview(tmp_path,{'width':108,'height':192},F(0),[],ShortStyle(mode='fill'),(),canceled=lambda:False))
            else:asyncio.run(renderer.render(tmp_path,{},F(0),F(30),[],ShortStyle(mode='fill'),(),canceled=lambda:False,progress=lambda *_:None))
        assert process.commands and all(Path(command[0]).name.lower().startswith('ffprobe') for command in process.commands)
    assert not (tmp_path/'short.mp4').exists() and not (tmp_path/'preview.png').exists()


@pytest.mark.parametrize('patch',[
    {'tags':{'rotate':'bad'}}, {'tags':{'rotate':None}}, {'side_data_list':[{'side_data_type':'Display Matrix'}]},
    {'side_data_list':[{'side_data_type':'Display Matrix','rotation':'bad','displaymatrix':'broken'}]},
    {'side_data_list':None}, {'sample_aspect_ratio':'broken'}, {'sample_aspect_ratio':'2:1'},
])
def test_malformed_or_unsupported_metadata_from_real_probe_is_actionable(media,patch):
    video={**probe(media/'portrait.mp4'),**patch}
    renderer=FFmpegShortsRenderer(FF,FP)
    with pytest.raises(ValueError,match='(?i)(rotation|display|square-pixel|pixel aspect)'):renderer.stream_geometry(video)


@pytest.mark.parametrize('patch',[
    {'sar':'2:1'}, {'sar':None}, {'dar':'16:9'}, {'dar':None}, {'field_order':'unknown'},
    {'rotation_metadata':{'tags':{},'matrices':[{'rotation':0}]}},
])
def test_output_validation_requires_explicit_display_proof(patch):
    output={'width':1080,'height':1920,'sar':'1:1','dar':'9:16','field_order':'progressive','rotation_metadata':{'tags':{},'matrices':[]}}
    with pytest.raises(ValueError,match='upright progressive'):FFmpegShortsRenderer.validate_portrait({**output,**patch})


def scene(identity,start,end,mode='fill'):
    return {'id':identity,'start_frame':start,'end_frame':end,'mode':mode,'focal':.5,'zoom':'off','pan':'off'}


def test_director_selected_preview_and_multi_scene_final_decode(media,tmp_path):
    shutil.copyfile(media/'portrait.png',tmp_path/'image-0.png')
    Image.new('RGB',(108,192),'magenta').save(tmp_path/'image-1.png')
    renderer=FFmpegDirectorRenderer(FF,FP)
    one={'fps':'1','scenes':[scene('one',0,15)]}
    raw=asyncio.run(renderer.preview(tmp_path,one,ShortStyle(),(),canceled=lambda:False))
    preview=Image.open(BytesIO(raw)).convert('RGB');assert preview.size==(1080,1920)
    color(preview,(270,480),(255,0,0));color(preview,(270,1440),(0,128,0))
    with wave.open(str(tmp_path/'audio.wav'),'wb') as stream:
        stream.setparams((1,2,48000,0,'NONE','not compressed'));stream.writeframes(b'\x00\x00'*48000*30)
    parameters,_=inspect_pcm_wav((tmp_path/'audio.wav').read_bytes())
    from types import SimpleNamespace
    audio=SimpleNamespace(frame_count=parameters.frame_count,sample_rate=parameters.sample_rate,checksum=sha256((tmp_path/'audio.wav').read_bytes()).hexdigest())
    board={'fps':'1','scenes':[scene('one',0,15),scene('two',15,30,'fit')]}
    report=asyncio.run(renderer.render(tmp_path,board,audio,ShortStyle(),(),canceled=lambda:False,progress=lambda *_:None))
    assert report['fully_decoded'] and report['frame_count']==30 and report['video_duration']==[30,1]
    assert (report['sar'],report['dar'],report['field_order'])==('1:1','9:16','progressive')
    color(frame(tmp_path/'director.mp4',2),(270,480),(255,0,0))
    color(frame(tmp_path/'director.mp4',17),(540,960),(255,0,255))
    assert not probe(tmp_path/'director.mp4').get('side_data_list')


@pytest.mark.parametrize('board',[
    {'fps':'1','scenes':[]}, {'fps':'1','scenes':[scene('one',0,15),scene('two',15,30)]},
    {'fps':'1','scenes':[scene('one',15,30)]}, {'fps':'bad','scenes':[scene('one',0,15)]},
])
def test_director_unsupported_preview_rejected_before_any_process(tmp_path,board):
    process=Recorded();renderer=FFmpegDirectorRenderer(FF,FP,process=process)
    with pytest.raises(ValueError,match='exactly one scene'):
        asyncio.run(renderer.preview(tmp_path,board,ShortStyle(),(),canceled=lambda:False))
    assert not process.commands and not (tmp_path/'preview.png').exists()

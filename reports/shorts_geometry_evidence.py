"""Disposable real-media before/after reproduction; PYTHONPATH chooses revision."""
import asyncio
from fractions import Fraction
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PIL import Image,ImageDraw
from app.domain.shorts import ShortStyle
from app.providers.ffmpeg_shorts import FFmpegShortsRenderer
from app.providers.ffmpeg_director import FFmpegDirectorRenderer
from app.runtime.media_process import MediaProcess

FF=shutil.which('ffmpeg');FP=shutil.which('ffprobe')


def run(*args):return subprocess.check_output([str(a) for a in args],stderr=subprocess.PIPE)


class Recorded(MediaProcess):
    def __init__(self):super().__init__();self.commands=[]
    async def run(self,args,**kwargs):
        self.commands.append([Path(str(args[0])).name,*map(str,args[1:])])
        return await super().run(args,**kwargs)


async def measure(root,destination):
    image=Image.new('RGB',(1080,1920));draw=ImageDraw.Draw(image)
    for box,color in [((0,0,540,960),'red'),((540,0,1080,960),'yellow'),((0,960,540,1920),'green'),((540,960,1080,1920),'blue')]:draw.rectangle(box,fill=color)
    for x in (270,810):draw.polygon([(x,70),(x-90,350),(x+90,350)],fill='white')
    image.save(root/'marker.png')
    run(FF,'-v','error','-loop','1','-framerate','1','-i',root/'marker.png','-f','lavfi','-i',
        'sine=frequency=440:sample_rate=48000:duration=2','-t','2','-c:v','libx264','-preset','ultrafast','-threads','2','-pix_fmt','yuv420p','-c:a','aac',root/'upright.mp4')
    result={};pictures=[('Upright source',image)]
    for angle in (90,270):
        case=root/str(angle);case.mkdir()
        run(FF,'-v','error','-display_rotation',angle,'-i',root/'upright.mp4','-c','copy',case/'source.mp4')
        native=json.loads(run(FP,'-v','error','-show_streams','-of','json',case/'source.mp4'))['streams'][0]
        raw=run(FF,'-v','error','-i',case/'source.mp4','-frames:v','1','-threads','1','-f','image2pipe','-vcodec','png','-')
        decoded=Image.open(BytesIO(raw)).convert('RGB');pictures.append((f'Decoded matrix {angle}',decoded))
        before=sha256((case/'source.mp4').read_bytes()).hexdigest()
        process=Recorded();renderer=FFmpegShortsRenderer(FF,FP,process=process)
        record={'coded':[native['width'],native['height']],'decoded':list(decoded.size),'side_data':native.get('side_data_list',[])}
        try:record['adapter_probe']=await renderer.probe(case,'source.mp4',lambda:False)
        except Exception as error:record['adapter_probe_error']=str(error)
        process.commands.clear()
        try:
            await renderer.preview(case,{'width':1080,'height':1920},Fraction(0),[],ShortStyle(mode='fill'),(),canceled=lambda:False)
            record['preview']='success'
        except Exception as error:record['preview_error']=str(error)[-2000:]
        record['preview_commands']=process.commands
        record['source_unchanged']=sha256((case/'source.mp4').read_bytes()).hexdigest()==before
        result[str(angle)]=record
    case=root/'director';case.mkdir();shutil.copyfile(root/'marker.png',case/'image-0.png');shutil.copyfile(root/'marker.png',case/'image-1.png')
    scene=lambda id,start,end:{'id':id,'start_frame':start,'end_frame':end,'mode':'fill','focal':.5,'zoom':'off','pan':'off'}
    board={'fps':'1','scenes':[scene('a',0,2),scene('b',2,4)]}
    for count in (1,2):
        process=Recorded();renderer=FFmpegDirectorRenderer(FF,FP,process=process);record={}
        try:
            raw=await renderer.preview(case,{**board,'scenes':board['scenes'][:count]},ShortStyle(),(),canceled=lambda:False)
            record['preview']='success';record['decoded']=list(Image.open(BytesIO(raw)).size)
        except Exception as error:record['preview_error']=str(error)[-2000:]
        record['commands']=process.commands;result[f'director-{count}']=record
        (case/'preview.png').unlink(missing_ok=True)
    canvas=Image.new('RGB',(960,400),'#202020');draw=ImageDraw.Draw(canvas)
    for index,(label,picture) in enumerate(pictures):
        picture.thumbnail((300,350));canvas.paste(picture,(index*320+(320-picture.width)//2,35))
        draw.text((index*320+12,12),label,fill='white')
    canvas.save(destination.with_suffix('.png'))
    destination.write_text(json.dumps(result,indent=2,default=str)+'\n',encoding='utf-8')


if __name__=='__main__':
    destination=Path(sys.argv[1]);destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='shorts-geometry-evidence-') as root:asyncio.run(measure(Path(root),destination))

import asyncio, json, shutil, subprocess, sys, wave, time
from pathlib import Path
from fractions import Fraction as F
from hashlib import sha256
from types import SimpleNamespace as NS
from PIL import Image, ImageDraw
sys.path.insert(0, str(Path.cwd()/'backend'))
from app.providers.ffmpeg_shorts import FFmpegShortsRenderer
from app.providers.ffmpeg_director import FFmpegDirectorRenderer
from app.domain.shorts import ShortStyle, ratio
from app.domain.caption_track import validate_caption_segments
from app.runtime.media_process import RenderCanceled
ROOT=Path(__file__).parent
FF=shutil.which('ffmpeg'); FP=shutil.which('ffprobe')
results={}
def run(args):
    return subprocess.run(list(map(str,args)),check=True,capture_output=True).stdout
def measure(path):
    data=json.loads(run([FP,'-v','error','-count_frames','-show_streams','-of','json',path]))
    frame=path.with_suffix('.decoded.png')
    run([FF,'-v','error','-y','-ss','1','-i',path,'-frames:v','1',frame])
    data['decoded_frame']=str(frame); data['decoded_size']=Image.open(frame).size
    data['sha256']=sha256(path.read_bytes()).hexdigest()
    return data
async def main():
    r=FFmpegShortsRenderer(FF,FP); d=FFmpegDirectorRenderer(FF,FP)
    for name,size in [('landscape',(1920,1080)),('portrait',(1080,1920))]:
        base=ROOT/name; base.mkdir(exist_ok=True)
        image=Image.new('RGB',size,'#00b000'); draw=ImageDraw.Draw(image)
        w,h=size; draw.rectangle((0,0,w//3,h),fill='#d00000');draw.rectangle((2*w//3,0,w,h),fill='#0000d0')
        draw.rectangle((w//3,0,2*w//3,h//6),fill='white');draw.text((w//2,h//12),'TOP',fill='black',font_size=64)
        image.save(base/'chart.png')
        run([FF,'-v','error','-n','-loop','1','-framerate','5','-i',base/'chart.png','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=30','-t','30','-c:v','libx264','-preset','ultrafast','-threads','2','-pix_fmt','yuv420p','-c:a','aac',base/'source.mp4'])
        for mode in ['fit','fill']:
            case=ROOT/f'{name}-{mode}';case.mkdir(exist_ok=True);shutil.copyfile(base/'source.mp4',case/'source.mp4')
            info=await r.probe(case,'source.mp4',lambda:False)
            evidence={**info,'fully_decoded':True,'fps':str(info['fps']),'video_duration':ratio(info['video_duration']),'audio_duration':ratio(info['audio_duration'])}
            style=ShortStyle(mode=mode,focal_points=(('left',0),('right',1)),captions=True,burn=True,hook='Synthetic TOP chart')
            shots=[{'scene_id':'left','start':[0,1],'end':[15,1]},{'scene_id':'right','start':[15,1],'end':[30,1]}]
            segments=validate_caption_segments([{'start_ms':0,'end_ms':3000,'text':'Audit caption.'}])
            report=await r.render(case,evidence,F(0),F(30),shots,style,segments,canceled=lambda:False,progress=lambda *_:None)
            await r.preview(case,evidence,F(16),shots,style,segments,canceled=lambda:False,relative=16)
            results[case.name]={'report':report,'probe':measure(case/'short.mp4')}
            # Compare a late rendered frame against preview at the same time/focal point.
            run([FF,'-v','error','-y','-ss','16','-i',case/'short.mp4','-frames:v','1',case/'late.png'])
            print(case.name,'PASS',flush=True)
        rotated=ROOT/f'{name}-rotated';rotated.mkdir(exist_ok=True)
        run([FF,'-v','error','-n','-i',base/'source.mp4','-c','copy','-metadata:s:v:0','rotate=90',rotated/'source.mp4'])
        info=await r.probe(rotated,'source.mp4',lambda:False)
        results[rotated.name]={'source':measure(rotated/'source.mp4')}
        try:
            await r.preview(rotated,info,F(0),[],ShortStyle(mode='fill'),(),canceled=lambda:False)
            results[rotated.name]['preview']='success'
        except Exception as e:results[rotated.name]['error']=str(e)
    for count in [1,2,5]:
        case=ROOT/f'director-{count}';case.mkdir(exist_ok=True)
        scenes=[]
        for i in range(count):
            im=Image.new('RGB',(1920,1080),['red','green','blue','orange','purple'][i]);ImageDraw.Draw(im).text((800,100),f'TOP SCENE {i}',fill='white',font_size=60);im.save(case/f'image-{i}.png')
            scenes.append({'id':f's{i}','start_frame':i*150//count,'end_frame':(i+1)*150//count,'mode':'fit' if i%2 else 'fill','focal':.25,'zoom':'subtle' if i%2 else 'off','pan':'subtle' if i%2 else 'off'})
        board={'fps':'5','scenes':scenes};style=ShortStyle(hook='Director synthetic')
        results[case.name]={}
        try:
            await d.preview(case,board,style,(),canceled=lambda:False);results[case.name]['direct_preview']='success'
        except Exception as e:results[case.name]['direct_preview_error']=str(e)
        with wave.open(str(case/'audio.wav'),'wb') as wav:
            wav.setparams((1,2,48000,0,'NONE','not compressed'));wav.writeframes(b'\0\0'*(30*48000))
        audio=NS(frame_count=30*48000,sample_rate=48000,checksum=sha256((case/'audio.wav').read_bytes()).hexdigest())
        report=await d.render(case,board,audio,style,(),canceled=lambda:False,progress=lambda *_:None)
        results[case.name].update(report=report,probe=measure(case/'director.mp4'))
        for i,s in enumerate(scenes):
            run([FF,'-v','error','-y','-ss',str(i*30/count+1),'-i',case/'director.mp4','-frames:v','1',case/f'scene-{i}.png'])
        print(case.name,'RENDER PASS',flush=True)
    for kind in ['missing','corrupt']:
        case=ROOT/kind;case.mkdir(exist_ok=True)
        if kind=='corrupt':(case/'source.mp4').write_bytes(b'corrupt fixture')
        try:await r.probe(case,'source.mp4',lambda:False)
        except Exception as e:results[kind]={'error':str(e)}
    case=ROOT/'cancel';case.mkdir(exist_ok=True);shutil.copyfile(ROOT/'landscape'/'source.mp4',case/'source.mp4')
    info=await r.probe(case,'source.mp4',lambda:False)
    evidence={**info,'fully_decoded':True,'fps':str(info['fps']),'video_duration':ratio(info['video_duration']),'audio_duration':ratio(info['audio_duration'])}
    started=time.monotonic()
    try:await r.render(case,evidence,F(0),F(30),[],ShortStyle(),(),canceled=lambda:time.monotonic()-started>.4,progress=lambda *_:None)
    except Exception as e:results['cancel']={'type':type(e).__name__,'error':str(e),'elapsed':time.monotonic()-started,'partial_exists':(case/'short.mp4').exists()}
    (ROOT/'measurements.json').write_text(json.dumps(results,indent=2,default=str),encoding='utf-8')
    print('DONE',ROOT,flush=True)
asyncio.run(main())

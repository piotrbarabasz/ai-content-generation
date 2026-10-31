import os,sys,json,subprocess,shutil,time,asyncio
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'backend'))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer,QVideoSink
from PIL import Image,ImageDraw
from app.providers.ffmpeg_shorts import FFmpegShortsRenderer
from app.domain.shorts import ShortStyle,ratio
from fractions import Fraction as F
ROOT=Path(__file__).parent; ff=shutil.which('ffmpeg');fp=shutil.which('ffprobe'); out={}
app=QApplication([])
for name,rel in [('source','landscape/source.mp4'),('fit','landscape-fit/short.mp4'),('fill','landscape-fill/short.mp4'),('director','director-5/director.mp4')]:
    p=QMediaPlayer();sink=QVideoSink();p.setVideoSink(sink);frames=[];errors=[]
    def frame(f):
        if f.isValid() and not frames:
            image=f.toImage();image.save(str(ROOT/f'qt-{name}.png'));frames.append([image.width(),image.height()])
    sink.videoFrameChanged.connect(frame);p.errorOccurred.connect(lambda err,msg:errors.append(msg));p.setSource(QUrl.fromLocalFile(str(ROOT/rel)));p.play()
    limit=time.monotonic()+10
    while not frames and not errors and time.monotonic()<limit:app.processEvents();time.sleep(.01)
    p.stop();out[name]={'frames':frames,'errors':errors,'status':str(p.mediaStatus())}
    env={**os.environ,'SDL_VIDEODRIVER':'dummy','SDL_AUDIODRIVER':'dummy'}
    q=subprocess.run([shutil.which('ffplay'),'-autoexit','-an','-t','1','-loglevel','info','-vf','showinfo',str(ROOT/rel)],env=env,capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
    (ROOT/f'ffplay-{name}.log').write_bytes(q.stderr);out[name]['ffplay_exit']=q.returncode
async def rotation():
    r=FFmpegShortsRenderer(ff,fp)
    for name in ['landscape','portrait']:
        root=ROOT/f'{name}-rotation-matrix';root.mkdir(exist_ok=True)
        subprocess.run([ff,'-v','error','-n','-display_rotation','90','-i',str(ROOT/name/'source.mp4'),'-c','copy',str(root/'source.mp4')],check=True)
        data=json.loads(subprocess.check_output([fp,'-v','error','-show_streams','-of','json',str(root/'source.mp4')]))
        subprocess.run([ff,'-v','error','-y','-i',str(root/'source.mp4'),'-frames:v','1',str(root/'input-decoded.png')],check=True)
        info=await r.probe(root,'source.mp4',lambda:False)
        out[name+'-rotation']={'video':data['streams'][0],'probe':str(info),'decoded_size':Image.open(root/'input-decoded.png').size}
        for mode in ['fit','fill']:
            case=root/mode;case.mkdir(exist_ok=True);shutil.copyfile(root/'source.mp4',case/'source.mp4')
            try:
                await r.preview(case,info,F(0),[],ShortStyle(mode=mode),(),canceled=lambda:False)
                out[name+'-rotation'][mode]='success'
            except Exception as e:out[name+'-rotation'][mode]=str(e)
asyncio.run(rotation())
(ROOT/'playback.json').write_text(json.dumps(out,indent=2,default=str))
# Contact sheet of decoded frames, preserving image proportions.
names=['landscape-fit/short.decoded.png','landscape-fill/short.decoded.png','portrait-fit/short.decoded.png','portrait-fill/short.decoded.png','director-5/scene-0.png','director-5/scene-1.png','director-5/scene-4.png','landscape-fill/late.png']
sheet=Image.new('RGB',(8*220,440),'#202020');draw=ImageDraw.Draw(sheet)
for i,name in enumerate(names):
    im=Image.open(ROOT/name);im.thumbnail((210,380));sheet.paste(im,(i*220,35));draw.text((i*220,5),name.split('/')[0],fill='white')
sheet.save(ROOT/'contact-sheet.png')
print(json.dumps(out,indent=2,default=str))

import asyncio,json,shutil,subprocess,time,sys,os
from pathlib import Path
from fractions import Fraction as F
from hashlib import sha256
sys.path.insert(0,str(Path.cwd()/'backend'))
from app.providers.ffmpeg_shorts import FFmpegShortsRenderer
from app.domain.shorts import ShortStyle,ratio
ROOT=Path(__file__).parent;ff=shutil.which('ffmpeg');fp=shutil.which('ffprobe');out={}
async def main():
    r=FFmpegShortsRenderer(ff,fp)
    for name,mode in [('portrait-rotation-matrix','fit'),('portrait-rotation-matrix','fill'),('landscape-rotation-matrix','fit')]:
        case=ROOT/f'{name}-render-{mode}';case.mkdir(exist_ok=True);shutil.copyfile(ROOT/name/'source.mp4',case/'source.mp4')
        info=await r.probe(case,'source.mp4',lambda:False)
        e={**info,'fully_decoded':True,'fps':str(info['fps']),'video_duration':ratio(info['video_duration']),'audio_duration':ratio(info['audio_duration'])}
        try:
            report=await r.render(case,e,F(0),F(30),[],ShortStyle(mode=mode),(),canceled=lambda:False,progress=lambda *_:None)
            probe=json.loads(subprocess.check_output([fp,'-v','error','-show_streams','-of','json',str(case/'short.mp4')]))
            out[case.name]={'report':report,'probe':probe}
        except Exception as exc:out[case.name]={'error':str(exc)}
    case=ROOT/'mid-encode-cancel';case.mkdir(exist_ok=True);shutil.copyfile(ROOT/'landscape'/'source.mp4',case/'source.mp4')
    info=await r.probe(case,'source.mp4',lambda:False)
    e={**info,'fully_decoded':True,'fps':str(info['fps']),'video_duration':ratio(info['video_duration']),'audio_duration':ratio(info['audio_duration'])}
    encoding=[None]
    def progress(label,*_):
        if label=='Encoding vertical excerpt':encoding[0]=time.monotonic()
    def cancel():return encoding[0] is not None and time.monotonic()-encoding[0]>.3
    try:await r.render(case,e,F(0),F(30),[],ShortStyle(),(),canceled=cancel,progress=progress)
    except Exception as exc:out['mid-encode-cancel']={'type':type(exc).__name__,'error':str(exc),'encoding_started':encoding[0] is not None,'partial_exists':(case/'short.mp4').exists()}
asyncio.run(main())
# Copy an actual rendered synthetic MP4 through the production export helper.
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication,QFileDialog
from app.storage.local_store import LocalArtifactStore
from app.desktop.artifact_export import copy_artifact
app=QApplication([]);store=LocalArtifactStore(ROOT/'export-test'/'artifacts');raw=(ROOT/'landscape-fit'/'short.mp4').read_bytes()
m=store.save_artifact('short.mp4',raw,{'artifact_type':'shorts_video','project_id':'audit-only'})
destination=ROOT/'reopened-export.mp4';QFileDialog.getSaveFileName=lambda *_:(str(destination),'')
copy_artifact(None,store,m,title='Audit',suggested='audit.mp4',revalidate=lambda:m)
probe=json.loads(subprocess.check_output([fp,'-v','error','-show_streams','-of','json',str(destination)]))
out['export']={'sha256':sha256(destination.read_bytes()).hexdigest(),'same_bytes':raw==destination.read_bytes(),'probe':probe}
(ROOT/'extra.json').write_text(json.dumps(out,indent=2,default=str))
print('DONE extra')

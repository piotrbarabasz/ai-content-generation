import os,json,time
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer,QVideoSink
ROOT=Path(__file__).parent;app=QApplication([]);p=QMediaPlayer();sink=QVideoSink();p.setVideoSink(sink)
frames=[];errors=[];out=[]
sink.videoFrameChanged.connect(lambda f:frames.append((f.width(),f.height())) if f.isValid() else None)
p.errorOccurred.connect(lambda code,message:errors.append(message))
def wait():
    deadline=time.monotonic()+8
    while not frames and not errors and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
    assert frames and not errors,(frames,errors,p.errorString())
for label,path,expected in [('original',ROOT/'landscape/source.mp4',(1920,1080)),('final',ROOT/'landscape-fill/short.mp4',(1080,1920)),('switch-other-source',ROOT/'portrait/source.mp4',(1080,1920)),('reopened-export',ROOT/'reopened-export.mp4',(1080,1920))]:
    p.stop();p.setSource(QUrl.fromLocalFile(str(path)));frames.clear();p.play();wait()
    assert frames[-1]==expected
    out.append({'step':label,'frame':frames[-1],'source':p.source().toLocalFile()})
    p.stop();frames.clear();p.play();wait();assert frames[-1]==expected
    out.append({'step':label+'-stop-replay','frame':frames[-1]})
p.stop();(ROOT/'lifecycle.json').write_text(json.dumps({'events':out,'errors':errors},indent=2));print('8 lifecycle checks passed')

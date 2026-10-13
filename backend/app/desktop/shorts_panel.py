"""Focused Shorts review workspace, with audio playback and actual crop preview."""
import asyncio
from fractions import Fraction
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QScrollArea,QLabel,QComboBox,QSpinBox,
    QDoubleSpinBox,QLineEdit,QCheckBox,QPushButton,QLayout)
from app.desktop.artifact_export import copy_artifact
from app.domain.shorts import ShortStyle, ratio


class ShortsPanel(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.service=self.revision=self.value=self.loop=self.task=None
        self.busy=self.canceled=self.loading=self.dirty=False
        self.play_end=None
        layout=QVBoxLayout(self); scroll=QScrollArea(); scroll.setWidgetResizable(True)
        self.body=QWidget(); scroll.setWidget(self.body); layout.addWidget(scroll); form=QFormLayout(self.body); form.setSizeConstraint(QLayout.SetMinimumSize)
        self.status=QLabel("Open a completed Standard project."); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.source=QComboBox(); form.addRow("Verified source",self.source)
        self.info=QLabel(); self.info.setWordWrap(True); form.addRow(self.info)
        self.count=QSpinBox(); self.count.setRange(1,10); self.count.setValue(3); form.addRow("Candidates",self.count)
        self.target=QSpinBox(); self.target.setRange(30,60); self.target.setValue(45); form.addRow("Target seconds (actual 30-60)",self.target)
        def button(text,callback):
            b=QPushButton(text); b.clicked.connect(lambda:self.run(callback)); form.addRow(b); return b
        button("Analyze for Shorts",lambda:self.start(self.analyze()))
        self.choices=QComboBox(); form.addRow("Retained candidates",self.choices)
        self.details=QLabel(); self.details.setWordWrap(True); form.addRow(self.details)
        self.title=QLineEdit(); form.addRow("Working title",self.title)
        button("Preview excerpt with audio",self.play_source)
        button("Accept excerpt",lambda:self.decision(True)); button("Reject excerpt",lambda:self.decision(False))
        self.begin=QComboBox(); self.end=QComboBox(); form.addRow("Verified start boundary",self.begin); form.addRow("Verified end boundary",self.end)
        self.mode=QComboBox(); self.mode.addItems(["fit","fill"]); form.addRow("Framing: fit / blurred background or fill / crop",self.mode)
        self.shot=QComboBox(); form.addRow("Source shot",self.shot)
        self.focal=QDoubleSpinBox(); self.focal.setRange(0,1); self.focal.setSingleStep(.05); self.focal.setValue(.5); form.addRow("Shot focal point: left 0 / center .5 / right 1",self.focal)
        self.warning=QLabel("Fill discards widescreen detail and upscales the crop. Review each shot; this does not add native 1080p detail. Fit preserves the whole frame.")
        self.warning.setWordWrap(True); form.addRow(self.warning)
        self.caps=QCheckBox("Reuse measured synchronized captions (external SRT)"); form.addRow(self.caps)
        self.burn=QCheckBox("Burn captions into video"); form.addRow(self.burn)
        self.font=QSpinBox(); self.font.setRange(32,72); self.font.setValue(54); form.addRow("Caption font size",self.font)
        self.margin=QSpinBox(); self.margin.setRange(280,450); self.margin.setValue(320); form.addRow("Caption bottom safe margin",self.margin)
        self.accent=QLineEdit("FFFFFF"); form.addRow("Caption color / accent (RGB hex)",self.accent)
        self.hook_on=QCheckBox("Enable hook headline (source facts only)"); form.addRow(self.hook_on)
        self.hook=QLineEdit(); self.hook.setMaxLength(100); form.addRow("Hook headline",self.hook)
        self.hook_seconds=QSpinBox(); self.hook_seconds.setRange(1,5); self.hook_seconds.setValue(2); form.addRow("Hook overlay seconds",self.hook_seconds)
        self.preview_time=QDoubleSpinBox(); self.preview_time.setRange(0,59.9); form.addRow("Preview seconds into Short",self.preview_time)
        self.preview=QLabel(); self.preview.setAlignment(Qt.AlignCenter); form.addRow(self.preview)
        button("Preview vertical crop / captions / hook",lambda:self.start(self.vertical_preview()))
        self.caption_preview=QLabel(); self.caption_preview.setWordWrap(True); form.addRow(self.caption_preview)
        button("Use suggested hook (review source facts)",self.suggested_hook)
        button("Save Short edit revision",self.save)
        button("Render selected Short",lambda:self.start(self.render(False)))
        button("Render accepted Shorts",lambda:self.start(self.render(True)))
        self.video=QVideoWidget(); self.video.setMinimumSize(240,300); form.addRow(self.video)
        self.audio=QAudioOutput(self); self.player=QMediaPlayer(self); self.player.setAudioOutput(self.audio); self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self.position)
        button("Play current Short",self.play_short); button("Stop playback",self.player.stop)
        button("Save MP4 copy as...",lambda:self.copy("video"))
        button("Export SRT...",lambda:self.copy("srt")); button("Export metadata...",lambda:self.copy("metadata"))
        button("Save Short package...",lambda:self.copy("package"))
        refresh=QPushButton("Refresh verified source / retained Shorts"); refresh.clicked.connect(lambda:self.run(self.activate)); layout.addWidget(refresh)
        self.cancel_button=QPushButton("Cancel"); self.cancel_button.clicked.connect(self.cancel); self.cancel_button.setEnabled(False); layout.addWidget(self.cancel_button)
        self.timer=QTimer(self); self.timer.setInterval(10); self.timer.timeout.connect(self.tick)
        self.choices.currentIndexChanged.connect(lambda:self.run(self.load))
        self.source.currentIndexChanged.connect(self.source_info)
        self.begin.currentIndexChanged.connect(self.range_changed); self.end.currentIndexChanged.connect(self.range_changed)
        self.shot.currentIndexChanged.connect(self.shot_changed); self.focal.valueChanged.connect(self.crop_changed)
        for widget,signal in [(self.title,"textChanged"),(self.begin,"currentIndexChanged"),(self.end,"currentIndexChanged"),
            (self.mode,"currentIndexChanged"),(self.caps,"toggled"),(self.burn,"toggled"),(self.font,"valueChanged"),
            (self.margin,"valueChanged"),(self.accent,"textChanged"),(self.hook_on,"toggled"),(self.hook,"textChanged"),(self.hook_seconds,"valueChanged")]:
            getattr(widget,signal).connect(self.changed)
        self.body.setEnabled(False)
        from app.desktop.director_panel import DirectorPanel
        self.original_scroll=scroll;self.director_factory=None;self.director=DirectorPanel(self)
        self.production_mode=QComboBox();self.production_mode.addItems(['Original Excerpt','AI Director']);layout.insertWidget(0,self.production_mode)
        layout.insertWidget(2,self.director);self.director.hide()
        self.production_mode.currentIndexChanged.connect(self.change_production_mode)
        create=QPushButton('Create AI-directed version of accepted candidate');create.clicked.connect(lambda:self.run(self.create_directed));form.addRow(create)

    @property
    def busy(self):return getattr(self,'_excerpt_busy',False) or bool(getattr(self,'director',None) and self.director.busy)
    @busy.setter
    def busy(self,value):self._excerpt_busy=value
    def change_production_mode(self,index):
        if self.busy:
            self.production_mode.blockSignals(True);self.production_mode.setCurrentIndex(0 if self._excerpt_busy else 1);self.production_mode.blockSignals(False);return
        self.player.stop();self.director.player.stop();self.original_scroll.setVisible(index==0);self.director.setVisible(index==1)
    def create_directed(self):
        self.production_mode.setCurrentIndex(1);self.director.create()

    def run(self,callback):
        try:return callback()
        except Exception as exc:self.status.setText(str(exc))
    def changed(self,*_):
        if not self.loading:self.dirty=True; self.status.setText("Unsaved Short edits; save before export.")
    def bind(self,service):
        self.player.stop(); self.director.close_service(); self.service=service; self.revision=self.value=None; self.body.setEnabled(False)
        self.status.setText("Open Shorts to verify the final source and restore retained edits.")
    def activate(self):
        if not self.service or self.busy:return
        self.player.stop(); self.source.clear(); self.source_evidence={}
        for kind,manifest,evidence in self.service.publishing.sources():
            try:self.service.source(kind)
            except ValueError:continue
            self.source_evidence[kind]=(manifest,evidence); self.source.addItem(kind,kind)
        self.body.setEnabled(self.source.count()>0)
        if not self.source.count(): self.service.source(); return
        self.source_info()
        self.refresh()
    def source_info(self,*_):
        value=getattr(self,'source_evidence',{}).get(self.source.currentData())
        if not value:return
        source,e=value
        self.info.setText(f"VALID {self.source.currentData()}: {source.artifact_id} | {e['width']} x {e['height']} | {float(Fraction(*e['video_duration'])):.3f}s | {e['fps']} FPS")
    def refresh(self):
        previous=self.choices.currentData(); self.choices.blockSignals(True); self.choices.clear()
        for p in self.service.candidates():
            try:self.service.candidate(p['id']); state="ACCEPTED" if self.service.accepted(p['id']) else "REVIEW / REJECTED"
            except ValueError:state="STALE"
            self.choices.addItem(f"{p['title']} [{state}]",p['id'])
        index=self.choices.findData(previous)
        if index>=0:self.choices.setCurrentIndex(index)
        self.choices.blockSignals(False); self.load()
    async def analyze(self):
        m=await self.service.analyze(self.source.currentData(),self.count.value(),self.target.value(),canceled=lambda:self.canceled)
        self.refresh(); self.status.setText(self.service.artifacts.read(m)['origin'])
    def load(self):
        self.player.stop(); identity=self.choices.currentData()
        if not identity:return
        p=self.service.candidate(identity); self.revision,self.value=self.service.edit(identity)
        self.loading=True
        try:
            self.details.setText(f"{float(Fraction(*p['start'])):.3f} to {float(Fraction(*p['end'])):.3f}s; {float(Fraction(*p['duration'])):.3f}s\nSource: {p['source_kind']} ({p['source_id']})\n{p['topic']}\n{p['reason']}\n{p['hook_assessment']}\nEditorial/audio review required.")
            self.title.setText(self.value['title']); _,_,timeline=self.service.source(p['source_kind']); self.timeline=timeline
            for box,key in [(self.begin,'original_start'),(self.end,'original_end')]:
                box.clear()
                for edge in self.service.boundaries(timeline):box.addItem(f"{float(edge):.3f}s",ratio(edge))
                box.setCurrentIndex(box.findData(self.value[key]))
            s=ShortStyle.from_payload(self.value['style']); self.points=dict(s.focal_points)
            self.mode.setCurrentText(s.mode); self.caps.setChecked(s.captions); self.burn.setChecked(s.burn)
            self.font.setValue(s.font_size); self.margin.setValue(s.margin); self.accent.setText(s.accent)
            self.hook.setText(s.hook); self.hook_on.setChecked(bool(s.hook)); self.hook_seconds.setValue(s.hook_seconds)
            self.shot.clear()
            for c in timeline.clips:
                if c.audio_offset<Fraction(*self.value['original_end']) and c.audio_offset+c.duration>Fraction(*self.value['original_start']):
                    self.shot.addItem(c.media.scene_id,c.media.scene_id)
            self.shot_changed(); self.preview.clear(); self.preview.setMinimumSize(0,0); self.caption_preview.clear(); self.dirty=False
            self.status.setText("Retained Short restored; review the excerpt and save changes before rendering.")
        finally:self.loading=False
    def range_changed(self,*_):
        if self.loading or not self.value or self.begin.currentData() is None or self.end.currentData() is None:return
        a,b=Fraction(*self.begin.currentData()),Fraction(*self.end.currentData())
        if a>=b:return
        included=[c.media.scene_id for c in self.timeline.clips if c.audio_offset<b and c.audio_offset+c.duration>a]
        self.points={k:v for k,v in self.points.items() if k in included}
        self.shot.clear()
        for identity in included:self.shot.addItem(identity,identity)
    def shot_changed(self,*_):
        self.focal.blockSignals(True); self.focal.setValue(self.points.get(self.shot.currentData(),.5) if hasattr(self,'points') else .5); self.focal.blockSignals(False)
    def crop_changed(self,*_):
        if self.loading:return
        if self.shot.currentData():self.points[self.shot.currentData()]=self.focal.value(); self.changed()
    def draft(self):
        if not self.value:raise ValueError("Select an analyzed candidate.")
        style=ShortStyle(mode=self.mode.currentText(),focal_points=tuple(sorted(self.points.items())),captions=self.caps.isChecked(),
            burn=self.burn.isChecked(),font_size=self.font.value(),margin=self.margin.value(),accent=self.accent.text(),
            hook=self.hook.text() if self.hook_on.isChecked() else '',hook_seconds=self.hook_seconds.value())
        return {**self.value,'title':self.title.text(),'original_start':self.begin.currentData(),'original_end':self.end.currentData(),'style':style.to_payload()}
    def save(self):
        self.revision=self.service.save_edit(self.draft(),self.revision.artifact_id if self.revision else None)
        self.value=self.service.artifacts.read(self.revision); self.dirty=False; self.status.setText("Short edit retained; parent Timeline unchanged.")
    def suggested_hook(self):
        p=self.service.candidate(self.choices.currentData())
        if not p['headline']:raise ValueError("No suggested hook; enter a supported headline manually.")
        if len(p['headline'])>100:raise ValueError("Suggested headline needs shortening to 100 characters.")
        self.hook.setText(p['headline']);self.hook_on.setChecked(True)
    def decision(self,accepted):
        self.service.decide(self.choices.currentData(),accepted); self.status.setText("Excerpt accepted." if accepted else "Excerpt rejected; history retained.")
    async def vertical_preview(self):
        p=self.service.candidate(self.choices.currentData()); edit=self.draft()
        prepared=self.service.prepare(p,edit)
        self.caption_preview.setText('\n'.join(s.text for s in prepared[7]) or "Captions disabled. Enable only with verified measured alignment.")
        raw=await self.service.preview(p['id'],edit,Fraction(str(self.preview_time.value())),canceled=lambda:self.canceled)
        pixmap=QPixmap(); pixmap.loadFromData(raw); self.preview.setPixmap(pixmap.scaled(270,480,Qt.KeepAspectRatio,Qt.SmoothTransformation)); self.preview.setMinimumSize(270,480)
        self.status.setText("Actual source frame / crop preview. Review motion and audio in playback.")
    def position(self,position):
        if self.play_end is not None and position>=self.play_end:self.player.pause()
    def play_source(self):
        p=self.service.candidate(self.choices.currentData()); values=self.service.prepare(p,self.draft())
        source,_,_,a,b,*_=values; self.play_end=int(b*1000)
        self.player.setSource(QUrl.fromLocalFile(str(self.service.store._artifact_path(source.storage_key))))
        self.player.setPosition(int(a*1000)); self.player.play()
    def output(self):
        if self.dirty:raise ValueError("Save the edited revision first.")
        return self.service.current_output(self.choices.currentData())
    def play_short(self):
        m=self.output(); self.play_end=None; self.player.setSource(QUrl.fromLocalFile(str(self.service.store._artifact_path(m.storage_key)))); self.player.play()
    async def render(self,batch):
        if self.dirty:raise ValueError("Save the edited revision first.")
        kwargs={'canceled':lambda:self.canceled,'progress':lambda label,*_:self.status.setText(label)}
        result=await self.service.render_accepted(**kwargs) if batch else await self.service.render(self.choices.currentData(),**kwargs)
        self.status.setText("READY: validated Shorts retained." if result else "No accepted Shorts.")
    def copy(self,kind):
        video=self.output()
        selection=self.service.artifacts.latest('selection',self.choices.currentData()); value=self.service.artifacts.read(selection)
        m=video if kind=='video' else self.service.artifacts.one(value[kind+'_id'],kind)
        suffix={'video':'.mp4','srt':'.srt','metadata':'.json','package':'.zip'}[kind]
        def verify():
            if self.output().artifact_id!=video.artifact_id:raise ValueError("Short changed before copy.")
            if kind!='video':self.service.artifacts.records.verified_bytes(m)
            return m
        return copy_artifact(self,self.service.store,m,title='Save Short '+kind,suggested='short'+suffix,revalidate=verify,
            suffix=suffix,file_filter=f"Short {kind} (*{suffix})")
    def start(self,coroutine):
        if self.busy:coroutine.close();return
        self.player.stop(); self.busy=True; self.canceled=False; self.body.setEnabled(False); self.cancel_button.setEnabled(True)
        self.loop=asyncio.new_event_loop();self.task=self.loop.create_task(coroutine);self.timer.start()
    def cancel(self):
        if self.director.busy:self.director.cancel();return
        self.canceled=True;self.status.setText("Cancellation requested; completed Shorts retained.")
    def tick(self):
        self.loop.call_soon(self.loop.stop);self.loop.run_forever()
        if not self.task.done():return
        self.timer.stop()
        try:self.task.result()
        except Exception as exc:self.status.setText(str(exc))
        finally:
            self.loop.close();self.loop=self.task=None;self.busy=False;self.body.setEnabled(bool(self.source.count()));self.cancel_button.setEnabled(False)

"""Explicit Story > Voice > Visuals > Finish workflow inside the Shorts workspace."""
import asyncio
from copy import deepcopy
from fractions import Fraction
from PySide6.QtCore import QTimer,QUrl,Qt
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QScrollArea,QLayout,QLabel,QLineEdit,QPlainTextEdit,QComboBox,
    QSpinBox,QDoubleSpinBox,QCheckBox,QPushButton,QTabWidget)
from app.domain.shorts_director import PARTS,narration
from app.domain.shorts import ShortStyle
from app.desktop.artifact_export import copy_artifact
from app.tts.chunking import sentence_chunks

class DirectorPanel(QWidget):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.service=None;self.busy=self.canceled=self.dirty=self.loading=False
        self.loop=self.task=self.script_revision=self.proposal=self.board_revision=self.board=None
        self.scene_dirty=False;self.loaded_scene=None
        layout=QVBoxLayout(self);self.status=QLabel('Explicitly create an AI-directed version of an accepted candidate.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.language=QLineEdit('en');layout.addWidget(QLabel('Directed Short language (set before creating)'));layout.addWidget(self.language)
        create=QPushButton('Create AI-directed version');create.clicked.connect(lambda:self.run(self.create));layout.addWidget(create)
        self.tabs=QTabWidget();layout.addWidget(self.tabs);self.tabs.setEnabled(False)
        def form(name):
            scroll=QScrollArea();scroll.setWidgetResizable(True);body=QWidget();f=QFormLayout(body);f.setSizeConstraint(QLayout.SetMinimumSize);scroll.setWidget(body);self.tabs.addTab(scroll,name);return f
        def button(text,callback,f):
            b=QPushButton(text);b.clicked.connect(lambda:self.run(callback));f.addRow(b);return b
        story=form('1 Story');self.source_text=QPlainTextEdit();self.source_text.setReadOnly(True);story.addRow('Verified source passages',self.source_text)
        button('Preview original story with audio',self.play_source,story)
        self.target=QSpinBox();self.target.setRange(30,60);self.target.setValue(45);story.addRow('Target seconds',self.target)
        self.style=QLineEdit('clear documentary');story.addRow('Storytelling style',self.style)
        self.cta_enabled=QCheckBox('Enable optional CTA (off by default)');story.addRow(self.cta_enabled)
        button('Create AI Short Script',lambda:self.start(self.generate_script()),story)
        self.proposed=QPlainTextEdit();self.proposed.setReadOnly(True);story.addRow('Retained AI proposal (does not replace manual text)',self.proposed)
        button('Explicitly replace working script with proposal',self.apply_proposal,story)
        self.title=QLineEdit();story.addRow('Short title',self.title);self.parts={}
        for p in PARTS:
            field=QPlainTextEdit();field.setMaximumHeight(100);story.addRow(p.title(),field);self.parts[p]=field;field.textChanged.connect(self.parts_changed)
        self.full=QPlainTextEdit();story.addRow('Full narration (must match parts)',self.full);self.full.textChanged.connect(self.edited);self.title.textChanged.connect(self.edited)
        button('Split full text: first sentence hook / last payoff',self.split_full,story)
        button('Save manual script revision',self.save_script,story)
        button('Discard unsaved edits and reload retained revisions',self.discard_drafts,story)
        self.target.valueChanged.connect(self.edited);self.style.textChanged.connect(self.edited);self.cta_enabled.toggled.connect(self.edited)
        button('Regenerate ONLY hook (preserve other text)',lambda:self.start(self.hook()),story)
        self.fact_review=QCheckBox('I reviewed the facts, source references and standalone meaning');story.addRow(self.fact_review)
        button('Approve current script for narration',self.approve_script,story)
        voice=form('2 Voice');self.voices=QComboBox();voice.addRow('Explicit voice (Chatterbox first when available)',self.voices)
        self.variant=QComboBox();self.variant.addItems(['original','processed']);voice.addRow('Audio variant',self.variant)
        button('Generate new Short narration',lambda:self.start(self.narrate()),voice)
        self.duration=QLabel();self.duration.setWordWrap(True);voice.addRow(self.duration)
        button('Listen to new narration',self.play_audio,voice);button('Approve measured narration',self.approve_audio,voice)
        visuals=form('3 Visuals');button('Generate / retime new storyboard',lambda:self.start(self.storyboard_generate()),visuals)
        self.scenes=QComboBox();visuals.addRow('Measured scene',self.scenes);self.spoken=QLabel();self.spoken.setWordWrap(True);visuals.addRow(self.spoken)
        self.assets=QComboBox();visuals.addRow('Relevant asset / new image required',self.assets)
        self.prompt=QPlainTextEdit();visuals.addRow('Single-image prompt',self.prompt);self.reason=QLineEdit();visuals.addRow('Relevance / reuse rationale',self.reason)
        self.hold=QLineEdit();visuals.addRow('Justification for a >10 second hold',self.hold)
        self.mode=QComboBox();self.mode.addItems(['fit','fill']);visuals.addRow('Fit / blur or fill crop',self.mode)
        self.focal=QDoubleSpinBox();self.focal.setRange(0,1);self.focal.setSingleStep(.05);visuals.addRow('Horizontal focal point',self.focal)
        self.zoom=QCheckBox('Subtle single-pass zoom');visuals.addRow(self.zoom);self.pan=QCheckBox('Optional gentle pan; shake disabled');visuals.addRow(self.pan)
        self.image=QLabel();self.image.setAlignment(Qt.AlignCenter);visuals.addRow(self.image)
        button('Preview selected original/generated image',self.image_preview,visuals)
        button('Preview actual vertical scene / new captions',lambda:self.start(self.vertical_preview()),visuals)
        button('Save this scene only',self.save_scene,visuals)
        self.image_summary=QLabel();self.image_summary.setWordWrap(True);visuals.addRow(self.image_summary)
        button('Review missing image count/provider/model',self.missing,visuals)
        self.paid_approval=QCheckBox('Approve this exact missing-image request list and provider/model');visuals.addRow(self.paid_approval)
        button('Generate ONLY approved missing images',lambda:self.start(self.images_generate()),visuals)
        finish=form('4 Finish');self.burn=QCheckBox('Burn verified NEW narration captions');self.burn.setChecked(True);finish.addRow(self.burn)
        self.font=QSpinBox();self.font.setRange(32,72);self.font.setValue(32);finish.addRow('Caption font size (two-line limit)',self.font)
        self.hook_overlay=QLineEdit();self.hook_overlay.setMaxLength(100);finish.addRow('Optional 2-4 second headline',self.hook_overlay)
        self.hook_seconds=QSpinBox();self.hook_seconds.setRange(2,4);self.hook_seconds.setValue(2);finish.addRow('Headline seconds',self.hook_seconds)
        self.brand=QCheckBox('Use configured D068 logo (no intro/outro cards)');finish.addRow(self.brand)
        self.caption_preview=QPlainTextEdit();self.caption_preview.setReadOnly(True);finish.addRow('Verified sentence captions (no invented word timings)',self.caption_preview)
        button('Save finishing settings / preview SRT',self.save_finish,finish)
        button('Render / preview current directed Short',lambda:self.start(self.render()),finish)
        self.video=QVideoWidget();self.video.setMinimumSize(240,360);finish.addRow(self.video)
        self.audio_output=QAudioOutput(self);self.player=QMediaPlayer(self);self.player.setAudioOutput(self.audio_output);self.player.setVideoOutput(self.video)
        self.end_position=None;self.player.positionChanged.connect(lambda position:self.player.pause() if self.end_position is not None and position>=self.end_position else None)
        button('Play verified directed Short',self.play_output,finish);button('Stop playback',self.player.stop,finish)
        for kind in ('video','srt','metadata'):button('Save '+kind+' copy...',lambda k=kind:self.copy(k),finish)
        self.cancel_button=QPushButton('Cancel current stage');self.cancel_button.clicked.connect(self.cancel);self.cancel_button.setEnabled(False);layout.addWidget(self.cancel_button)
        self.timer=QTimer(self);self.timer.setInterval(10);self.timer.timeout.connect(self.tick)
        self.scenes.currentIndexChanged.connect(lambda:self.run(self.load_scene))
        for field,signal in ((self.prompt,'textChanged'),(self.reason,'textChanged'),(self.hold,'textChanged'),(self.assets,'currentIndexChanged'),(self.mode,'currentIndexChanged'),(self.focal,'valueChanged'),(self.zoom,'toggled'),(self.pan,'toggled')):
            getattr(field,signal).connect(self.scene_edited)
        self.voices.currentIndexChanged.connect(lambda:self.run(self.voice_changed))
        self.variant.currentIndexChanged.connect(lambda:self.run(self.voice_changed))
    def run(self,callback):
        try:return callback()
        except Exception as exc:self.status.setText(str(exc))
    @property
    def unsaved(self):return self.dirty or self.scene_dirty
    def require_saved(self):
        if self.unsaved:raise ValueError('Save or explicitly discard director script/scene edits first.')
    def close_service(self):
        self.require_saved();self.player.stop()
        if self.service:self.service.close()
        self.service=None;self.tabs.setEnabled(False);self.dirty=self.scene_dirty=False
    def create(self):
        if self.busy:raise ValueError('Wait for current director cleanup.')
        if not getattr(self.owner,'director_factory',None):raise ValueError('Director composition is unavailable.')
        identity=self.owner.choices.currentData()
        if not identity:raise ValueError('Analyze and accept a source candidate first.')
        self.close_service();self.service=self.owner.director_factory(self.owner.service,identity,self.language.text().strip())
        self.tabs.setEnabled(True);self.dirty=self.scene_dirty=False;self.board=self.board_revision=self.proposal=None;self.proposed.clear();self.scenes.clear();self.image.clear();self.caption_preview.clear();self.duration.clear();self.populate_script();self.voices.blockSignals(True);self.voices.clear()
        for choice in self.service.scope.choices():self.voices.addItem(choice.label,choice)
        saved=self.service.records.latest('voice_choice')
        if saved:
            saved=self.service.records.read(saved)
            for i in range(self.voices.count()):
                if self.service.scope.audio.selection(self.voices.itemData(i))==saved['selection']:self.voices.setCurrentIndex(i);self.variant.setCurrentText(saved['variant']);break
        self.voices.blockSignals(False)
        self.source_text.setPlainText('\n\n'.join(r+'\n'+v['text'] for r,v in self.service.context()['source_passages'].items()))
        try:self.board_revision,self.board=self.service.storyboard();self.populate_board()
        except ValueError:pass
        settings=self.service.finish();s=ShortStyle.from_payload(settings['style']);self.burn.setChecked(s.burn);self.font.setValue(s.font_size);self.hook_overlay.setText(s.hook);self.hook_seconds.setValue(s.hook_seconds);self.brand.setChecked(settings['branding'])
        self.show_duration();self.refresh()
    def show_duration(self):
        m=self.service.narration_record()
        if m:
            n=self.service.records.read(m);self.duration.setText(f"Measured {float(Fraction(*n['duration'])):.3f}s; target {n['target']}s; deviation {n['deviation_seconds']:+.3f}s. {n['status']}")
    def voice_changed(self):
        if self.service and self.voices.currentData():self.service.set_voice(self.voices.currentData(),self.variant.currentText());self.refresh()
    def parts_changed(self):
        if not self.loading:
            self.full.setPlainText(' '.join(self.parts[p].toPlainText().strip() for p in PARTS if self.parts[p].toPlainText().strip()))
            self.edited()
    def scene_edited(self):
        if not self.loading and self.board:
            self.scene_dirty=True;self.paid_approval.setChecked(False);self.status.setText('Unsaved scene edits; save this scene before proceeding.')
    def discard_drafts(self):
        self.dirty=self.scene_dirty=False;self.populate_script()
        if self.board:self.load_scene()
        self.refresh()
    def edited(self):
        if not self.loading:self.dirty=True;self.fact_review.setChecked(False);self.status.setText('REVIEW REQUIRED: unsaved manual script edits.')
    def populate_script(self):
        self.script_revision,value=self.service.script();self.loading=True
        try:
            if value:
                self.title.setText(value['title']);self.full.setPlainText(value['narration']);self.style.setText(value['style']);self.target.setValue(value['target_duration'])
                for p in PARTS:self.parts[p].setPlainText(value[p])
                self.cta_enabled.setChecked(bool(value['cta']))
            else:
                self.title.clear();self.full.clear()
                for f in self.parts.values():f.clear()
            self.dirty=False;self.fact_review.setChecked(False)
        finally:self.loading=False
    def manual(self):
        _,v=self.service.script()
        if v is None:raise ValueError('Generate a source-grounded script before manual editing.')
        result=deepcopy(v);result.update({p:self.parts[p].toPlainText().strip() for p in PARTS})
        result.update(title=self.title.text(),style=self.style.text(),target_duration=self.target.value(),narration=self.full.toPlainText().strip())
        if result['cta'] and not self.cta_enabled.isChecked():raise ValueError('Enable CTA explicitly or clear its text.')
        result['beats']=[{'text':result[p],'visual_concept':result[p],'source_refs':result['source_refs']} for p in PARTS if result[p]]
        result['claims_review']=['Manual text requires source fact review.'];return result
    def save_script(self):
        self.service.edit_script(self.manual(),self.script_revision.artifact_id);self.populate_script();self.refresh()
    def split_full(self):
        text=self.full.toPlainText().strip();chunks=sentence_chunks(text,max_words=1000)
        if len(chunks)<3:raise ValueError('Provide at least three complete sentences for hook/development/payoff.')
        spans=[c.source_span for c in chunks]
        self.parts['hook'].setPlainText(text[spans[0].start:spans[0].end].strip())
        self.parts['development'].setPlainText(text[spans[1].start:spans[-2].end].strip())
        self.parts['payoff'].setPlainText(text[spans[-1].start:spans[-1].end].strip());self.parts['cta'].clear()
        self.full.setPlainText(' '.join(self.parts[p].toPlainText().strip() for p in PARTS if self.parts[p].toPlainText().strip()))
    async def generate_script(self):
        self.proposal=await self.service.generate_script(self.target.value(),self.style.text(),cta=self.cta_enabled.isChecked(),canceled=lambda:self.canceled)
        self.proposed.setPlainText(self.service.records.read(self.proposal)['script']['narration'])
        if self.script_revision is None:self.populate_script()
        self.refresh()
    def apply_proposal(self):
        if self.proposal is None:raise ValueError('Generate a proposal first.')
        self.require_saved();self.service.apply_proposal(self.proposal.artifact_id,replace_current=True);self.populate_script();self.refresh()
    async def hook(self):
        if self.dirty:raise ValueError('Save manual edits before hook-only regeneration.')
        await self.service.regenerate_hook(canceled=lambda:self.canceled);self.populate_script();self.refresh()
    def approve_script(self):
        if self.dirty or not self.fact_review.isChecked():raise ValueError('Save the script and explicitly review its source facts first.')
        self.service.approve_script();self.refresh()
    async def narrate(self):
        if self.dirty:raise ValueError('Save and approve script edits before narration.')
        if self.voices.currentData() is None:raise ValueError('Choose an available installed voice.')
        self.require_saved();m=await self.service.generate_narration(self.voices.currentData(),variant=self.variant.currentText(),canceled=lambda:self.canceled,progress=self.progress)
        n=self.service.records.read(m);self.duration.setText(f"Measured {float(Fraction(*n['duration'])):.3f}s; target {n['target']}s; deviation {n['deviation_seconds']:+.3f}s. {n['status']}");self.refresh()
    def approve_audio(self):self.service.approve_narration();self.refresh()
    def play_audio(self):
        m=self.service.narration_record()
        if m is None:raise ValueError('Generate narration first.')
        n=self.service.records.read(m);_,_,manifest=self.service.audio_bytes(n)
        self.end_position=None;self.player.setSource(QUrl.fromLocalFile(str(self.service.scope.store._artifact_path(manifest.storage_key))));self.player.play()
    def play_source(self):
        p=self.service.source();m,_,_=self.owner.service.source(p['source_kind'])
        self.tabs.setCurrentIndex(3);self.end_position=int(Fraction(*p['end'])*1000);self.player.setSource(QUrl.fromLocalFile(str(self.owner.service.store._artifact_path(m.storage_key))));self.player.setPosition(int(Fraction(*p['start'])*1000));self.player.play()
    async def storyboard_generate(self):
        self.require_saved();self.board_revision=await self.service.generate_storyboard(canceled=lambda:self.canceled);self.board=self.service.records.read(self.board_revision);self.populate_board();self.refresh()
    def populate_board(self):
        self.scene_dirty=False;self.scenes.blockSignals(True);self.scenes.clear()
        for s in self.board['scenes']:self.scenes.addItem(s['text'][:65],s['id'])
        self.scenes.blockSignals(False);self.load_scene()
    def load_scene(self):
        if not self.board or not self.scenes.currentData():return
        if self.scene_dirty:
            self.scenes.blockSignals(True);self.scenes.setCurrentIndex(self.scenes.findData(self.loaded_scene));self.scenes.blockSignals(False)
            raise ValueError('Save or discard scene edits before selecting another scene.')
        self.loading=True
        try:
            s=next(s for s in self.board['scenes'] if s['id']==self.scenes.currentData());self.assets.clear();self.assets.addItem('New image required (explicit approval)',None)
            for id,a in self.service.assets().items():self.assets.addItem(a['description'][:90],id)
            self.assets.setCurrentIndex(self.assets.findData(s['asset_ref']));self.prompt.setPlainText(s['prompt']);self.reason.setText(s['rationale']);self.hold.setText(s['hold_reason'])
            self.mode.setCurrentText(s['mode']);self.focal.setValue(s['focal']);self.zoom.setChecked(s['zoom']=='subtle');self.pan.setChecked(s['pan']=='subtle')
            self.spoken.setText(f"{s['start_frame']} to {s['end_frame']} frames; {s['text']}\n"+('Reused source image' if s['asset_ref'] else 'New image required'))
            self.image.clear();self.paid_approval.setChecked(False);self.loaded_scene=s['id'];self.scene_dirty=False
        finally:self.loading=False
    def save_scene(self):
        if not self.board:raise ValueError('Generate a storyboard first.')
        b=deepcopy(self.board);s=next(s for s in b['scenes'] if s['id']==self.scenes.currentData())
        s.update(asset_ref=self.assets.currentData(),prompt=self.prompt.toPlainText(),rationale=self.reason.text(),hold_reason=self.hold.text(),mode=self.mode.currentText(),focal=self.focal.value(),zoom='subtle' if self.zoom.isChecked() else 'off',pan='subtle' if self.pan.isChecked() else 'off')
        self.board_revision=self.service.save_board(b,self.board_revision.artifact_id);self.board=b;self.scene_dirty=False;self.paid_approval.setChecked(False);self.refresh()
    def image_preview(self):
        self.require_saved()
        s=next(s for s in self.board['scenes'] if s['id']==self.scenes.currentData());_,raw,_=self.service.image(s)
        pixmap=QPixmap();pixmap.loadFromData(raw);self.image.setPixmap(pixmap.scaled(270,360,Qt.KeepAspectRatio,Qt.SmoothTransformation));self.image.setMinimumHeight(360)
    async def vertical_preview(self):
        self.require_saved();raw=await self.service.preview_scene(self.scenes.currentData(),canceled=lambda:self.canceled)
        pixmap=QPixmap();pixmap.loadFromData(raw);self.image.setPixmap(pixmap.scaled(270,480,Qt.KeepAspectRatio,Qt.SmoothTransformation));self.image.setMinimumSize(270,480)
    def missing(self):
        self.require_saved();pending,token=self.service.missing_images();providers={item['identity']['provider']['provider']+'/'+item['identity']['provider']['model'] for item in pending}
        self.image_summary.setText(f"{len(pending)} new image(s); provider/model: {', '.join(providers) or 'none; all reused/cached'}. Reliable cost estimate unavailable.")
        self.image_token=token;return token
    async def images_generate(self):
        self.require_saved()
        if not self.paid_approval.isChecked():raise ValueError('Review and approve the exact missing-image count/provider/model.')
        self.service.approve_images(getattr(self,'image_token',None));await self.service.generate_missing_images(canceled=lambda:self.canceled,progress=self.progress);self.refresh()
    def save_finish(self):
        value={'style':ShortStyle(captions=True,burn=self.burn.isChecked(),font_size=self.font.value(),hook=self.hook_overlay.text(),hook_seconds=self.hook_seconds.value()).to_payload(),'branding':self.brand.isChecked()}
        self.service.save_finish(value);segments,_=self.service.captions();self.caption_preview.setPlainText('\n'.join(f'{s.start_ms}-{s.end_ms} ms: {s.text}' for s in segments));self.refresh()
    async def render(self):
        if self.dirty:raise ValueError('Save/review script edits before rendering.')
        self.require_saved();self.save_finish();await self.service.render(canceled=lambda:self.canceled,progress=self.progress);self.refresh()
    def play_output(self):
        self.require_saved()
        m=self.service.current_output();self.end_position=None;self.player.setSource(QUrl.fromLocalFile(str(self.service.records.store._artifact_path(m.storage_key))));self.player.play()
    def copy(self,kind):
        self.require_saved()
        video=self.service.current_output();selection=self.service.records.selection(video.artifact_id)
        m=video if kind=='video' else self.service.records.one(selection[kind+'_id'],kind)
        suffix={'video':'.mp4','metadata':'.json','srt':'.srt'}[kind]
        def verify():
            if self.service.current_output().artifact_id!=video.artifact_id:raise ValueError('Director selection changed before copy.')
            if kind!='video':self.service.records.bytes(m)
            return m
        return copy_artifact(self,self.service.records.store,m,title='Save directed '+kind,suggested='directed-short'+suffix,revalidate=verify,suffix=suffix,file_filter='Directed '+kind+' (*'+suffix+')')
    def refresh(self):self.status.setText(' | '.join(k.upper()+': '+v for k,v in self.service.status().items()))
    def progress(self,label,*_):self.status.setText(label)
    def start(self,coroutine):
        if self.busy:coroutine.close();return
        self.player.stop();self.busy=True;self.canceled=False;self.tabs.setEnabled(False);self.cancel_button.setEnabled(True)
        self.loop=asyncio.new_event_loop();self.task=self.loop.create_task(coroutine);self.timer.start()
    def cancel(self):self.canceled=True;self.status.setText('Cancellation requested; waiting for provider/process cleanup.')
    def tick(self):
        self.loop.call_soon(self.loop.stop);self.loop.run_forever()
        if not self.task.done():return
        self.timer.stop()
        try:self.task.result()
        except Exception as exc:self.status.setText(str(exc))
        finally:self.loop.close();self.loop=self.task=None;self.busy=False;self.tabs.setEnabled(True);self.cancel_button.setEnabled(False)

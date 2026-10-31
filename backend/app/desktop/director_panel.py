"""Explicit Story > Voice > Visuals > Finish workflow inside the Shorts workspace."""
import asyncio
import inspect
from copy import deepcopy
from fractions import Fraction
from PySide6.QtCore import QTimer,Qt
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QScrollArea,QLayout,QLabel,QLineEdit,QPlainTextEdit,QComboBox,
    QSpinBox,QDoubleSpinBox,QCheckBox,QPushButton,QTabWidget)
from app.domain.shorts_director import PARTS,narration
from app.domain.shorts import ShortStyle
from app.desktop.artifact_export import copy_artifact_async
from app.desktop.shorts_playback import ShortsPlayback, DisplayedMedia, present_video
from app.tts.chunking import sentence_chunks
from app.runtime.media_process import RenderCanceled
from app.runtime.media_worker import media_error_text

class DirectorPanel(QWidget):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.service=None;self.busy=self.canceled=self.dirty=self.loading=False
        self.loop=self.task=self.script_revision=self.proposal=self.board_revision=self.board=None
        self.scene_dirty=self.finish_dirty=self.output_ready=False;self.loaded_scene=None
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
        self.finish_readiness=QLabel('Render the saved finishing settings to create a directed MP4.');self.finish_readiness.setWordWrap(True);finish.addRow(self.finish_readiness)
        self.caption_preview=QPlainTextEdit();self.caption_preview.setReadOnly(True);finish.addRow('Verified sentence captions (no invented word timings)',self.caption_preview)
        button('Save finishing settings / preview SRT',self.save_finish,finish)
        button('Render / preview current directed Short',lambda:self.start(self.render()),finish)
        self.media_label=QLabel('No media loaded');self.media_label.setWordWrap(True);finish.addRow(self.media_label)
        self.video=QVideoWidget();present_video(self.video,DisplayedMedia.NO_MEDIA,director=True);finish.addRow(self.video)
        self.audio_output=QAudioOutput(self);self.player=QMediaPlayer(self);self.player.setAudioOutput(self.audio_output);self.player.setVideoOutput(self.video)
        self.playback=ShortsPlayback(self.player,self.playback_context,self)
        self.playback.changed.connect(self.media_changed);self.playback.failed.connect(self.playback_failed)
        self.play_output_button=button('Play verified directed Short',self.play_output,finish);button('Stop playback',self.playback.stop,finish)
        self.copy_buttons={kind:button('Save '+kind+' copy...',lambda k=kind:self.copy(k),finish) for kind in ('video','srt','metadata')}
        self.media_error=QLabel();self.media_error.setWordWrap(True);layout.addWidget(self.media_error);self.media_error.hide()
        self.dismiss_playback_error=QPushButton('Dismiss playback error');self.dismiss_playback_error.clicked.connect(self.dismiss_media_error);layout.addWidget(self.dismiss_playback_error);self.dismiss_playback_error.hide()
        self.cancel_button=QPushButton('Cancel current stage');self.cancel_button.clicked.connect(self.cancel);self.cancel_button.setEnabled(False);layout.addWidget(self.cancel_button)
        self.timer=QTimer(self);self.timer.setInterval(10);self.timer.timeout.connect(self.tick)
        self.scenes.currentIndexChanged.connect(lambda:self.run(self.load_scene))
        for field,signal in ((self.prompt,'textChanged'),(self.reason,'textChanged'),(self.hold,'textChanged'),(self.assets,'currentIndexChanged'),(self.mode,'currentIndexChanged'),(self.focal,'valueChanged'),(self.zoom,'toggled'),(self.pan,'toggled')):
            getattr(field,signal).connect(self.scene_edited)
        self.voices.currentIndexChanged.connect(lambda:self.run(self.voice_changed))
        self.variant.currentIndexChanged.connect(lambda:self.run(self.voice_changed))
        for field,signal in ((self.burn,'toggled'),(self.font,'valueChanged'),(self.hook_overlay,'textChanged'),(self.hook_seconds,'valueChanged'),(self.brand,'toggled')):
            getattr(field,signal).connect(self.finish_edited)
    def run(self,callback):
        try:return callback()
        except Exception as exc:self.status.setText(('Canceled: ' if isinstance(exc,RenderCanceled) else 'Failed: ')+media_error_text(exc))
    @property
    def unsaved(self):return self.dirty or self.scene_dirty or self.finish_dirty
    def require_saved(self,*,allow_finish=False):
        if self.dirty or self.scene_dirty or self.finish_dirty and not allow_finish:
            raise ValueError('Save or explicitly discard director script/scene/finishing edits first.')
    def playback_context(self):return id(self.service),self.service.candidate_id if self.service else None
    @property
    def end_position(self):return self.playback.request.end if self.playback.request else None
    def media_changed(self):
        self.media_label.setText(self.playback.label);self.media_label.setToolTip(self.player.source().toLocalFile())
        present_video(self.video,self.playback.request.kind if self.playback.request else DisplayedMedia.NO_MEDIA,director=True)
        self.video.setVisible(not self.playback.pending and self.playback.kind not in (DisplayedMedia.NO_MEDIA,DisplayedMedia.PLAYBACK_ERROR))
        self.media_error.setText(self.playback.error_message);self.media_error.setVisible(bool(self.playback.error_message));self.dismiss_playback_error.setVisible(bool(self.playback.error_message))
    def playback_failed(self,message):self.status.setText(message)
    def dismiss_media_error(self):
        self.playback.dismiss_error()
        if self.service:self.refresh()
    def finish_edited(self):
        if self.service and not self.loading:
            self.finish_dirty=True;self.playback.clear();self.refresh()
    def restore_finish(self):
        previous=self.loading;self.loading=True
        try:
            settings=self.service.finish();s=ShortStyle.from_payload(settings['style'])
            self.burn.setChecked(s.burn);self.font.setValue(s.font_size);self.hook_overlay.setText(s.hook);self.hook_seconds.setValue(s.hook_seconds);self.brand.setChecked(settings['branding']);self.finish_dirty=False
        finally:self.loading=previous
    def close_service(self):
        if self.busy:raise ValueError('Wait for current director cleanup before closing.')
        self.require_saved();self.playback.clear();self.playback.dismiss_error()
        if self.service:self.service.close()
        self.service=None;self.tabs.setEnabled(False);self.dirty=self.scene_dirty=self.finish_dirty=False
        self.output_ready=False;self.finish_readiness.setText('No directed Short loaded.')
        self.play_output_button.setEnabled(False)
        for control in self.copy_buttons.values():control.setEnabled(False)
        self.status.setText('Explicitly create an AI-directed version of an accepted candidate.')
    def create(self):
        if self.busy:raise ValueError('Wait for current director cleanup.')
        if not getattr(self.owner,'director_factory',None):raise ValueError('Director composition is unavailable.')
        identity=self.owner.choices.currentData()
        if not identity:raise ValueError('Analyze and accept a source candidate first.')
        self.close_service();self.start(self._create(identity))
    async def _create(self,identity):
        candidate=await self.owner.service.read_async('candidate',identity,canceled=lambda:self.canceled)
        factory=self.owner.director_factory
        kwargs={'_verified_candidate':candidate} if '_verified_candidate' in inspect.signature(factory).parameters else {}
        self.service=factory(self.owner.service,identity,self.language.text().strip(),**kwargs)
        self.tabs.setEnabled(True);self.dirty=self.scene_dirty=False;self.board=self.board_revision=self.proposal=None;self.proposed.clear();self.scenes.clear();self.image.clear();self.caption_preview.clear();self.duration.clear();self.populate_script();self.voices.blockSignals(True);self.voices.clear()
        for choice in self.service.scope.choices():self.voices.addItem(choice.label,choice)
        saved=self.service.records.latest('voice_choice')
        if saved:
            saved=self.service.records.read(saved)
            for i in range(self.voices.count()):
                if self.service.scope.audio.selection(self.voices.itemData(i))==saved['selection']:self.voices.setCurrentIndex(i);self.variant.setCurrentText(saved['variant']);break
        self.voices.blockSignals(False)
        def view(reader):
            try:board=reader.storyboard()
            except ValueError:board=(None,None)
            return reader.context(),board,reader.assets(),reader.status()
        context,board,self.asset_values,state=await self.service.read_async(view,canceled=lambda:self.canceled)
        self.source_text.setPlainText('\n\n'.join(r+'\n'+v['text'] for r,v in context['source_passages'].items()))
        self.board_revision,self.board=board
        if self.board:self.populate_board()
        self.restore_finish()
        self.show_duration();self._show_state(state)
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
        self.restore_finish()
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
        self.start(self._save_script(self.manual(),self.script_revision.artifact_id))
    async def _save_script(self,value,expected):
        await self.service.read_async('edit_script',value,expected,canceled=lambda:self.canceled)
        self.populate_script();await self._refresh()
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
        await self._refresh()
    def apply_proposal(self):
        if self.proposal is None:raise ValueError('Generate a proposal first.')
        self.require_saved();self.start(self._apply_proposal())
    async def _apply_proposal(self):
        await self.service.read_async('apply_proposal',self.proposal.artifact_id,replace_current=True,canceled=lambda:self.canceled);self.populate_script();await self._refresh()
    async def hook(self):
        if self.dirty:raise ValueError('Save manual edits before hook-only regeneration.')
        await self.service.regenerate_hook(canceled=lambda:self.canceled);self.populate_script();await self._refresh()
    def approve_script(self):
        if self.dirty or not self.fact_review.isChecked():raise ValueError('Save the script and explicitly review its source facts first.')
        self.start(self._approve('approve_script'))
    async def _approve(self,method):
        await self.service.read_async(method,canceled=lambda:self.canceled);await self._refresh()
    async def narrate(self):
        if self.dirty:raise ValueError('Save and approve script edits before narration.')
        if self.voices.currentData() is None:raise ValueError('Choose an available installed voice.')
        self.require_saved();m=await self.service.generate_narration(self.voices.currentData(),variant=self.variant.currentText(),canceled=lambda:self.canceled,progress=self.progress)
        n=self.service.records.read(m);self.duration.setText(f"Measured {float(Fraction(*n['duration'])):.3f}s; target {n['target']}s; deviation {n['deviation_seconds']:+.3f}s. {n['status']}");await self._refresh()
    def approve_audio(self):self.start(self._approve('approve_narration'))
    def play_audio(self):self.start(self._play_audio())
    async def _play_audio(self):
        def audio(reader):
            m=reader.narration_record()
            if m is None:raise ValueError('Generate narration first.')
            n=reader.records.read(m)
            return reader.audio_bytes(n)[2]
        manifest=await self.service.read_async(audio,canceled=lambda:self.canceled)
        self.status.setText('Director narration selected · audio playback.')
        self.playback.load(self.service.scope.store._artifact_path(manifest.storage_key),DisplayedMedia.NARRATION,self.playback_context(),autoplay=True,retry=True)
    def play_source(self):self.start(self._play_source())
    async def _play_source(self):
        def source(reader):
            p=reader.source();return p,reader.shorts.source(p['source_kind'])[0]
        p,m=await self.service.read_async(source,canceled=lambda:self.canceled)
        self.tabs.setCurrentIndex(3);self.status.setText('Original excerpt selected · source framing.')
        self.playback.load(self.owner.service.store._artifact_path(m.storage_key),DisplayedMedia.ORIGINAL_EXCERPT,self.playback_context(),
            start=int(Fraction(*p['start'])*1000),end=int(Fraction(*p['end'])*1000),autoplay=True,retry=True)
    async def storyboard_generate(self):
        self.require_saved();self.board_revision=await self.service.generate_storyboard(canceled=lambda:self.canceled);self.board=self.service.records.read(self.board_revision)
        self.asset_values=await self.service.read_async('assets',canceled=lambda:self.canceled)
        self.populate_board();await self._refresh()
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
            for id,a in (getattr(self,'asset_values',None) or self.service._assets or {}).items():self.assets.addItem(a['description'][:90],id)
            self.assets.setCurrentIndex(self.assets.findData(s['asset_ref']));self.prompt.setPlainText(s['prompt']);self.reason.setText(s['rationale']);self.hold.setText(s['hold_reason'])
            self.mode.setCurrentText(s['mode']);self.focal.setValue(s['focal']);self.zoom.setChecked(s['zoom']=='subtle');self.pan.setChecked(s['pan']=='subtle')
            self.spoken.setText(f"{s['start_frame']} to {s['end_frame']} frames; {s['text']}\n"+('Reused source image' if s['asset_ref'] else 'New image required'))
            self.image.clear();self.paid_approval.setChecked(False);self.loaded_scene=s['id'];self.scene_dirty=False
        finally:self.loading=False
    def save_scene(self):
        if not self.board:raise ValueError('Generate a storyboard first.')
        b=deepcopy(self.board);s=next(s for s in b['scenes'] if s['id']==self.scenes.currentData())
        s.update(asset_ref=self.assets.currentData(),prompt=self.prompt.toPlainText(),rationale=self.reason.text(),hold_reason=self.hold.text(),mode=self.mode.currentText(),focal=self.focal.value(),zoom='subtle' if self.zoom.isChecked() else 'off',pan='subtle' if self.pan.isChecked() else 'off')
        self.start(self._save_scene(b,self.board_revision.artifact_id))
    async def _save_scene(self,b,expected):
        self.board_revision=await self.service.read_async('save_board',b,expected,canceled=lambda:self.canceled)
        self.board=b;self.scene_dirty=False;self.paid_approval.setChecked(False);await self._refresh()
    def image_preview(self):
        self.require_saved()
        self.start(self._image_preview())
    async def _image_preview(self):
        s=next(s for s in self.board['scenes'] if s['id']==self.scenes.currentData());_,raw,_=await self.service.read_async('image',s,canceled=lambda:self.canceled)
        pixmap=QPixmap();pixmap.loadFromData(raw);self.image.setPixmap(pixmap.scaled(270,360,Qt.KeepAspectRatio,Qt.SmoothTransformation));self.image.setMinimumHeight(360)
    async def vertical_preview(self):
        self.require_saved();raw=await self.service.preview_scene(self.scenes.currentData(),canceled=lambda:self.canceled,progress=self.progress)
        pixmap=QPixmap();pixmap.loadFromData(raw);self.image.setPixmap(pixmap.scaled(270,480,Qt.KeepAspectRatio,Qt.SmoothTransformation));self.image.setMinimumSize(270,480)
    def missing(self):
        self.require_saved();self.start(self._missing())
    async def _missing(self):
        pending,token=await self.service.read_async('missing_images',canceled=lambda:self.canceled);providers={item['identity']['provider']['provider']+'/'+item['identity']['provider']['model'] for item in pending}
        self.image_summary.setText(f"{len(pending)} new image(s); provider/model: {', '.join(providers) or 'none; all reused/cached'}. Reliable cost estimate unavailable.")
        self.image_token=token;return token
    async def images_generate(self):
        self.require_saved()
        if not self.paid_approval.isChecked():raise ValueError('Review and approve the exact missing-image count/provider/model.')
        await self.service.read_async('approve_images',getattr(self,'image_token',None),canceled=lambda:self.canceled);await self.service.generate_missing_images(canceled=lambda:self.canceled,progress=self.progress);await self._refresh()
    def save_finish(self):
        self.start(self._save_finish())
    async def _save_finish(self):
        value={'style':ShortStyle(captions=True,burn=self.burn.isChecked(),font_size=self.font.value(),hook=self.hook_overlay.text(),hook_seconds=self.hook_seconds.value()).to_payload(),'branding':self.brand.isChecked()}
        await self.service.read_async('save_finish',value,canceled=lambda:self.canceled)
        segments,_=await self.service.read_async('captions',canceled=lambda:self.canceled);self.caption_preview.setPlainText('\n'.join(f'{s.start_ms}-{s.end_ms} ms: {s.text}' for s in segments));self.finish_dirty=False
        await self._refresh()
    async def render(self):
        if self.dirty:raise ValueError('Save/review script edits before rendering.')
        self.require_saved(allow_finish=True);await self._save_finish();context=self.playback_context()
        await self.service.render(canceled=lambda:self.canceled,progress=self.progress)
        if context!=self.playback_context():raise ValueError('Director selection changed during rendering.')
        await self._load_output(autoplay=False);await self._refresh()
    def load_output(self,*,autoplay):
        self.start(self._load_output(autoplay=autoplay))
    async def _load_output(self,*,autoplay):
        self.output_ready=False
        m=await self.service.read_async('current_output',canceled=lambda:self.canceled);self.output_ready=True;self.status.setText('Directed Short ready. Play the verified portrait MP4 or save a copy.')
        self.playback.load(self.service.records.store._artifact_path(m.storage_key),DisplayedMedia.DIRECTED_SHORT,self.playback_context(),autoplay=autoplay,retry=True)
    def play_output(self):
        self.require_saved()
        self.load_output(autoplay=True)
    def copy(self,kind):
        self.require_saved()
        self.start(self._copy(kind))
    async def _copy(self,kind):
        video=await self.service.read_async('current_output',canceled=lambda:self.canceled);selection=self.service.records.selection(video.artifact_id)
        m=video if kind=='video' else self.service.records.one(selection[kind+'_id'],kind)
        suffix={'video':'.mp4','metadata':'.json','srt':'.srt'}[kind]
        async def verify():
            self.require_saved()
            if (await self.service.read_async('current_output',canceled=lambda:self.canceled)).artifact_id!=video.artifact_id:raise ValueError('Director selection changed before copy.')
            return m
        return await copy_artifact_async(self,self.service.records.store,m,title='Save directed '+kind,suggested='directed-short'+suffix,revalidate=verify,suffix=suffix,file_filter='Directed '+kind+' (*'+suffix+')',canceled=lambda:self.canceled,progress=self.progress)
    def refresh(self):
        if self.busy:
            self.refresh_pending=True
            return
        if not self.finish_dirty:
            self.start(self._refresh())
            return
        self._show_state(None)
    async def _refresh(self):
        self.output_ready=False
        self.finish_readiness.setText('Checking source and saved output…')
        state=await self.service.read_async('status',canceled=lambda:self.canceled)
        self._show_state(state)
    def _show_state(self,state):
        if self.finish_dirty:
            label='Unsaved finishing settings. Previous verified MP4 retained; save/discard these edits or render them.'
            ready=False
        else:
            ready=state.get('render')=='READY' and not self.unsaved
            label='Directed Short ready. Play the verified portrait MP4 or save a copy.' if ready else ' | '.join(k.upper()+': '+v for k,v in state.items())
        self.finish_readiness.setText(label)
        self.output_ready=ready
        self.play_output_button.setEnabled(ready and not self.busy)
        for control in self.copy_buttons.values():control.setEnabled(ready and not self.busy)
        self.status.setText(self.playback.error_message or label)
    def progress(self,label,*_):self.status.setText(self.playback.error_message or label)
    def start(self,coroutine):
        if self.busy or self.owner._excerpt_busy:coroutine.close();return
        self.status.setText(self.playback.error_message or 'Checking source')
        self.playback.stop();self.busy=True;self.canceled=False;self.tabs.setEnabled(False);self.cancel_button.setEnabled(True)
        self.loop=asyncio.new_event_loop();self.task=self.loop.create_task(coroutine);self.timer.start()
    def cancel(self):self.canceled=True;self.status.setText('Cancellation requested; waiting for provider/process cleanup.')
    def tick(self):
        self.loop.call_soon(self.loop.stop);self.loop.run_forever()
        if not self.task.done():return
        self.timer.stop()
        failed=False
        try:self.task.result()
        except Exception as exc:
            failed=True
            self.status.setText(('Canceled: ' if isinstance(exc,RenderCanceled) else 'Failed: ')+media_error_text(exc))
            if not self.output_ready:self.finish_readiness.setText(self.status.text())
        finally:
            self.loop.close();self.loop=self.task=None;self.busy=False;self.tabs.setEnabled(True);self.cancel_button.setEnabled(False)
            self.play_output_button.setEnabled(self.output_ready and not self.unsaved)
            for control in self.copy_buttons.values():control.setEnabled(self.play_output_button.isEnabled())
            if getattr(self,'refresh_pending',False):
                self.refresh_pending=False
                if not failed:self.refresh()

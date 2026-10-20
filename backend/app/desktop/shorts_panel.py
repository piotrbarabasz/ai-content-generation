"""Focused Shorts review workspace, with audio playback and actual crop preview."""
import asyncio
import zipfile
from copy import deepcopy
from types import SimpleNamespace
from enum import Enum, auto
from fractions import Fraction
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QScrollArea,QLabel,QComboBox,QSpinBox,
    QDoubleSpinBox,QLineEdit,QCheckBox,QPushButton,QLayout,QGroupBox,QToolButton,QSizePolicy)
from app.desktop.artifact_export import copy_artifact_async
from app.desktop.shorts_playback import ShortsPlayback, DisplayedMedia, present_video
from app.domain.shorts import ShortStyle, ratio
from app.runtime.media_process import RenderCanceled
from app.runtime.media_worker import media_error_text


class ShortsState(Enum):
    SOURCE_NOT_READY = auto()
    READY_TO_ANALYZE = auto()
    WORKING = auto()
    REVIEWING = auto()
    SHORT_READY = auto()
    ERROR = auto()


class CandidateCard(QGroupBox):
    """A keyboard-accessible selection and preview of one retained excerpt."""

    def __init__(self, candidate, title, state, select, play, parent=None):
        super().__init__(parent)
        self.identity = candidate['id']
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 6, 6, 6)
        self.select_button = QPushButton(title)
        self.select_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.select_button.setToolTip(title)
        self.select_button.setCheckable(True)
        self.select_button.clicked.connect(select)
        column = QVBoxLayout()
        column.setSpacing(2)
        column.addWidget(self.select_button)
        self.summary = QLabel(
            f"{float(Fraction(*candidate['duration'])):g}s | {candidate['topic']} | {state}")
        self.summary.setWordWrap(True)
        column.addWidget(self.summary)
        row.addLayout(column, 1)
        self.play_button = QPushButton("Play")
        self.play_button.setAccessibleName(f"Preview {title}")
        self.play_button.clicked.connect(play)
        row.addWidget(self.play_button)


class ShortsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = self.revision = self.value = self.loop = self.task = None
        self.busy = self.canceled = self.loading = self.dirty = False
        self.status_error = False
        self.drafts = {}
        self.cards = []
        self.verified_output = None
        self.card_values = []
        self.view = None
        self.state = ShortsState.SOURCE_NOT_READY
        layout = QVBoxLayout(self)
        self.original_scroll = QScrollArea()
        self.original_scroll.setWidgetResizable(True)
        self.body = QWidget()
        self.original_scroll.setWidget(self.body)
        layout.addWidget(self.original_scroll)
        flow = QVBoxLayout(self.body)

        def section(title):
            box = QGroupBox(title)
            flow.addWidget(box)
            return box, QVBoxLayout(box)

        def button(text, callback, target):
            control = QPushButton(text)
            control.clicked.connect(lambda: self.run(callback))
            target.addWidget(control)
            return control

        self.step1, find = section("1. Find Shorts")
        self.info = QLabel("No verified source. Complete and verify the documentary first.")
        self.info.setWordWrap(True)
        find.addWidget(self.info)
        self.provider_info = QLabel()
        self.provider_info.setWordWrap(True)
        find.addWidget(self.provider_info)
        self.analyze_button = button("Find 3 Shorts", lambda: self.start(self.analyze(), "Checking source"), find)

        self.step2, review = section("2. Review and Create")
        columns = QHBoxLayout()
        review.addLayout(columns)
        self.card_scroll = QScrollArea()
        self.card_scroll.setWidgetResizable(True)
        self.card_scroll.setMinimumWidth(280)
        self.card_scroll.setMinimumHeight(270)
        self.card_scroll.setMaximumHeight(290)
        card_body = QWidget()
        self.card_layout = QVBoxLayout(card_body)
        self.card_layout.setContentsMargins(4, 4, 4, 4)
        self.card_layout.setSpacing(4)
        self.card_layout.setAlignment(Qt.AlignTop)
        self.card_scroll.setWidget(card_body)
        columns.addWidget(self.card_scroll, 1)
        self.edit_controls = QWidget()
        edit = QVBoxLayout(self.edit_controls)
        edit.setContentsMargins(0, 0, 0, 0)
        fields = QFormLayout()
        edit.addLayout(fields)
        self.title = QLineEdit()
        self.title.setMaxLength(100)
        fields.addRow("Working title", self.title)
        self.mode = QComboBox()
        self.mode.addItems(["fit", "fill"])
        fields.addRow("Fit / Fill", self.mode)
        self.caps = QCheckBox("Captions")
        self.caps.setToolTip("Export verified external SRT captions. Enable burn-in under Advanced for visible captions.")
        edit.addWidget(self.caps)
        self.captions_reason = QLabel()
        self.captions_reason.setWordWrap(True)
        edit.addWidget(self.captions_reason)
        self.remove_captions_button = button("Remove unavailable captions", self.remove_captions, edit)
        self.remove_captions_button.hide()
        self.hook_on = QCheckBox("Hook headline")
        edit.addWidget(self.hook_on)
        self.hook = QLineEdit()
        self.hook.setMaxLength(100)
        self.hook.setPlaceholderText("Optional source-supported headline")
        self.hook.setEnabled(False)
        self.hook_on.toggled.connect(self.hook.setEnabled)
        edit.addWidget(self.hook)
        button("Play original excerpt (source framing)", self.play_source, edit)
        self.source_stop_button = button("Stop original playback", lambda: self.playback.stop(), edit)
        self.accept_check = QCheckBox("I reviewed and accept this excerpt")
        self.accept_check.setToolTip("Confirm the facts, standalone meaning, audio and selected boundaries.")
        edit.addWidget(self.accept_check)
        self.accept_check.toggled.connect(self.update_workflow)
        self.create_button = button("Create Short", self.create_short, edit)
        self.review_reason = QLabel("Review the excerpt before creating a Short.")
        self.review_reason.setWordWrap(True)
        edit.addWidget(self.review_reason)
        columns.addWidget(self.edit_controls, 1)
        self.video = QVideoWidget()
        self.media_view = QWidget()
        media_layout = QVBoxLayout(self.media_view)
        media_layout.setContentsMargins(0, 0, 0, 0)
        self.media_label = QLabel("No media loaded")
        self.media_label.setWordWrap(True)
        self.media_label.setMaximumWidth(200)
        media_layout.addWidget(self.media_label)
        media_layout.addWidget(self.video)
        columns.addWidget(self.media_view)
        self.review_columns = columns
        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.playback = ShortsPlayback(self.player, self.playback_context, self)
        self.playback.changed.connect(self.media_changed)
        self.playback.failed.connect(self.error)
        present_video(self.video, DisplayedMedia.NO_MEDIA)

        self.step3, finish = section("3. Preview and Export")
        self.output_info = QLabel("Create a Short to unlock a verified MP4.")
        self.output_info.setWordWrap(True)
        finish.addWidget(self.output_info)
        self.finish_row = QHBoxLayout()
        finish.addLayout(self.finish_row)
        actions = QVBoxLayout()
        self.finish_row.addLayout(actions)
        self.play_button = button("Play current Short", self.play_short, actions)
        self.export_button = button("Save MP4", lambda: self.copy("video"), actions)
        button("Play original excerpt (source framing)", self.play_source, actions)
        button("Edit selected Short", self.edit_selected, actions)
        button("Stop playback", self.playback.stop, actions)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("Advanced settings")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.RightArrow)
        flow.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        flow.addWidget(self.advanced)
        form = QFormLayout(self.advanced)
        form.setSizeConstraint(QLayout.SetMinimumSize)
        form.addRow(QLabel("<b>Analysis</b>"))
        self.advanced_toggle.toggled.connect(self.toggle_advanced)
        self.advanced.hide()
        self.source = QComboBox()
        form.addRow("Analysis source", self.source)
        self.count = QSpinBox()
        self.count.setRange(1, 10)
        self.count.setValue(3)
        form.addRow("Candidates", self.count)
        self.target = QSpinBox()
        self.target.setRange(30, 60)
        self.target.setValue(45)
        form.addRow("Target seconds", self.target)
        self.choices = QComboBox()
        form.addRow("Retained revisions / candidates", self.choices)
        self.details = QLabel()
        self.details.setWordWrap(True)
        form.addRow(self.details)
        form.addRow(QLabel("<b>Timeline</b>"))
        self.begin = QComboBox()
        self.end = QComboBox()
        form.addRow("Verified start boundary", self.begin)
        form.addRow("Verified end boundary", self.end)
        form.addRow(QLabel("<b>Framing</b>"))
        self.shot = QComboBox()
        form.addRow("Source shot", self.shot)
        self.focal = QDoubleSpinBox()
        self.focal.setRange(0, 1)
        self.focal.setSingleStep(.05)
        self.focal.setValue(.5)
        form.addRow("Focal point: left 0 / center .5 / right 1", self.focal)
        self.warning = QLabel("Fit preserves the whole frame. Fill crops widescreen detail; review each shot.")
        self.warning.setWordWrap(True)
        form.addRow(self.warning)
        form.addRow(QLabel("<b>Captions and overlays</b>"))
        self.burn = QCheckBox("Burn captions into video")
        form.addRow(self.burn)
        self.external_captions = QCheckBox("External captions (SRT)")
        form.insertRow(form.rowCount()-1, self.external_captions)
        self.external_captions.toggled.connect(self.caps.setChecked)
        self.caps.toggled.connect(self.external_captions.setChecked)
        self.caps.toggled.connect(lambda enabled: self.burn.setChecked(False) if not enabled and not self.loading else None)
        self.font = QSpinBox()
        self.font.setRange(32, 72)
        self.font.setValue(54)
        form.addRow("Caption font size", self.font)
        self.margin = QSpinBox()
        self.margin.setRange(280, 450)
        self.margin.setValue(320)
        form.addRow("Caption bottom safe margin", self.margin)
        self.accent = QLineEdit("FFFFFF")
        form.addRow("Caption color (RGB hex)", self.accent)
        self.hook_seconds = QSpinBox()
        self.hook_seconds.setRange(1, 5)
        self.hook_seconds.setValue(2)
        form.addRow("Hook overlay seconds", self.hook_seconds)
        self.preview_time = QDoubleSpinBox()
        self.preview_time.setRange(0, 59.9)
        form.addRow("Preview seconds into Short", self.preview_time)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        form.addRow(QLabel("Portrait crop / captions / hook · still preview"))
        form.addRow(self.preview)
        # Keep technical controls on the same widgets/state used by Simple Mode.
        def technical(text, callback):
            control = QPushButton(text)
            control.clicked.connect(lambda: self.run(callback))
            form.addRow(control)
            return control
        technical("Preview vertical crop / captions / hook", lambda: self.start(self.vertical_preview()))
        self.caption_preview = QLabel()
        self.caption_preview.setWordWrap(True)
        form.addRow(self.caption_preview)
        technical("Use suggested hook (review source facts)", self.suggested_hook)
        technical("Accept excerpt", lambda: self.decision(True))
        technical("Reject excerpt", lambda: self.decision(False))
        form.addRow(QLabel("<b>Outputs and troubleshooting</b>"))
        technical("Save Short edit revision", self.save)
        technical("Render selected Short", lambda: self.start(self.render(False)))
        technical("Render accepted Shorts", lambda: self.start(self.render(True)))
        technical("Export SRT...", lambda: self.copy("srt"))
        technical("Export metadata...", lambda: self.copy("metadata"))
        technical("Save Short package...", lambda: self.copy("package"))
        technical("Refresh verified source / retained Shorts", self.activate)
        flow.addStretch()
        self.status = QLabel("Open a completed Standard project.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.media_error = QLabel()
        self.media_error.setWordWrap(True)
        layout.addWidget(self.media_error)
        self.dismiss_playback_error = QPushButton("Dismiss playback error")
        self.dismiss_playback_error.clicked.connect(self.dismiss_media_error)
        layout.addWidget(self.dismiss_playback_error)
        self.media_error.hide(); self.dismiss_playback_error.hide()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        layout.addWidget(self.cancel_button)
        self.timer = QTimer(self)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self.tick)
        self.choices.currentIndexChanged.connect(lambda: self.run(self.load, explicit=False))
        self.source.currentIndexChanged.connect(self.source_info)
        self.count.valueChanged.connect(lambda n: self.analyze_button.setText(f"Find {n} Shorts"))
        self.begin.currentIndexChanged.connect(self.range_changed)
        self.end.currentIndexChanged.connect(self.range_changed)
        self.shot.currentIndexChanged.connect(self.shot_changed)
        self.focal.valueChanged.connect(self.crop_changed)
        for widget, signal in [(self.title,"textChanged"),(self.begin,"currentIndexChanged"),(self.end,"currentIndexChanged"),
            (self.mode,"currentIndexChanged"),(self.caps,"toggled"),(self.burn,"toggled"),(self.font,"valueChanged"),
            (self.margin,"valueChanged"),(self.accent,"textChanged"),(self.hook_on,"toggled"),(self.hook,"textChanged"),(self.hook_seconds,"valueChanged")]:
            getattr(widget,signal).connect(self.changed)
        from app.desktop.director_panel import DirectorPanel
        self.director_factory = None
        self.director = DirectorPanel(self)
        layout.insertWidget(1, self.director)
        self.director.hide()
        self.production_mode = QComboBox()
        self.production_mode.addItems(["Original Excerpt", "AI Director"])
        form.addRow("Advanced -> AI Director", self.production_mode)
        self.production_mode.currentIndexChanged.connect(self.change_production_mode)
        # This return control stays available when the Original Excerpt panel is hidden.
        self.return_button = QPushButton("Back to Original Excerpt")
        self.return_button.clicked.connect(lambda: self.production_mode.setCurrentIndex(0))
        layout.insertWidget(0, self.return_button)
        self.return_button.hide()
        technical("Create AI-directed version of accepted candidate", self.create_directed)
        self.update_workflow()

    def toggle_advanced(self, expanded):
        self.advanced.setVisible(expanded)
        self.advanced_toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)

    def edit_selected(self):
        self.verified_output = None
        self.playback.clear()
        self.update_workflow()

    def playback_context(self):
        return id(self.service), self.choices.currentData()

    @property
    def play_end(self):
        return self.playback.request.end if self.playback.request else None

    def media_changed(self):
        self.media_label.setText(self.playback.label)
        self.media_label.setToolTip(self.player.source().toLocalFile())
        kind = self.playback.request.kind if self.playback.request else DisplayedMedia.NO_MEDIA
        present_video(self.video, kind)
        self.video.setVisible(not self.playback.pending and self.playback.kind not in
            (DisplayedMedia.NO_MEDIA, DisplayedMedia.PLAYBACK_ERROR))
        if hasattr(self, 'media_error'):
            self.media_error.setText(self.playback.error_message)
            self.media_error.setVisible(bool(self.playback.error_message))
            self.dismiss_playback_error.setVisible(bool(self.playback.error_message))

    def dismiss_media_error(self):
        self.playback.dismiss_error()
        self.status_error = False
        self.status.setText("Playback error dismissed. Choose original excerpt or verified Short to retry.")
        self.update_workflow()

    def remember_draft(self):
        if self.value and self.dirty:
            draft = deepcopy(self.draft())
            # Disabling a headline affects rendering, but must not erase its
            # working text when navigating between candidates.
            draft['style']['hook'] = self.hook.text()
            self.drafts[self.value['candidate_id']] = (
                self.revision, draft, self.accept_check.isChecked(), self.hook_on.isChecked())

    def select_card(self, identity, play=False):
        if self.busy:
            if getattr(self,'navigating',False):
                self.pending_card=(identity,play);self.canceled=True
            return
        self.choices.blockSignals(True)
        self.choices.setCurrentIndex(self.choices.findData(identity))
        self.choices.blockSignals(False)
        self.update_card_selection()
        self.start(self._load(play=play), 'Checking source',preserve_error=not play,navigating=True)

    def play_selected(self):
        self.start(self._play_selected(), 'Checking source')

    async def _play_selected(self):
        if not self.dirty:
            try:media=await self.service.read_async('current_output',self.choices.currentData(),canceled=lambda:self.canceled)
            except ValueError:pass
            else:
                self._play_verified(media)
                return
        await self._cue_source(autoplay=True,retry=True)

    def rebuild_cards(self):
        while self.card_layout.count():
            widget = self.card_layout.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        self.cards = []
        for p, edit, state in self.card_values:
            identity = p['id']
            draft = self.drafts.get(identity)
            edit = draft[1] if draft else edit
            card = CandidateCard(p, edit['title'], state,
                lambda checked=False, key=identity: self.run(lambda: self.select_card(key), explicit=False),
                lambda checked=False, key=identity: self.run(lambda: self.select_card(key, True)))
            card.setEnabled(state != "Stale source")
            self.card_layout.addWidget(card)
            self.cards.append(card)
        self.update_card_selection()

    def update_card_selection(self):
        for card in self.cards:
            card.select_button.setChecked(card.identity == self.choices.currentData())
            if self.value and card.identity == self.value['candidate_id']:
                card.select_button.setText(self.title.text())
                card.select_button.setToolTip(self.title.text())

    def update_workflow(self, *_):
        ready = bool(self.service and self.source.count())
        selected = bool(self.value and self.value['candidate_id'] == self.choices.currentData())
        self.state = (ShortsState.WORKING if self.busy else
            ShortsState.ERROR if self.status_error else
            ShortsState.SOURCE_NOT_READY if not ready else
            ShortsState.SHORT_READY if self.verified_output and not self.dirty else
            ShortsState.REVIEWING if selected else ShortsState.READY_TO_ANALYZE)
        self.body.setEnabled(not self.busy)
        self.analyze_button.setEnabled(ready and not self.busy)
        self.analyze_button.setToolTip("Analyze the verified source using the provider shown above." if ready else
            "Complete and verify a Standard documentary first.")
        self.step2.setVisible(bool(self.choices.count()))
        rendered = bool(ready and selected and self.verified_output and not self.dirty)
        self.card_scroll.setMinimumHeight(230 if rendered else 270)
        self.card_scroll.setMaximumHeight(250 if rendered else 290)
        self.step3.setVisible(rendered)
        original = bool(self.playback.request and self.playback.request.kind == DisplayedMedia.ORIGINAL_EXCERPT)
        self.edit_controls.setVisible(not rendered or original)
        self.edit_controls.setEnabled(ready and selected)
        (self.finish_row if rendered and not original else self.review_columns).addWidget(self.media_view)
        self.media_view.setVisible(selected)
        self.create_button.setEnabled(ready and selected and self.accept_check.isChecked() and not self.busy)
        self.review_reason.setText("Ready to create using local rendering." if self.accept_check.isChecked() else
            "Editorial review required. Preview, then confirm acceptance above.")
        if not selected:
            self.review_reason.setText("Select a current candidate; stale sources require fresh analysis.")
        self.play_button.setEnabled(rendered and not self.busy)
        self.export_button.setEnabled(rendered and not self.busy)
        self.production_mode.setEnabled(not self.busy)
        self.return_button.setEnabled(not self.busy)
        self.cancel_button.setVisible(self._excerpt_busy)
        self.update_card_selection()
        if self.playback.error_message and not self.status_error:
            self.status_error = True
            self.status.setText(self.playback.error_message)

    def check_output(self):
        self.start(self._check_output(),'Verifying',preserve_error=True)
    async def _check_output(self):
        self.verified_output=None
        if self.view and self.value and not self.dirty:
            try:self.view['output']=await self.service.read_async('current_output',self.choices.currentData(),canceled=lambda:self.canceled)
            except (ValueError,OSError,KeyError,zipfile.BadZipFile):self.view['output']=None
        self._present_output()
    def _present_output(self):
        self.verified_output = (self.view['output'] if self.view and self.value and not self.dirty
            and self.view['revision']==self.revision and self.view['value']==self.value else None)
        if self.verified_output:
            evidence = self.verified_output.metadata['shorts']
            self.output_info.setText(
                f"Saved verified MP4: {self.value['title']} | {evidence['width']} x {evidence['height']} | "
                f"{float(Fraction(*evidence['video_duration'])):g}s | Verified MP4")
            self.playback.load(self.service.store._artifact_path(self.verified_output.storage_key),
                DisplayedMedia.VERIFIED_SHORT, self.playback_context())
            if not self.status_error and not self.busy:
                self.status.setText("Short ready. Preview, then Save MP4.")
        self.update_workflow()

    def check_captions(self):
        self.start(self._check_captions(),'Verifying captions',preserve_error=True)
    async def _check_captions(self):
        if not self.view or not self.value:return
        identity=self.choices.currentData();draft=self.draft();draft['style']=ShortStyle(captions=True).to_payload()
        def check(reader):
            try:reader.prepare(reader.candidate(identity),draft);return True,'Verified synchronized captions available.'
            except ValueError as exc:return False,'Captions unavailable: '+str(exc)
        self.view['captions']=await self.service.read_async(check,canceled=lambda:self.canceled)
        self._present_captions()
    def _present_captions(self):
        available,reason = self.view['captions'] if self.view else (False,'Checking synchronized captions')
        self.caps.setEnabled(available)
        self.burn.setEnabled(available)
        self.external_captions.setEnabled(available)
        self.external_captions.setToolTip(reason)
        self.burn.setToolTip(reason)
        self.captions_reason.setText(reason)
        self.remove_captions_button.setVisible(not available and self.caps.isChecked())

    def remove_captions(self):
        # A retained captioned edit must remain recoverable if its captions are
        # later unavailable, while enabling captions still requires verification.
        self.caps.setChecked(False)
        self.remove_captions_button.hide()

    def create_short(self):
        if self.busy:
            return
        if not self.accept_check.isChecked():
            raise ValueError("Explicitly accept the reviewed excerpt before creating a Short.")
        self.start(self._create_short(), 'Checking source')

    async def _create_short(self):
        await self._save()
        if not await self.service.read_async('accepted',self.choices.currentData(),canceled=lambda:self.canceled):
            await self.service.read_async('decide',self.choices.currentData(),True,canceled=lambda:self.canceled)
        await self.render(False)

    @property
    def busy(self):return getattr(self,'_excerpt_busy',False) or bool(getattr(self,'director',None) and self.director.busy)
    @busy.setter
    def busy(self,value):self._excerpt_busy=value
    def change_production_mode(self,index):
        if self.busy:
            self.production_mode.blockSignals(True);self.production_mode.setCurrentIndex(0 if self._excerpt_busy else 1);self.production_mode.blockSignals(False);return
        self.playback.stop();self.director.playback.stop();self.original_scroll.setVisible(index==0);self.director.setVisible(index==1)
        self.return_button.setVisible(index==1)
    def create_directed(self):
        self.production_mode.setCurrentIndex(1);self.director.create()

    def error(self,exc):
        self.status_error=True; self.status.setText(exc if isinstance(exc,str) else ('Canceled: ' if isinstance(exc,RenderCanceled) else 'Failed: ')+media_error_text(exc))
        self.update_workflow()
    def run(self,callback,*,explicit=True):
        if explicit and not self.busy and not self.playback.error_message:self.status_error=False
        try:return callback()
        except Exception as exc:self.error(exc)
    def changed(self,*_):
        if not self.loading and self.value and self.choices.currentData():
            self.dirty=True
            self.preview.clear(); self.preview.setMinimumSize(0,0)
            self.caption_preview.setText("Settings changed; refresh the vertical preview.")
            self.playback.clear()
            self.verified_output=None
            self.update_workflow()
            if not self.status_error and not self.busy:self.status.setText("Unsaved Short edits; Create Short will save them before rendering.")
    def bind(self,service):
        if self.busy:raise ValueError("Cancel the running Shorts operation and wait for cleanup before switching projects.")
        self.director.close_service(); self.playback.clear(); self.playback.dismiss_error(); self.service=service; self.revision=self.value=None; self.body.setEnabled(False)
        self.drafts.clear(); self.choices.blockSignals(True);self.choices.clear();self.choices.blockSignals(False); self.source.clear(); self.verified_output=None
        self.card_values=[];self.view=None
        self.dirty=self.status_error=False
        self.status.setText("Open Shorts to verify the final source and restore retained edits.")
        self.info.setText("No verified source. Open Shorts to verify the completed documentary.")
        self.provider_info.clear()
        self.update_workflow()
    def activate(self):
        if not self.service or self.busy:return
        self.start(self._activate(), 'Checking source')

    async def _activate(self):
        self.remember_draft()
        previous_source=self.source.currentData() or 'original'
        self.playback.stop()
        def sources(reader):
            values=[]
            for kind,manifest,evidence in reader.publishing.sources():
                try:reader.source(kind)
                except ValueError:continue
                values.append((kind,manifest,evidence))
            return values
        verified_sources=await self.service.read_async(sources,canceled=lambda:self.canceled)
        self.source.blockSignals(True);self.source.clear();self.source_evidence={}
        for kind,manifest,evidence in verified_sources:
            self.source_evidence[kind]=(manifest,evidence); self.source.addItem(kind,kind)
        index=self.source.findData(previous_source)
        if index>=0:self.source.setCurrentIndex(index)
        self.source.blockSignals(False)
        self.update_workflow()
        if not self.source.count():
            self.info.setText("No verified source. Complete and verify the documentary first.")
            self.status.setText(self.info.text())
            await self.service.read_async('source',canceled=lambda:self.canceled); return
        self.source_info()
        await self._refresh()
    def source_info(self,*_):
        value=getattr(self,'source_evidence',{}).get(self.source.currentData())
        if not value:return
        source,e=value
        self.info.setText(f"{source.name} · {float(Fraction(*e['video_duration'])):g}s · Verified source ({self.source.currentData()})")
        self.info.setToolTip(f"VALID {self.source.currentData()}: {source.artifact_id}")
        llm=self.service.publishing.llm
        if llm:
            identity=llm.generation_identity() if hasattr(llm,'generation_identity') else {}
            model=identity.get('model','') if isinstance(identity,dict) else ''
            self.provider_info.setText(f"Configured analysis: {type(llm).__name__} {model}. Find Shorts may make a paid LLM call; no automatic retries.")
        else:self.provider_info.setText("Local heuristic analysis · No paid API call.")
    def refresh(self,*,first=False):
        self.start(self._refresh(first=first),'Checking source',preserve_error=True)

    async def _refresh(self,*,first=False):
        self.remember_draft()
        def cards(reader):
            values=[]
            for p in reader.candidates():
                state='Accepted' if reader.accepted(p['id']) else 'Review needed'
                try:
                    reader.candidate(p['id'])
                    edit=reader.edit(p['id'])[1]
                    if reader.artifacts.latest('selection',p['id']):state='Retained output | '+state
                except ValueError:state='Stale source';edit=p
                values.append((p,edit,state))
            return values
        self.card_values=await self.service.read_async(cards,canceled=lambda:self.canceled)
        previous=None if first else self.choices.currentData(); self.choices.blockSignals(True)
        try:
            self.choices.clear(); first_current=None
            for p,_,state in self.card_values:
                if state!='Stale source' and first_current is None:first_current=self.choices.count()
                self.choices.addItem(f"{p['title']} [{'STALE' if state=='Stale source' else state}]",p['id'])
            index=self.choices.findData(previous)
            if index>=0:self.choices.setCurrentIndex(index)
            elif first_current is not None:self.choices.setCurrentIndex(first_current)
        finally:self.choices.blockSignals(False)
        await self._load()
        self.rebuild_cards()
        if not self.choices.count() and not self.status_error:
            self.status.setText("No retained candidates. Click Find 3 Shorts to find verified excerpts.")
    async def analyze(self):
        phase="Checking source"
        def progress(label):
            nonlocal phase
            phase=label; self.status.setText(label+"…")
        try:
            m=await self.service.analyze(self.source.currentData(),self.count.value(),self.target.value(),
                canceled=lambda:self.canceled,progress=progress)
            value=self.service.artifacts.read(m)
            if not value['candidates']:raise ValueError("No candidates returned; review source chapter coverage.")
            await self._refresh(first=True)
            self.status.setText(f"Completed: {len(value['candidates'])} candidates. {value['origin']}")
        except Exception as exc:
            raise ValueError(f"Shorts analysis failed during {phase}: {str(exc) or type(exc).__name__}. Previous candidates retained.") from exc
    def load(self):
        if not self.loading:
            if self.busy:
                if getattr(self,'navigating',False):self.pending_card=(self.choices.currentData(),False);self.canceled=True
            else:self.start(self._load(),'Checking source',preserve_error=True,navigating=True)

    async def _load(self,*,play=False):
        self.remember_draft()
        self.playback.clear(); identity=self.choices.currentData()
        self.verified_output=None
        if not identity:
            self.revision=self.value=None; self.dirty=False
            self.loading=True
            try:
                self.details.clear(); self.title.clear(); self.begin.clear(); self.end.clear(); self.shot.clear()
            finally:self.loading=False
            self.update_workflow()
            return
        self.revision=self.value=None
        self.dirty=False
        self.update_workflow()
        restored=identity in self.drafts
        draft=deepcopy(self.drafts[identity][1]) if restored else None
        def view(reader):
            p=reader.candidate(identity)
            revision,value=reader.edit(identity)
            timeline=reader.source(p['source_kind'])[2]
            output_error=None
            try:output=reader.current_output(identity) if not restored else None
            except (ValueError,OSError,KeyError,zipfile.BadZipFile) as exc:
                output=None
                if not isinstance(exc,ValueError) or not str(exc).startswith(('Save the Short edit','MISSING/STALE:')):
                    output_error='Saved output verification failed: '+str(exc)+'. Render again; previous files are retained.'
            try:
                proposed=deepcopy(draft or value);proposed['style']=ShortStyle(captions=True).to_payload()
                reader.prepare(p,proposed)
                captions=True,'Verified synchronized captions available.'
            except ValueError as exc:captions=False,'Captions unavailable: '+str(exc)
            return dict(p=p,revision=revision,value=value,accepted=reader.accepted(identity),timeline=timeline,
                        edges=reader.boundaries(timeline),captions=captions,output=output,output_error=output_error)
        self.view=await self.service.read_async(view,canceled=lambda:self.canceled)
        p=self.view['p']
        if restored:
            self.revision,self.value,accepted,hook_enabled=deepcopy(self.drafts[identity])
        else:
            self.revision,self.value=self.view['revision'],self.view['value']
            accepted=self.view['accepted']
            hook_enabled=bool(self.value['style']['hook'])
        self.loading=True
        try:
            provenance={'llm':'AI-selected; model assessment retained',
                'verified_fallback':'Verified fallback; heuristic assessment, not AI-ranked',
                'heuristic':'Heuristic selection; not AI-ranked'}.get(
                    p.get('selection_provenance',{}).get('method'),'Retained selection; provenance not recorded')
            self.details.setText(f"{float(Fraction(*p['start'])):.3f} to {float(Fraction(*p['end'])):.3f}s; {float(Fraction(*p['duration'])):.3f}s\nSource: {p['source_kind']} ({p['source_id']})\n{p['topic']}\n{p['reason']}\n{p['hook_assessment']}\nEditorial/audio review required.")
            self.details.setText(self.details.text()+f"\nSelection: {provenance}")
            self.title.setText(self.value['title']); timeline=self.view['timeline']; self.timeline=timeline
            for box,key in [(self.begin,'original_start'),(self.end,'original_end')]:
                box.clear()
                for edge in self.view['edges']:box.addItem(f"{float(edge):.3f}s",ratio(edge))
                box.setCurrentIndex(box.findData(self.value[key]))
            # A draft may contain temporarily invalid text (for example half an RGB value).
            # Restore it verbatim; service validation remains authoritative on save/preview.
            s=SimpleNamespace(**self.value['style']); self.points=dict(s.focal_points)
            self.mode.setCurrentText(s.mode); self.caps.setChecked(s.captions); self.burn.setChecked(s.burn)
            self.font.setValue(s.font_size); self.margin.setValue(s.margin); self.accent.setText(s.accent)
            self.hook.setText(s.hook); self.hook_on.setChecked(hook_enabled); self.hook_seconds.setValue(s.hook_seconds)
            self.shot.clear()
            for c in timeline.clips:
                if c.audio_offset<Fraction(*self.value['original_end']) and c.audio_offset+c.duration>Fraction(*self.value['original_start']):
                    self.shot.addItem(c.media.scene_id,c.media.scene_id)
            self.shot_changed(); self.preview.clear(); self.preview.setMinimumSize(0,0); self.caption_preview.clear(); self.dirty=restored
            self.accept_check.setChecked(accepted)
            if not self.status_error:self.status.setText("Preview the excerpt, confirm acceptance, then Create Short." if not restored else
                "Unsaved edits restored. Create Short will save them before rendering.")
        finally:self.loading=False
        self._present_captions()
        self._present_output()
        if self.view['output_error']:self.error(ValueError(self.view['output_error']))
        if self.verified_output:
            self.card_values=[(candidate,edit,state.replace('Retained output | ','Rendered | '))
                if candidate['id']==identity else (candidate,edit,state) for candidate,edit,state in self.card_values]
            state=next((state for candidate,_,state in self.card_values if candidate['id']==identity),'Rendered')
            for card in self.cards:
                if card.identity==identity:card.summary.setText(f"{float(Fraction(*p['duration'])):g}s | {p['topic']} | {state}")
        if not self.verified_output:
            try:await self._cue_source()
            except ValueError:pass  # An unfinished draft remains editable without stale media.
        if play:await self._play_selected()
    def range_changed(self,*_):
        if self.loading or not self.value or self.begin.currentData() is None or self.end.currentData() is None:return
        a,b=Fraction(*self.begin.currentData()),Fraction(*self.end.currentData())
        if a>=b:return
        included=[c.media.scene_id for c in self.timeline.clips if c.audio_offset<b and c.audio_offset+c.duration>a]
        self.points={k:v for k,v in self.points.items() if k in included}
        self.shot.clear()
        for identity in included:self.shot.addItem(identity,identity)
        self.accept_check.setChecked(False)
        self._present_captions()
    def shot_changed(self,*_):
        self.focal.blockSignals(True); self.focal.setValue(self.points.get(self.shot.currentData(),.5) if hasattr(self,'points') else .5); self.focal.blockSignals(False)
    def crop_changed(self,*_):
        if self.loading:return
        if self.shot.currentData():self.points[self.shot.currentData()]=self.focal.value(); self.changed()
    def draft(self):
        if not self.value:raise ValueError("Select an analyzed candidate.")
        style=dict(mode=self.mode.currentText(),focal_points=[list(x) for x in sorted(self.points.items())],captions=self.caps.isChecked(),
            burn=self.burn.isChecked(),font_size=self.font.value(),margin=self.margin.value(),accent=self.accent.text(),
            hook=self.hook.text() if self.hook_on.isChecked() else '',hook_seconds=self.hook_seconds.value())
        return {**self.value,'title':self.title.text(),'original_start':self.begin.currentData(),'original_end':self.end.currentData(),'style':style}
    def save(self):
        self.start(self._save(),'Checking source')
    async def _save(self):
        self.revision=await self.service.read_async('save_edit',self.draft(),self.revision.artifact_id if self.revision else None,canceled=lambda:self.canceled)
        self.value=self.service.artifacts.read(self.revision); self.dirty=False; self.status.setText("Short edit retained; parent Timeline unchanged.")
        self.drafts.pop(self.value['candidate_id'],None)
        self.verified_output=None
    def suggested_hook(self):
        p=self.view['p']
        if not p['headline']:raise ValueError("No suggested hook; enter a supported headline manually.")
        if len(p['headline'])>100:raise ValueError("Suggested headline needs shortening to 100 characters.")
        self.hook.setText(p['headline']);self.hook_on.setChecked(True)
    def decision(self,accepted):
        self.start(self._decision(accepted),'Checking source')
    async def _decision(self,accepted):
        await self.service.read_async('decide',self.choices.currentData(),accepted,canceled=lambda:self.canceled)
        self.status.setText("Excerpt accepted." if accepted else "Excerpt rejected; history retained.")
        self.accept_check.setChecked(accepted)
        await self._refresh()
    async def vertical_preview(self):
        identity=self.choices.currentData(); edit=self.draft()
        prepared=await self.service.read_async(lambda r:r.prepare(r.candidate(identity),edit),canceled=lambda:self.canceled)
        self.caption_preview.setText('\n'.join(s.text for s in prepared[7]) or "Captions disabled. Enable only with verified measured alignment.")
        raw=await self.service.preview(identity,edit,Fraction(str(self.preview_time.value())),canceled=lambda:self.canceled,progress=lambda label:self.status.setText(label))
        pixmap=QPixmap(); pixmap.loadFromData(raw); self.preview.setPixmap(pixmap.scaled(270,480,Qt.KeepAspectRatio,Qt.SmoothTransformation)); self.preview.setMinimumSize(270,480)
        self.status.setText("Portrait crop / captions / hook still preview. Original excerpt playback uses source framing.")
    def cue_source(self, *, autoplay=False, retry=False):
        self.start(self._cue_source(autoplay=autoplay,retry=retry),'Checking source')
    async def _cue_source(self, *, autoplay=False, retry=False):
        identity=self.choices.currentData();edit=self.draft()
        values=await self.service.read_async(lambda r:r.prepare(r.candidate(identity),edit),canceled=lambda:self.canceled)
        source,_,_,a,b,*_=values
        self.playback.load(self.service.store._artifact_path(source.storage_key),
            DisplayedMedia.ORIGINAL_EXCERPT, self.playback_context(), start=int(a*1000), end=int(b*1000),
            autoplay=autoplay, retry=retry)
        self.update_workflow()
    def play_source(self):
        self.status_error=False
        self.status.setText("Original excerpt selected · source framing.")
        self.cue_source(autoplay=True, retry=True)
    def output(self):
        if self.dirty:raise ValueError("Save the edited revision first.")
        return self.service.current_output(self.choices.currentData())
    def play_short(self):
        self.start(self._play_short(),'Checking source')
    async def _play_short(self):
        self._play_verified(await self.service.read_async('current_output',self.choices.currentData(),canceled=lambda:self.canceled))
    def _play_verified(self,m):
        self.status_error=False
        self.status.setText("Verified portrait Short selected for playback.")
        self.playback.load(self.service.store._artifact_path(m.storage_key), DisplayedMedia.VERIFIED_SHORT,
            self.playback_context(), autoplay=True, retry=True)
        self.update_workflow()
    async def render(self,batch):
        if self.dirty:raise ValueError("Save the edited revision first.")
        kwargs={'canceled':lambda:self.canceled,'progress':lambda label,*_:self.status.setText(label)}
        result=await self.service.render_accepted(**kwargs) if batch else await self.service.render(self.choices.currentData(),**kwargs)
        self.status.setText(self.playback.error_message or ("READY: validated Shorts retained." if result else "No accepted Shorts."))
        await self._refresh()
    def copy(self,kind):
        self.start(self._copy(kind),'Checking source')
    async def _copy(self,kind):
        if self.dirty:raise ValueError('Save the edited revision first.')
        video=await self.service.read_async('current_output',self.choices.currentData(),canceled=lambda:self.canceled)
        selection=self.service.artifacts.latest('selection',self.choices.currentData()); value=self.service.artifacts.read(selection)
        m=video if kind=='video' else self.service.artifacts.one(value[kind+'_id'],kind)
        suffix={'video':'.mp4','srt':'.srt','metadata':'.json','package':'.zip'}[kind]
        async def verify():
            if self.dirty:raise ValueError('Save the edited revision first.')
            current=await self.service.read_async('current_output',self.choices.currentData(),canceled=lambda:self.canceled)
            if current.artifact_id!=video.artifact_id:raise ValueError("Short changed before copy.")
            return m
        return await copy_artifact_async(self,self.service.store,m,title='Save Short '+kind,suggested='short'+suffix,revalidate=verify,
            suffix=suffix,file_filter=f"Short {kind} (*{suffix})",canceled=lambda:self.canceled,progress=lambda label:self.status.setText(label))
    def start(self,coroutine,status="Working",*,preserve_error=False,navigating=False):
        if self.busy:coroutine.close();return
        self.status_error=bool(self.playback.error_message) or (preserve_error and self.status_error)
        if not (preserve_error and self.status_error):self.status.setText(self.playback.error_message or status+"…")
        self.playback.stop(); self.busy=True; self.canceled=False; self.body.setEnabled(False); self.cancel_button.setEnabled(True)
        self.navigating=navigating
        self.loop=asyncio.new_event_loop();self.task=self.loop.create_task(coroutine);self.timer.start()
        self.update_workflow()
    def cancel(self):
        if self.director.busy:self.director.cancel();return
        self.canceled=True;self.status.setText("Cancellation requested; completed Shorts retained.")
    def tick(self):
        self.loop.call_soon(self.loop.stop);self.loop.run_forever()
        if not self.task.done():return
        self.timer.stop()
        try:self.task.result()
        except Exception as exc:self.error(exc)
        finally:
            self.loop.close();self.loop=self.task=None;self.busy=False;self.body.setEnabled(bool(self.source.count()));self.cancel_button.setEnabled(False)
            self.update_workflow()
            pending=getattr(self,'pending_card',None)
            if pending:
                self.pending_card=None
                self.select_card(*pending)

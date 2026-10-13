"""Small local channel manager; intake/render tasks run on an asynchronous owner loop."""
import asyncio
from pathlib import Path
from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog,QComboBox,QLineEdit,QFormLayout,QPushButton,QFileDialog,QLabel,QHBoxLayout,QSpinBox,QVBoxLayout,QScrollArea,QWidget)
from app.application.channel_library import ChannelLibraryService


class ChannelLibraryDialog(QDialog):
    changed=Signal()
    def __init__(self,library,renderer,parent=None):
        super().__init__(parent)
        self.setWindowTitle("Channel Library")
        self.library,self.creator=library,ChannelLibraryService(library,renderer)
        self.profile=None;self.busy=False;self.canceled=False;self.loop=self.task=None
        self.resize(700,640)
        outer=QVBoxLayout(self);scroll=QScrollArea();scroll.setWidgetResizable(True);content=QWidget();scroll.setWidget(content);outer.addWidget(scroll)
        form=QFormLayout(content)
        self.channels=QComboBox();self.channels.currentIndexChanged.connect(self.select)
        form.addRow("Channel",self.channels)
        self.name=QLineEdit();self.tagline=QLineEdit();self.cta=QLineEdit();self.link=QLineEdit()
        self.background=QLineEdit("#10141C");self.accent=QLineEdit("#7CA6BB")
        for label,widget in (("Name",self.name),("Tagline",self.tagline),("Subscription reminder",self.cta),
            ("Website/social URL (optional)",self.link),("Background #RRGGBB",self.background),("Accent #RRGGBB",self.accent)):
            form.addRow(label,widget)
        actions=QHBoxLayout()
        for text,callback in (("Create channel",self.create),("Save metadata",self.save),("Set default channel",self.set_default)):
            button=QPushButton(text);button.clicked.connect(callback);actions.addWidget(button)
        form.addRow(actions)
        self.versions={}
        for kind,label in (("logo","PNG logo"),("intro","Intro MP4"),("outro","Outro MP4"),("sound","Music/sound (optional)")):
            row=QHBoxLayout();combo=QComboBox();self.versions[kind]=combo;row.addWidget(combo)
            button=QPushButton("Import");button.clicked.connect(lambda _,k=kind:self.import_asset(k));row.addWidget(button)
            if kind in {"intro","outro"}:
                button=QPushButton("Preview");button.clicked.connect(lambda _,k=kind:self.preview(k));row.addWidget(button)
            form.addRow(label,row)
        self.intro_seconds=QSpinBox();self.intro_seconds.setRange(2,4);self.intro_seconds.setValue(3)
        self.outro_seconds=QSpinBox();self.outro_seconds.setRange(10,20);self.outro_seconds.setValue(15)
        form.addRow("Created intro seconds",self.intro_seconds);form.addRow("Created outro seconds",self.outro_seconds)
        actions=QHBoxLayout()
        for text,callback in (("Create intro once",lambda:self.create_clip("intro")),("Create outro once",lambda:self.create_clip("outro")),("Save selected versions",self.save_versions)):
            button=QPushButton(text);button.clicked.connect(callback);actions.addWidget(button)
        form.addRow(actions)
        self.placement=QComboBox();self.placement.addItem("After cold open (Standard)","after_cold_open");self.placement.addItem("Before documentary","before")
        from PySide6.QtWidgets import QCheckBox
        self.intro=QCheckBox("Default intro enabled");self.outro=QCheckBox("Default outro enabled")
        form.addRow("Default placement",self.placement);form.addRow(self.intro);form.addRow(self.outro)
        self.cancel_button=QPushButton("Cancel operation");self.cancel_button.clicked.connect(self.cancel);form.addRow(self.cancel_button)
        self.status=QLabel("Shared assets stay in application user data. Apply profile changes explicitly in each project.")
        self.status.setWordWrap(True);form.addRow(self.status)
        note=QLabel("Recommendation boxes are visual only. Configure clickable end screens in YouTube Studio. Moving a project requires its channel library/assets.")
        note.setWordWrap(True);form.addRow(note)
        self.timer=QTimer(self);self.timer.setInterval(10);self.timer.timeout.connect(self.tick)
        self.reload()

    def reload(self):
        selected=self.profile.channel_id if self.profile else self.channels.currentData()
        self.channels.blockSignals(True);self.channels.clear()
        for p in self.library.profiles():self.channels.addItem(p.channel_name,p.channel_id)
        index=self.channels.findData(selected)
        self.channels.setCurrentIndex(max(0,index));self.channels.blockSignals(False)
        self.select()

    def select(self,*_):
        try:
            channel=self.channels.currentData()
            if not channel:self.profile=None;return
            self.profile=self.library.profile(channel)
            for name in ("channel_name","tagline","cta","link","background","accent"):
                getattr(self,"name" if name=="channel_name" else name).setText(getattr(self.profile,name))
            self.placement.setCurrentIndex(self.placement.findData(self.profile.placement))
            self.intro.setChecked(self.profile.intro);self.outro.setChecked(self.profile.outro)
            for kind,combo in self.versions.items():
                combo.clear();combo.addItem("None",None)
                for number,asset in enumerate(self.library.versions(channel,kind),1):
                    duration=asset["media"].get("video_duration",asset["media"].get("duration"))
                    suffix=f" - {duration[0]/duration[1]:.3f}s" if duration else ""
                    combo.addItem(f"v{number} - "+asset["version_id"][-12:]+suffix,asset["version_id"])
                selected=getattr(self.profile,kind+"_version",None)
                combo.setCurrentIndex(max(0,combo.findData(selected)))
        except Exception as exc:self.status.setText(str(exc))

    def create(self):
        try:self.profile=self.library.create(self.name.text());self.reload();self.changed.emit()
        except Exception as exc:self.status.setText(str(exc))
    def save(self):
        try:
            if self.profile is None:raise ValueError("Create/select a channel first.")
            self.profile=self.library.edit(self.profile,channel_name=self.name.text(),tagline=self.tagline.text(),cta=self.cta.text(),
                link=self.link.text(),background=self.background.text(),accent=self.accent.text(),placement=self.placement.currentData(),
                intro=self.intro.isChecked(),outro=self.outro.isChecked())
            self.reload();self.changed.emit();self.status.setText("New immutable profile revision saved. Existing projects keep their pinned selections.")
        except Exception as exc:self.status.setText(str(exc))
    def save_versions(self):
        try:
            self.profile=self.library.edit(self.profile,**{k+"_version":self.versions[k].currentData() for k in ("logo","intro","outro")})
            self.reload();self.changed.emit()
        except Exception as exc:self.status.setText(str(exc))
    def set_default(self):
        try:self.library.set_default(self.profile.channel_id);self.changed.emit();self.status.setText("Default channel saved; existing project selections were preserved.")
        except Exception as exc:self.status.setText(str(exc))
    def import_asset(self,kind):
        if self.profile is None or self.busy:return
        filters="PNG (*.png)" if kind=="logo" else "MP4 (*.mp4)" if kind in {"intro","outro"} else "Audio (*.wav *.mp3 *.m4a *.ogg *.flac)"
        path,_=QFileDialog.getOpenFileName(self,"Import shared "+kind,"",filters)
        if path:self.start(self.creator.import_asset(self.profile.channel_id,kind,Path(path),canceled=lambda:self.canceled),kind)
    def create_clip(self,kind):
        if self.profile is None or self.busy:return
        # Metadata/version edits are explicit. The immutable selected logo is the creator input.
        duration=(self.intro_seconds if kind=="intro" else self.outro_seconds).value()
        self.start(self.creator.create_clip(self.profile,kind,duration=duration,sound_version=self.versions["sound"].currentData(),canceled=lambda:self.canceled),kind)
    def preview(self,kind):
        try:
            version=self.versions[kind].currentData()
            if version is None:raise ValueError("Select a shared "+kind+" version before previewing.")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.path(version))))
        except Exception as exc:self.status.setText(str(exc))
    def start(self,operation,kind):
        self.pending_kind=kind;self.pending_channel=self.profile.channel_id;self.pending_profile=self.profile;self.busy=True;self.canceled=False
        self.loop=asyncio.new_event_loop();self.task=self.loop.create_task(operation);self.timer.start()
        for child in self.findChildren(QPushButton):child.setEnabled(child is self.cancel_button)
        self.channels.setEnabled(False);self.status.setText("Validating/rendering shared asset locally...")
    def cancel(self):self.canceled=True
    def tick(self):
        self.loop.call_soon(self.loop.stop);self.loop.run_forever()
        if not self.task.done():return
        self.timer.stop()
        try:
            asset=self.task.result()
            current=self.library.profile(self.pending_channel)
            if current.revision_id!=self.pending_profile.revision_id:
                raise ValueError("Channel changed during the operation. The asset is retained; select its version deliberately after refreshing.")
            if self.pending_kind in {"logo","intro","outro"}:
                self.profile=self.library.edit(current,**{self.pending_kind+"_version":asset["version_id"]})
            self.reload();self.changed.emit();self.status.setText("Immutable shared asset retained. Projects update only when explicitly applied.")
        except Exception as exc:
            try:self.reload()
            except Exception:pass
            self.status.setText(str(exc))
        finally:
            self.loop.close();self.loop=self.task=None;self.busy=False
            for child in self.findChildren(QPushButton):child.setEnabled(True)
            self.channels.setEnabled(True)
    def closeEvent(self,event):
        if self.busy:self.cancel();event.ignore()
        else:event.accept()

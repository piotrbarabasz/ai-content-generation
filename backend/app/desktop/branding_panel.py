"""Small optional Export subsection; FFmpeg stays asynchronous on the owner loop."""

import asyncio
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from app.domain.branding import BrandingConfig
from app.domain.channel_library import ReusableBrandingConfig
from dataclasses import replace


class BrandingPanel(QWidget):
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = self.output = None
        self.logo_id = None
        self.reusable = None
        self.library_dialog = None
        self.busy = self.canceled = self.loading = False
        self.loop = self.task = None
        root = QVBoxLayout(self)
        self.group = QGroupBox("Branding / Finishing")
        self.group.setCheckable(True)
        self.group.setChecked(False)
        root.addWidget(self.group)
        self.body = QWidget()
        group_layout = QVBoxLayout(self.group)
        group_layout.addWidget(self.body)
        self.group.toggled.connect(self.body.setVisible)
        self.body.setVisible(False)
        form = QFormLayout(self.body)
        self.mode=QComboBox();self.mode.addItem("Legacy generated cards","legacy");self.mode.addItem("Reusable channel clips","reusable")
        self.mode.currentIndexChanged.connect(self._mode_changed);form.addRow("Finishing mode",self.mode)
        self.channel=QComboBox();self.intro_version=QComboBox();self.outro_version=QComboBox()
        self.manage_button=QPushButton("Channel Library...");self.manage_button.clicked.connect(self.manage_library)
        self.apply_profile_button=QPushButton("Apply selected channel defaults");self.apply_profile_button.clicked.connect(self.apply_profile)
        form.addRow("Shared channel",self.channel);form.addRow(self.manage_button,self.apply_profile_button)
        form.addRow("Project intro version",self.intro_version);form.addRow("Project outro version",self.outro_version)
        self.intro_version.currentIndexChanged.connect(self.save);self.outro_version.currentIndexChanged.connect(self.save)
        self.fit=QComboBox();self.fit.addItem("Contain / letterbox (no crop or stretch)","contain");self.fit.addItem("Reject different aspect ratios","reject")
        self.fit.currentIndexChanged.connect(self.save);form.addRow("Clip fit policy",self.fit)
        self.channel_name, self.tagline, self.cta = QLineEdit(), QLineEdit(), QLineEdit("Subscribe for more")
        self.background, self.accent = QLineEdit("#10141C"), QLineEdit("#7CA6BB")
        for label, field in (("Channel name", self.channel_name), ("Tagline (optional)", self.tagline),
                             ("CTA text", self.cta), ("Background color", self.background), ("Accent color", self.accent)):
            form.addRow(label, field)
            field.editingFinished.connect(self.save)
        self.intro, self.outro, self.placeholders = QCheckBox("Enable intro"), QCheckBox("Enable outro"), QCheckBox("Two recommendation placeholders")
        self.placeholders.setChecked(True)
        self.placement = QComboBox()
        self.placement.addItem("Before video", "before")
        self.placement.addItem("After cold open", "after_cold_open")
        self.intro_seconds, self.outro_seconds = QSpinBox(), QSpinBox()
        self.intro_seconds.setRange(2, 4); self.intro_seconds.setValue(3)
        self.outro_seconds.setRange(10, 20); self.outro_seconds.setValue(15)
        for widget in (self.intro, self.outro, self.placeholders):
            form.addRow(widget)
            widget.toggled.connect(self.save)
        form.addRow("Intro placement", self.placement)
        form.addRow("Intro duration (seconds)", self.intro_seconds)
        form.addRow("Outro duration (seconds)", self.outro_seconds)
        self.placement.currentIndexChanged.connect(self.save)
        self.intro_seconds.valueChanged.connect(self.save)
        self.outro_seconds.valueChanged.connect(self.save)
        self.logo_button = QPushButton("Import PNG logo")
        self.logo_label = QLabel("No logo selected")
        self.logo_button.clicked.connect(self.import_logo)
        form.addRow(self.logo_button, self.logo_label)
        self.mapping = QLabel(""); self.mapping.setWordWrap(True)
        self.details = QLabel(""); self.details.setWordWrap(True)
        self.status = QLabel("Open a project to configure finishing."); self.status.setWordWrap(True)
        for label in (self.mapping, self.details, self.status):
            form.addRow(label)
        form.addRow(QLabel("Placeholders are visual only; configure clickable end screens in YouTube Studio."))
        actions = QHBoxLayout()
        self.preview_intro=QPushButton("Preview intro");self.preview_outro=QPushButton("Preview outro")
        self.preview_intro.clicked.connect(lambda:self.preview_clip("intro"));self.preview_outro.clicked.connect(lambda:self.preview_clip("outro"))
        form.addRow(self.preview_intro,self.preview_outro)
        self.build_button = QPushButton("Build / Rebuild branded video")
        self.cancel_button = QPushButton("Cancel finishing")
        self.play_button = QPushButton("Play branded video")
        self.copy_button = QPushButton("Save branded copy as…")
        for button, callback in ((self.build_button, self.build), (self.cancel_button, self.cancel),
                                 (self.play_button, self.play), (self.copy_button, self.save_copy)):
            if button is not self.cancel_button:
                actions.addWidget(button)
            button.clicked.connect(callback)
        form.addRow(actions)
        root.addWidget(self.cancel_button)
        self.timer = QTimer(self); self.timer.setInterval(10); self.timer.timeout.connect(self.tick)
        self.refresh()
        self._mode_visibility()

    def config(self):
        if self.mode.currentData()=="reusable":
            if self.reusable is None:raise ValueError("Select a channel and explicitly apply its defaults first.")
            library=self.service.channel_library
            def ref(combo,pinned):
                version=combo.currentData()
                if not version:return None
                if pinned and pinned["version_id"]==version:return pinned
                asset=library.asset(version)
                return {"version_id":version,"checksum":asset["checksum"],"duration":asset["media"]["video_duration"]}
            return replace(self.reusable,intro=self.intro.isChecked(),outro=self.outro.isChecked(),placement=self.placement.currentData(),
                intro_asset=ref(self.intro_version,self.reusable.intro_asset),outro_asset=ref(self.outro_version,self.reusable.outro_asset),fit=self.fit.currentData())
        return BrandingConfig(channel_name=self.channel_name.text(), tagline=self.tagline.text(), cta=self.cta.text(),
            background=self.background.text(), accent=self.accent.text(), logo_id=self.logo_id,
            intro=self.intro.isChecked(), intro_seconds=self.intro_seconds.value(), placement=self.placement.currentData(),
            outro=self.outro.isChecked(), outro_seconds=self.outro_seconds.value(), placeholders=self.placeholders.isChecked())

    def bind(self, service):
        self.service, self.output = service, None
        if self.library_dialog is not None and not self.library_dialog.busy:
            self.library_dialog.close();self.library_dialog=None
        self.loading = True
        try:
            config = service.branding.current() if service else BrandingConfig()
            self.reusable=config if isinstance(config,ReusableBrandingConfig) else None
            self.mode.setCurrentIndex(self.mode.findData("reusable" if self.reusable else "legacy"))
            if self.reusable:
                try:
                    self._load_channels(config.channel_id)
                    self._load_versions(config)
                except (ValueError,OSError):
                    # A missing shared library must not disable explicit legacy fallback or Disable.
                    self.channel.clear();self.channel.addItem(config.channel_name,config.channel_id)
                    for combo,ref in ((self.intro_version,config.intro_asset),(self.outro_version,config.outro_asset)):
                        combo.clear();combo.addItem("None",None)
                        if ref:combo.addItem("Missing pinned version: "+ref["version_id"],ref["version_id"]);combo.setCurrentIndex(1)
                self.intro.setChecked(config.intro);self.outro.setChecked(config.outro)
                self.placement.setCurrentIndex(self.placement.findData(config.placement))
                self.loading=False;self._mode_visibility();self.refresh();return
            self.logo_id = config.logo_id
            for name in ("channel_name", "tagline", "cta", "background", "accent"):
                getattr(self, name).setText(getattr(config, name))
            for name in ("intro", "outro", "placeholders"):
                getattr(self, name).setChecked(getattr(config, name))
            self.intro_seconds.setValue(config.intro_seconds); self.outro_seconds.setValue(config.outro_seconds)
            self.placement.setCurrentIndex(self.placement.findData(config.placement))
        except (ValueError, OSError) as exc:
            self.service = None
            self.refresh()
            self.status.setText(f"Retained branding settings failed validation: {exc}")
            return
        finally:
            self.loading = False
        self._mode_visibility()
        self.refresh()

    def save(self, *_):
        if self.loading or self.busy or self.service is None:
            return
        try:
            self.service.branding.save(self.config())
            self.refresh()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def refresh(self):
        self.output = None
        self.build_button.setEnabled(self.service is not None and not self.busy)
        self.cancel_button.setEnabled(self.busy)
        self.play_button.setEnabled(False); self.copy_button.setEnabled(False)
        if self.service is None:
            self.status.setText("Finishing unavailable: open a project with trusted FFmpeg/FFprobe configured.")
            return
        try:
            config = self.config()
            source = self.service.renders.selected()
            if source is None:
                raise ValueError("Build the original final MP4 first.")
            available, message = True, "After cold open uses the verified Standard plan/timeline boundary."
            try:
                self.service.boundary(source)
            except (ValueError, OSError, KeyError) as exc:
                available, message = False, str(exc)
            self.placement.model().item(1).setEnabled(available)
            self.mapping.setText(message)
            seconds = float(Fraction(*source.metadata["render"]["video_duration"]))
            narration = source.metadata["render"].get("audio_duration", source.metadata["render"]["video_duration"])
            intro=config.intro_seconds if config.intro else 0
            outro=config.outro_seconds if config.outro else 0
            extra=config.extra_seconds
            if isinstance(config,ReusableBrandingConfig):
                from app.domain.branding_timing import clip_allocation
                fps=Fraction(str(source.metadata["render"]["fps"]))
                extra=(clip_allocation(intro,fps)[2] if config.intro and intro else 0)+(clip_allocation(outro,fps)[2] if config.outro and outro else 0)
            pinned=(f"Pinned channel: {config.channel_name} - profile {config.profile_revision[-12:]}\n" if isinstance(config,ReusableBrandingConfig) else "")
            self.details.setText(pinned+f"Narrative duration: {float(Fraction(*narration)):.2f}s - Source: {seconds:.2f}s\n"
                f"Selected intro: {float(intro):.3f}s - Outro: {float(outro):.3f}s - "
                f"Estimated branded delivery: {float(seconds + extra):.2f}s")
            self.logo_label.setText("Project PNG logo selected" if self.logo_id else "No logo selected")
            if not config.enabled:
                self.status.setText("Branding disabled. Original final video remains available.")
                return
            _, _, fingerprint = self.service.prepare(config)
            self.output = self.service.branding.cached(fingerprint)
            self.status.setText(("Branded video ready" + ("\n" + "\n".join(self.output.metadata["finishing"].get("audio_warnings",[])) if self.output else "")) if self.output else "Branded output missing or stale; build to apply current settings.")
            self.play_button.setEnabled(self.output is not None); self.copy_button.setEnabled(self.output is not None)
        except (ValueError, OSError, KeyError) as exc:
            self.status.setText(str(exc))

    def _mode_visibility(self):
        reusable=self.mode.currentData()=="reusable"
        for widget in (self.channel,self.intro_version,self.outro_version,self.apply_profile_button,self.fit,self.preview_intro,self.preview_outro):widget.setVisible(reusable)
        for widget in (self.channel_name,self.tagline,self.cta,self.background,self.accent,self.logo_button,self.logo_label,self.intro_seconds,self.outro_seconds,self.placeholders):widget.setVisible(not reusable)

    def _load_channels(self,selected=None):
        library=self.service.channel_library
        selected=selected or self.channel.currentData()
        self.channel.clear()
        for profile in library.profiles():self.channel.addItem(profile.channel_name,profile.channel_id)
        default=library.default()
        index=self.channel.findData(selected or (default.channel_id if default else None))
        self.channel.setCurrentIndex(max(0,index))

    def _load_versions(self,config):
        previous_loading=self.loading
        self.loading=True
        try:
            for kind,combo,ref in (("intro",self.intro_version,config.intro_asset),("outro",self.outro_version,config.outro_asset)):
                combo.clear();combo.addItem("None",None)
                for number,asset in enumerate(self.service.channel_library.versions(config.channel_id,kind),1):
                    d=asset["media"]["video_duration"]
                    combo.addItem(f"v{number} - {asset['version_id'][-12:]} - {d[0]/d[1]:.3f}s",asset["version_id"])
                index=combo.findData(ref["version_id"] if ref else None)
                if ref and index<0:
                    combo.addItem("Missing pinned version: "+ref["version_id"],ref["version_id"]);index=combo.count()-1
                combo.setCurrentIndex(max(0,index))
            self.fit.setCurrentIndex(self.fit.findData(config.fit))
        finally:self.loading=previous_loading

    def _mode_changed(self,*_):
        self._mode_visibility()
        if self.loading or self.service is None:return
        if self.mode.currentData()=="reusable":
            try:self._load_channels(self.reusable.channel_id if self.reusable else None)
            except Exception as exc:self.status.setText(str(exc))
        else:
            import json
            history=self.service.branding.history("branding_settings")
            for record in reversed(history):
                payload=json.loads(self.service.branding.verified_bytes(record))
                if payload.get("version")==1:
                    config=BrandingConfig.from_payload(payload)
                    self.loading=True
                    try:
                        self.logo_id=config.logo_id
                        for name in ("channel_name","tagline","cta","background","accent"):getattr(self,name).setText(getattr(config,name))
                        for name in ("intro","outro","placeholders"):getattr(self,name).setChecked(getattr(config,name))
                        self.intro_seconds.setValue(config.intro_seconds);self.outro_seconds.setValue(config.outro_seconds)
                        self.placement.setCurrentIndex(self.placement.findData(config.placement))
                    finally:self.loading=False
                    break
        self.save()

    def manage_library(self):
        if self.service is None or self.busy:return
        try:
            from app.desktop.channel_library_panel import ChannelLibraryDialog
            if self.library_dialog is None:
                self.library_dialog=ChannelLibraryDialog(self.service.channel_library,self.service.renderer,self)
                self.library_dialog.changed.connect(lambda:self._load_channels(self.reusable.channel_id if self.reusable else None))
            self.library_dialog.show();self.library_dialog.raise_()
        except Exception as exc:self.status.setText(str(exc))

    def apply_profile(self):
        try:
            profile=self.service.channel_library.profile(self.channel.currentData())
            config=self.service.channel_library.selection(profile)
            self.reusable=config
            self.loading=True;self.mode.setCurrentIndex(self.mode.findData("reusable"))
            self.intro.setChecked(config.intro);self.outro.setChecked(config.outro)
            self.placement.setCurrentIndex(self.placement.findData(config.placement))
            self._load_versions(config);self._mode_visibility();self.loading=False;self.save()
        except Exception as exc:self.loading=False;self.status.setText(str(exc))

    def preview_clip(self,kind):
        try:
            config=self.config()
            if not isinstance(config,ReusableBrandingConfig):raise ValueError("Choose reusable clip mode before previewing shared assets.")
            ref=getattr(config,kind+"_asset")
            if ref is None:raise ValueError("Select a shared "+kind+" version before previewing.")
            return QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.service.channel_library.path(ref["version_id"]))))
        except Exception as exc:self.status.setText(str(exc));return False

    def import_logo(self):
        if self.service is None or self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import channel logo", "", "PNG logo (*.png)")
        if path:
            try:
                self.logo_id = self.service.branding.import_logo(Path(path)).artifact_id
                self.save()
            except (ValueError, OSError) as exc:
                self.status.setText(f"Logo import failed: {exc}")

    def build(self):
        if self.service is None or self.busy:
            return
        try:
            config = self.config()
            self.service.prepare(config)
            self.canceled = False; self.busy = True
            self.loop = asyncio.new_event_loop()
            self.task = self.loop.create_task(self.service.build(config, canceled=lambda: self.canceled,
                progress=lambda phase, *_: self.status.setText(f"Finishing: {phase}")))
            self.body.setEnabled(False); self.cancel_button.setEnabled(True)
            self.busy_changed.emit(True); self.timer.start()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def cancel(self):
        self.canceled = True
        self.status.setText("Cancellation requested; the original video is retained.")

    def tick(self):
        self.loop.call_soon(self.loop.stop); self.loop.run_forever()
        if not self.task.done():
            return
        self.timer.stop()
        self.busy = False
        try:
            self.output = self.task.result()
            self.refresh()
        except Exception as exc:
            self.status.setText(f"Finishing stopped: {exc}")
        finally:
            self.loop.close(); self.loop = self.task = None; self.busy = False
            self.body.setEnabled(True); self.cancel_button.setEnabled(False)
            self.busy_changed.emit(False)

    def verified_output(self):
        if self.output is None:
            raise ValueError("No current branded output.")
        expected = self.output
        _, _, fingerprint = self.service.prepare(self.config())
        if fingerprint != expected.metadata.get("fingerprint"):
            raise ValueError("Branded output is stale for current finishing inputs.")
        return self.service.branding.verify_output(expected)

    def play(self):
        try:
            manifest = self.verified_output()
            path = self.service.branding.store._artifact_path(manifest.storage_key)
            return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc)); return False

    def save_copy(self):
        try:
            manifest = self.verified_output()
            from app.desktop.artifact_export import copy_artifact
            path = copy_artifact(self, self.service.branding.store, manifest,
                title="Save branded video copy", suggested="branded.mp4", revalidate=self.verified_output)
            if path is None:
                return None
            self.status.setText(f"Branded video copy saved: {path}")
            return path
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc)); return None

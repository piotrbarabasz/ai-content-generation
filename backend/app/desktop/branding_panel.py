"""Small optional Export subsection; FFmpeg stays asynchronous on the owner loop."""

import asyncio
from fractions import Fraction
from hashlib import sha256
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from app.domain.branding import BrandingConfig


class BrandingPanel(QWidget):
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = self.output = None
        self.logo_id = None
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

    def config(self):
        return BrandingConfig(channel_name=self.channel_name.text(), tagline=self.tagline.text(), cta=self.cta.text(),
            background=self.background.text(), accent=self.accent.text(), logo_id=self.logo_id,
            intro=self.intro.isChecked(), intro_seconds=self.intro_seconds.value(), placement=self.placement.currentData(),
            outro=self.outro.isChecked(), outro_seconds=self.outro_seconds.value(), placeholders=self.placeholders.isChecked())

    def bind(self, service):
        self.service, self.output = service, None
        self.loading = True
        try:
            config = service.branding.current() if service else BrandingConfig()
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
            self.details.setText(f"Narrative duration: {float(Fraction(*narration)):.2f}s · Source: {seconds:.2f}s\n"
                f"Intro: {config.intro_seconds if config.intro else 0}s · Outro: {config.outro_seconds if config.outro else 0}s · "
                f"Estimated branded delivery: {seconds + config.extra_seconds:.2f}s")
            self.logo_label.setText("Project PNG logo selected" if self.logo_id else "No logo selected")
            if not config.enabled:
                self.status.setText("Branding disabled. Original final video remains available.")
                return
            _, _, fingerprint = self.service.prepare(config)
            self.output = self.service.branding.cached(fingerprint)
            self.status.setText("Branded video ready" if self.output else "Branded output missing or stale; build to apply current settings.")
            self.play_button.setEnabled(self.output is not None); self.copy_button.setEnabled(self.output is not None)
        except (ValueError, OSError, KeyError) as exc:
            self.status.setText(str(exc))

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
            destination, _ = QFileDialog.getSaveFileName(self, "Save branded video copy", "branded.mp4", "MP4 video (*.mp4)")
            if not destination:
                return None
            path = Path(destination)
            if path.suffix.lower() != ".mp4":
                path = path.with_suffix(".mp4")
            # Never replace an immutable source or artifact through an export dialog.
            try:
                path.resolve().relative_to(self.service.branding.store.root.resolve())
            except ValueError:
                pass
            else:
                raise ValueError("Save exported copies outside the immutable artifact directory.")
            output = QSaveFile(str(path))
            if not output.open(QIODevice.WriteOnly):
                raise OSError(output.errorString())
            try:
                digest, size = sha256(), 0
                with self.service.branding.store.open_artifact_id(manifest.artifact_id) as stream:
                    while block := stream.read(1024 * 1024):
                        digest.update(block); size += len(block)
                        if output.write(block) != len(block):
                            raise OSError("Short write copying branded video.")
                if digest.hexdigest() != manifest.checksum or size != manifest.size_bytes or not output.commit():
                    raise ValueError("Branded copy could not be verified or committed.")
            except Exception:
                output.cancelWriting(); raise
            self.status.setText(f"Branded video copy saved: {path}")
            return path
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc)); return None

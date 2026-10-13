"""Compact owner-thread delivery controls inside the existing Export tab."""

import asyncio
from fractions import Fraction

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from app.desktop.artifact_export import copy_artifact
from app.domain.delivery import DELIVERY_PRESETS, DeliveryConfig


class DeliveryPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = self.output = self.loop = self.task = None
        self.busy = self.canceled = self.loading = False
        self.target_lra = 11.0
        root = QVBoxLayout(self)
        self.group = QGroupBox("Delivery Quality"); self.group.setCheckable(True); self.group.setChecked(True)
        root.addWidget(self.group)
        self.body = QWidget(); QVBoxLayout(self.group).addWidget(self.body)
        self.group.toggled.connect(self.body.setVisible)
        form = QFormLayout(self.body)
        self.source = QComboBox(); self.source.addItem("Verified original final MP4", "original"); self.source.addItem("Current D068 branded MP4", "branded")
        self.preset = QComboBox()
        for preset in DELIVERY_PRESETS.values():
            self.preset.addItem(preset.label, preset.id)
        self.preset.setCurrentIndex(self.preset.findData("youtube_hq"))
        self.normalize = QCheckBox("Normalize audio"); self.normalize.setChecked(True)
        self.lufs = QDoubleSpinBox(); self.lufs.setRange(-30, -10); self.lufs.setValue(-16)
        self.peak = QDoubleSpinBox(); self.peak.setRange(-9, -1); self.peak.setValue(-2)
        self.crf = QSpinBox(); self.crf.setRange(0, 51); self.crf.setValue(18)
        for label, control in (("Source video", self.source), ("Export preset", self.preset), ("", self.normalize),
                               ("Target LUFS", self.lufs), ("Target true peak (dBTP)", self.peak), ("CRF", self.crf)):
            form.addRow(label, control)
        for widget in (self.source, self.preset): widget.currentIndexChanged.connect(self.save)
        self.normalize.toggled.connect(self.save)
        for widget in (self.lufs, self.peak, self.crf): widget.valueChanged.connect(self.save)
        self.source_details = QLabel(""); self.source_details.setWordWrap(True)
        self.description = QLabel(""); self.description.setWordWrap(True)
        self.report = QLabel(""); self.report.setWordWrap(True)
        self.status = QLabel("Open a project to configure delivery export."); self.status.setWordWrap(True)
        for widget in (self.source_details, self.description, self.report, self.status): form.addRow(widget)
        actions = QHBoxLayout()
        self.build_button = QPushButton("Build / Rebuild delivery MP4")
        self.play_button = QPushButton("Play delivery MP4")
        self.copy_button = QPushButton("Save delivery copy as…")
        for button, callback in ((self.build_button, self.build), (self.play_button, self.play), (self.copy_button, self.save_copy)):
            actions.addWidget(button); button.clicked.connect(callback)
        form.addRow(actions)
        self.cancel_button = QPushButton("Cancel delivery export"); self.cancel_button.clicked.connect(self.cancel)
        root.addWidget(self.cancel_button)
        self.timer = QTimer(self); self.timer.setInterval(10); self.timer.timeout.connect(self.tick)
        self.refresh()

    def config(self):
        return DeliveryConfig(source=self.source.currentData(), preset=self.preset.currentData(), normalize=self.normalize.isChecked(),
                              target_lufs=self.lufs.value(), target_peak=self.peak.value(), target_lra=self.target_lra, crf=self.crf.value())

    def bind(self, service):
        self.service, self.output = service, None
        self.loading = True
        try:
            config = service.artifacts.current() if service else DeliveryConfig()
            self.target_lra = config.target_lra
            self.source.setCurrentIndex(self.source.findData(config.source)); self.preset.setCurrentIndex(self.preset.findData(config.preset))
            self.normalize.setChecked(config.normalize); self.lufs.setValue(config.target_lufs)
            self.peak.setValue(config.target_peak); self.crf.setValue(config.crf)
        except (ValueError, OSError) as exc:
            self.service = None; self.refresh(); self.status.setText(f"Retained delivery settings failed validation: {exc}"); return
        finally:
            self.loading = False
        self.refresh()

    def save(self, *_):
        if self.loading or self.busy or self.service is None:
            return
        try:
            self.service.artifacts.save(self.config()); self.refresh()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def refresh(self):
        self.output = None
        self.build_button.setEnabled(self.service is not None and not self.busy)
        self.cancel_button.setEnabled(self.busy)
        self.play_button.setEnabled(False); self.copy_button.setEnabled(False)
        self.report.clear()
        if self.service is None:
            self.status.setText("Open a project with a verified final render to export delivery media."); return
        config = self.config()
        hq = config.preset == "youtube_hq"
        self.normalize.setEnabled(hq); self.crf.setEnabled(hq)
        self.lufs.setEnabled(hq and config.normalize); self.peak.setEnabled(hq and config.normalize)
        self.description.setText("Byte-identical copy: no encoding or audio normalization." if not hq else
            f"H.264 High · medium / CRF {config.crf} · AAC-LC 48 kHz · "
            + (f"Two-pass mastering: {config.target_lufs:g} LUFS / {config.target_peak:g} dBTP." if config.normalize else "Audio normalization disabled.")
            + " Loudness defaults are editorial choices; final size and bitrate are measured after encoding.")
        try:
            self.service.source("branded"); branded = True
        except (ValueError, OSError, KeyError):
            branded = False
        self.source.model().item(1).setEnabled(branded)
        try:
            source, _, fingerprint = self.service.prepare(config)
            evidence = source.metadata["render" if config.source == "original" else "finishing"]
            self.source_details.setText(f"Selected source: {self.source.currentText()} · {source.artifact_id}\n"
                f"{float(Fraction(*evidence['video_duration'])):.2f}s · {evidence['width']}×{evidence['height']} · "
                f"{evidence['fps']} FPS · H.264/AAC MP4 · {source.size_bytes / 1048576:.2f} MiB")
            self.output = self.service.artifacts.cached(fingerprint)
            self.status.setText("Delivery output missing or stale; build current settings." if self.output is None else
                                "Delivery result: " + self.output.metadata["delivery"]["status"])
            if self.output is not None:
                r = self.service.artifacts.report(self.output)
                after = r.get("after")
                def measurement(value):
                    return f"{value:.2f}" if value is not None else "unavailable"
                self.report.setText(f"Output: {r['output_artifact_id']}\nValidation: {r['status']} · {self.output.size_bytes / 1048576:.2f} MiB\n"
                    f"Video bitrate: {r.get('video_bitrate') or 'not measured'} bit/s · Audio bitrate: {r.get('audio_bitrate') or 'not measured'} bit/s\n"
                    + (f"Actual loudness: {measurement(after['integrated_lufs'])} LUFS · True peak: {measurement(after['true_peak_dbtp'])} dBTP\n"
                       f"Target difference: {measurement(r.get('loudness_difference_lu'))} LU / {measurement(r.get('true_peak_difference_db'))} dB\n" if after else "Loudness not analyzed: source copy.\n")
                    + "\n".join(r.get("reasons", ())))
                self.play_button.setEnabled(True); self.copy_button.setEnabled(True)
        except (ValueError, OSError, KeyError) as exc:
            self.source_details.clear(); self.status.setText(str(exc))

    def build(self):
        if self.busy or self.service is None:
            return
        try:
            config = self.config(); self.service.prepare(config)
            self.canceled = False; self.busy = True
            self.loop = asyncio.new_event_loop()
            self.task = self.loop.create_task(self.service.build(config, canceled=lambda: self.canceled,
                progress=lambda phase, *_: self.status.setText(phase)))
            self.body.setEnabled(False); self.cancel_button.setEnabled(True); self.timer.start()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def cancel(self):
        self.canceled = True; self.status.setText("Cancellation requested; source artifacts are retained.")

    def tick(self):
        self.loop.call_soon(self.loop.stop); self.loop.run_forever()
        if not self.task.done(): return
        self.timer.stop(); self.busy = False
        try:
            self.output = self.task.result(); self.refresh()
        except Exception as exc:
            self.status.setText(f"Delivery export stopped: {exc}")
        finally:
            self.loop.close(); self.loop = self.task = None
            self.body.setEnabled(True); self.cancel_button.setEnabled(False)

    def verified_output(self):
        if self.output is None: raise ValueError("No current delivery output.")
        _, _, fingerprint = self.service.prepare(self.config())
        if fingerprint != self.output.metadata.get("fingerprint"):
            raise ValueError("Delivery output is stale for the current source/settings.")
        return self.service.artifacts.verify(self.output)

    def play(self):
        try:
            manifest = self.verified_output()
            return QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.service.artifacts.store._artifact_path(manifest.storage_key))))
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc)); return False

    def save_copy(self):
        try:
            path = copy_artifact(self, self.service.artifacts.store, self.verified_output(), title="Save delivery copy",
                                 suggested="delivery.mp4", revalidate=self.verified_output)
            if path is not None: self.status.setText(f"Delivery copy saved: {path}")
            return path
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc)); return None

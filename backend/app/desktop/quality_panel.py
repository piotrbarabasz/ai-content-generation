"""Explicit local Quick/Deep QA, retained history, evidence and review decisions."""
import asyncio
from dataclasses import replace
import json

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QComboBox, QPushButton,
    QDoubleSpinBox, QSpinBox, QCheckBox, QTableWidget, QTableWidgetItem, QPlainTextEdit, QScrollArea)

from app.desktop.artifact_export import copy_artifact
from app.domain.production_quality import QARunConfiguration


class QualityPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = self.report_id = self.loop = self.task = None
        self.busy = self.canceled = self.loading = False
        self.changed = lambda: None
        layout = QVBoxLayout(self)
        self.status = QLabel("NOT_RUN: QA inspects existing assets; it does not regenerate or publish."); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.source = QComboBox(); layout.addWidget(self.source)
        self.source.currentIndexChanged.connect(lambda: self.run(self.source_changed))
        self.source_info = QLabel(); self.source_info.setWordWrap(True); layout.addWidget(self.source_info)
        row = QHBoxLayout(); layout.addLayout(row)
        def button(text, callback, target):
            b = QPushButton(text); b.clicked.connect(lambda: self.run(callback)); target.addWidget(b); return b
        self.refresh_button = button("Refresh sources/history (no scan)", self.activate, row)
        self.quick_button = button("Run Quick QA", lambda: self.start(self.scan("quick")), row)
        self.deep_button = button("Run Deep QA - full decode", lambda: self.start(self.scan("deep")), row)
        self.cancel_button = button("Cancel", self.cancel, row); self.cancel_button.setEnabled(False)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); self.body = QWidget(); scroll.setWidget(self.body); layout.addWidget(scroll)
        form = QFormLayout(self.body)
        self.warning = QLabel("Deep QA decodes the complete video/audio and samples a bounded number of shots. Local-only; no GPU or paid API required."); self.warning.setWordWrap(True); form.addRow(self.warning)
        self.semantic = QCheckBox("Request optional semantic review (currently UNAVAILABLE: no supported configured multimodal provider)"); form.addRow(self.semantic)
        self.inherit = QCheckBox("Inherit verified D069 loudness/peak targets when available"); self.inherit.setChecked(True); form.addRow(self.inherit)
        self.thresholds = {}
        for name, label, lo, hi, value in (("target_lufs", "Editorial target LUFS", -70, 0, -16), ("true_peak", "Editorial peak threshold dBTP", -20, 0, -1),
            ("lufs_tolerance", "Loudness tolerance LU", .1, 15, 1), ("silence_seconds", "Suspicious silence seconds", .1, 120, 3),
            ("black_seconds", "Black interval seconds", .04, 120, .4), ("freeze_seconds", "Low-motion interval seconds", .1, 600, 12),
            ("long_shot", "Long shot guidance seconds", 1, 600, 20)):
            field = QDoubleSpinBox(); field.setRange(lo, hi); field.setValue(value); field.setDecimals(2); form.addRow(label, field)
            self.thresholds[name] = field; field.valueChanged.connect(self.settings_changed)
        self.evidence = QCheckBox("Retain bounded evidence previews"); self.evidence.setChecked(True); form.addRow(self.evidence)
        self.reuse = QCheckBox("Reuse a complete report with identical verified inputs"); self.reuse.setChecked(True); form.addRow(self.reuse)
        self.semantic.toggled.connect(self.settings_changed); self.inherit.toggled.connect(self.settings_changed); self.evidence.toggled.connect(self.settings_changed)
        self.history = QComboBox(); form.addRow("Report history - exact source revisions", self.history)
        self.history.currentIndexChanged.connect(lambda: self.run(self.show_report))
        self.overview = QLabel(); self.overview.setWordWrap(True); form.addRow(self.overview)
        self.table = QTableWidget(0, 5); self.table.setHorizontalHeaderLabels(["Severity", "Timestamp", "Category", "Finding", "Action"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows); self.table.setEditTriggers(QTableWidget.NoEditTriggers); self.table.setMinimumHeight(250)
        self.table.itemSelectionChanged.connect(lambda: self.run(self.details)); form.addRow(self.table)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True); form.addRow("Measurements / explanation / references", self.detail)
        self.image = QLabel(); self.image.setAlignment(Qt.AlignCenter); form.addRow("Owned run evidence", self.image)
        self.samples = QComboBox(); form.addRow("Representative evidence (not findings)", self.samples)
        self.samples.currentIndexChanged.connect(lambda: self.run(self.sample_preview))
        self.note = QPlainTextEdit(); self.note.setMaximumHeight(70); form.addRow("Review note", self.note)
        self.review_action = QComboBox(); self.review_action.addItems(["note", "confirmed", "intentional", "resolved"]); form.addRow("Review action", self.review_action)
        self.resolution = QComboBox(); form.addRow("New completed run for resolution", self.resolution)
        for combo in (self.source, self.history, self.resolution, self.samples):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(24)
        reviews = QHBoxLayout(); form.addRow(reviews)
        button("Save separate review decision", self.review, reviews)
        button("Acknowledge current REVIEW items for manual Publish", self.acknowledge, reviews)
        exports = QHBoxLayout(); form.addRow(exports)
        button("Retain / export JSON copy", lambda: self.export("json"), exports)
        button("Retain / export Markdown copy", lambda: self.export("markdown"), exports)
        playback = QHBoxLayout(); form.addRow(playback)
        button("Open verified video at finding timestamp", self.play, playback); button("Stop playback", lambda: self.player.stop(), playback)
        button("Play representative evidence timestamp", lambda: self.play(sample=True), playback)
        self.video = QVideoWidget(); self.video.setMinimumSize(240, 220); form.addRow(self.video)
        self.audio_output = QAudioOutput(self); self.player = QMediaPlayer(self); self.player.setAudioOutput(self.audio_output); self.player.setVideoOutput(self.video)
        self.seek = 0; self.player.mediaStatusChanged.connect(lambda value: self.player.setPosition(self.seek) if value == QMediaPlayer.LoadedMedia else None)
        self.timer = QTimer(self); self.timer.setInterval(10); self.timer.timeout.connect(self.tick)
        self.enable(False)

    def run(self, callback):
        try: return callback()
        except Exception as exc: self.status.setText(str(exc))

    def enable(self, enabled):
        self.body.setEnabled(enabled); self.source.setEnabled(enabled)
        for b in (self.quick_button, self.deep_button, self.refresh_button): b.setEnabled(enabled)

    def bind(self, service):
        if self.busy: raise ValueError("Wait for QA process cleanup before changing projects.")
        self.player.stop(); self.service = service; self.report_id = None
        self.source.clear(); self.history.clear(); self.table.setRowCount(0); self.detail.clear(); self.image.clear()
        self.enable(bool(service)); self.status.setText("NOT_RUN: refresh retained sources; no scan starts automatically.")
        if service:
            self.loading = True
            try:
                config = service.config
                for name, field in self.thresholds.items(): field.setValue(getattr(config, name))
                self.semantic.setChecked(config.semantic); self.inherit.setChecked(config.inherit_delivery); self.evidence.setChecked(config.evidence)
            finally: self.loading = False

    def configuration(self, mode=None):
        config = self.service.config if self.service else QARunConfiguration()
        return replace(config, mode=mode or config.mode, **{name: field.value() for name, field in self.thresholds.items()},
                       inherit_delivery=self.inherit.isChecked(), evidence=self.evidence.isChecked(), semantic=self.semantic.isChecked())

    def settings_changed(self):
        if self.loading or not self.service: return
        try:
            self.service.config = self.configuration(); self.service.records.save("configuration", self.service.config.to_payload())
            self.show_report(); self.changed()
        except ValueError as exc: self.status.setText(str(exc))

    def activate(self):
        if self.busy or not self.service: return
        previous = self.source.currentData(); self.source.blockSignals(True); self.source.clear()
        for m in self.service.sources.candidates():
            self.source.addItem(m.artifact_type + " | " + m.artifact_id, m.artifact_id)
        index = self.source.findData(previous)
        if index < 0:
            selected = self.service.sources.publishing.renders.index.selected().get("project:video_render")
            index = self.source.findData(selected)
        if index >= 0: self.source.setCurrentIndex(index)
        self.source.blockSignals(False); self.refresh_history()
        self.source_changed()

    def source_changed(self):
        if not self.service: return
        if self.source.currentData():
            c = self.service.sources.capture(self.source.currentData()); e = c.facts.get("expected", {})
            self.source_info.setText(f"{c.source_kind} | {e.get('video_duration', 'unavailable')}s | {e.get('width', '?')} x {e.get('height', '?')} | freshness: {'STALE' if c.facts.get('stale') else 'retained; checksum checked on scan'}")
        self.show_report()

    def refresh_history(self):
        previous = self.report_id; self.history.blockSignals(True); self.history.clear(); self.resolution.clear()
        for m in reversed(self.service.records.history()):
            try:
                _, report = self.service.records.report(m.artifact_id)
                label = f"{report.configuration['mode']} | {report.source['source_id']} | {m.created_at.isoformat()}"
                self.history.addItem(label, m.artifact_id); self.resolution.addItem(label, m.artifact_id)
            except (ValueError, KeyError, TypeError, OSError): continue
        index = self.history.findData(previous)
        if index >= 0: self.history.setCurrentIndex(index)
        self.history.blockSignals(False); self.show_report()

    def show_report(self):
        if not self.service or not self.history.currentData(): return
        self.report_id = self.history.currentData(); report, decisions, state = self.service.view(self.report_id)
        if self.source.currentData() and self.source.currentData() != report.source["source_id"]: state = "STALE"
        counts = {name: sum(f.severity == name for f in report.findings) for name in ("BLOCKED", "REVIEW", "INFO")}
        self.status.setText(state)
        self.overview.setText(f"{state} | findings {counts}\nExecuted: " + ", ".join(v["validator_id"] for v in report.outcomes if v["state"] in {"PASS", "FAIL"}) +
                              "\nUnavailable/skipped: " + ", ".join(v["validator_id"] + ': ' + v.get("reason", "") for v in report.outcomes if v["state"] in {"UNAVAILABLE", "SKIPPED"}))
        self.table.blockSignals(True); self.table.clearContents(); self.table.setRowCount(len(report.findings))
        for row, f in enumerate(report.findings):
            time = "whole video" if f.start is None else f"{f.start:.3f}-{f.end:.3f}s"
            for col, text in enumerate((f.severity, time, f.category, f.rule_id, f.action)):
                item = QTableWidgetItem(text); item.setData(Qt.UserRole, f.id); self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)
        self.samples.blockSignals(True); self.samples.clear()
        for row in report.measurements.get("representative_evidence", []):
            self.samples.addItem(f"{row['time_seconds']:.3f}s | shot {row['shot_index']}", row)
        self.samples.blockSignals(False); self.sample_preview()

    def sample_preview(self):
        row = self.samples.currentData()
        if not row or not self.service: return
        m = self.service.records.one(row["artifact_id"], "evidence"); pix = QPixmap(); pix.loadFromData(self.service.records.bytes(m))
        self.image.setPixmap(pix.scaled(384, 384, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def selected_finding(self):
        row = self.table.currentRow()
        if row < 0: raise ValueError("Select a finding first.")
        identity = self.table.item(row, 0).data(Qt.UserRole)
        _, report = self.service.records.report(self.report_id)
        return report, next(f for f in report.findings if f.id == identity)

    def details(self):
        report, f = self.selected_finding(); self.image.clear()
        decisions = self.service.records.decisions(report)
        self.detail.setPlainText(json.dumps({**f.to_payload(), "decisions": [d for d in decisions if d["finding_id"] == f.id]}, indent=2, ensure_ascii=False))
        if f.evidence:
            m = self.service.records.one(f.evidence[0], "evidence"); pix = QPixmap(); pix.loadFromData(self.service.records.bytes(m))
            self.image.setPixmap(pix.scaled(384, 384, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def review(self):
        _, f = self.selected_finding(); action = self.review_action.currentText()
        self.service.records.review(self.report_id, f.id, action, self.note.toPlainText(), new_report_id=self.resolution.currentData() if action == "resolved" else None)
        self.show_report(); self.details(); self.changed()

    def acknowledge(self):
        c = self.service.sources.capture(self.source.currentData()); self.service.acknowledge_publishing(c.source_id, c.source_checksum, self.note.toPlainText())
        self.changed(); self.status.setText("Exact current REVIEW items acknowledged for manual packaging; BLOCKED/STALE/incomplete QA remains unapproved.")

    def export(self, kind):
        _, _, state = self.service.view(self.report_id); json_m, markdown_m = self.service.records.export(self.report_id, stale=state == "STALE")
        m = json_m if kind == "json" else markdown_m; suffix = ".json" if kind == "json" else ".md"
        return copy_artifact(self, self.service.records.store, m, title="Export QA " + kind, suggested="quality-report" + suffix,
                             revalidate=lambda: m if self.service.records.bytes(m) else None, suffix=suffix, file_filter="QA report (*" + suffix + ")")

    def play(self, *, sample=False):
        if sample:
            if not self.samples.currentData(): raise ValueError("Select retained representative evidence first.")
            _, report = self.service.records.report(self.report_id); time = self.samples.currentData()["time_seconds"]
        else:
            report, f = self.selected_finding(); time = f.start or 0
        c = self.service.sources.capture(report.source["source_id"])
        from app.application.production_quality import source_measurement
        if not c.facts.get("owner_ok") or source_measurement(c, lambda: False).get("source_checksum") != report.source["source_checksum"]:
            raise ValueError("Cannot preview a missing, foreign or changed source.")
        self.seek = round(time * 1000); self.player.setSource(QUrl.fromLocalFile(str(c.path))); self.player.setPosition(self.seek); self.player.play()

    async def scan(self, mode):
        m = await self.service.run(self.source.currentData(), self.configuration(mode), reuse=self.reuse.isChecked(), canceled=lambda: self.canceled, progress=lambda label, *_: self.status.setText("RUNNING: " + label))
        self.report_id = m.artifact_id; self.refresh_history(); self.changed()

    def start(self, coroutine):
        if self.busy: coroutine.close(); return
        self.player.stop(); self.busy = True; self.canceled = False; self.enable(False); self.cancel_button.setEnabled(True)
        self.loop = asyncio.new_event_loop(); self.task = self.loop.create_task(coroutine); self.timer.start()

    def cancel(self): self.canceled = True; self.status.setText("Cancellation requested; waiting for local process cleanup.")

    def tick(self):
        self.loop.call_soon(self.loop.stop); self.loop.run_forever()
        if not self.task.done(): return
        self.timer.stop()
        try: self.task.result()
        except Exception as exc: self.status.setText(str(exc))
        finally:
            self.loop.close(); self.loop = self.task = None; self.busy = False; self.enable(True); self.cancel_button.setEnabled(False); self.changed()

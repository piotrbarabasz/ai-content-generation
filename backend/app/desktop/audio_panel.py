"""Responsive Qt audio controls over an injected audio/jobs service port."""

import asyncio

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)


class AudioPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.services = self.section = self.language = None
        self.task = self.loop = None
        self.cancel_requested = False
        self.draft = False
        self.buffer = None
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.player.errorOccurred.connect(lambda *args: self.status.setText(self.player.errorString()))
        self.player.mediaStatusChanged.connect(self._media_status)
        layout = QVBoxLayout(self)
        self.voices = QComboBox()
        layout.addWidget(self.voices)
        self.preview_text = QLineEdit("This is a short voice preview.")
        self.preview_text.setMaxLength(400)
        layout.addWidget(self.preview_text)
        references = QHBoxLayout()
        layout.addLayout(references)
        self.references = QComboBox()
        self.references.setToolTip("Imported reference audio; approval history is retained in the project.")
        references.addWidget(self.references)
        self.approval_label = QLineEdit("editor-approved")
        self.approval_label.setMaxLength(128)
        self.approval_label.setPlaceholderText("Approval label or rejection reason")
        references.addWidget(self.approval_label)
        self.reference_buttons = {}
        for name, callback in (("Import reference WAV", self.import_reference),
                               ("Approve reference", self.approve_reference),
                               ("Reject reference", self.reject_reference)):
            button = QPushButton(name)
            button.clicked.connect(callback)
            references.addWidget(button)
            self.reference_buttons[name] = button
        actions = QHBoxLayout()
        layout.addLayout(actions)
        self.buttons = {}
        for name, callback in (("Preview voice", self.preview), ("Generate section audio", self.generate),
                               ("Cancel audio", self.cancel), ("Play audio", self.play), ("Stop audio", self.stop)):
            button = QPushButton(name)
            button.clicked.connect(callback)
            actions.addWidget(button)
            self.buttons[name] = button
        self.variant = QComboBox()
        self.variant.addItem("Original", "original")
        self.variant.addItem("Tempo processed", "processed")
        actions.addWidget(self.variant)
        self.status = QLabel("Audio services are not configured.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self._tick)
        self._enable()

    @property
    def busy(self):
        return self.task is not None

    def _enable(self):
        available = self.services is not None
        voice = available and self.voices.currentData() is not None
        self.voices.setEnabled(not self.busy)
        self.buttons["Preview voice"].setEnabled(voice and not self.busy)
        self.buttons["Generate section audio"].setEnabled(voice and self.section is not None and not self.busy and not self.draft)
        self.buttons["Play audio"].setEnabled(available and self.section is not None and not self.busy)
        self.buttons["Cancel audio"].setEnabled(self.busy and not self.cancel_requested)
        configured = getattr(self.services, "reference_audio_available", None) if available else None
        reference_capable = available and (
            bool(configured()) if callable(configured) else hasattr(self.services, "reference_entries"))
        selected_reference = reference_capable and self.references.currentData() is not None
        self.references.setEnabled(reference_capable and not self.busy)
        self.approval_label.setEnabled(reference_capable and not self.busy)
        self.reference_buttons["Import reference WAV"].setEnabled(reference_capable and not self.busy)
        self.reference_buttons["Approve reference"].setEnabled(selected_reference and not self.busy)
        self.reference_buttons["Reject reference"].setEnabled(selected_reference and not self.busy)

    def bind(self, services, language):
        if self.busy:
            raise ValueError("Cancel audio and wait for worker cleanup before switching projects.")
        self.stop()
        self.services, self.section, self.language = services, None, language
        self._refresh_references()
        self._refresh_voices()
        self._enable()

    def _refresh_voices(self):
        self.voices.clear()
        if self.services:
            try:
                for choice in self.services.choices(self.language):
                    self.voices.addItem(choice.label, choice)
                self.status.setText("Ready" if self.voices.count() else "No compatible configured voices for this language.")
            except Exception as exc:
                self.status.setText(str(exc))
        else:
            self.status.setText("Audio services are not configured.")

    def _refresh_references(self):
        self.references.clear()
        if self.services is None or not hasattr(self.services, "reference_entries"):
            return
        try:
            for source, decision in self.services.reference_entries():
                status = "pending" if decision is None else f"{decision.status}: {decision.label}"
                self.references.addItem(
                    f"{source.source_name} / {source.duration_seconds:.2f}s / {status}", source.artifact_id)
        except Exception as exc:
            self.status.setText(str(exc))

    def import_reference(self):
        if self.services is None or self.busy or not hasattr(self.services, "import_reference"):
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import reference WAV", "", "WAV audio (*.wav)")
        if not path:
            return
        try:
            source = self.services.import_reference(path)
            self._refresh_references()
            index = self.references.findData(source.artifact_id)
            if index >= 0:
                self.references.setCurrentIndex(index)
            self.status.setText("Reference imported; approve it before use.")
        except Exception as exc:
            self.status.setText(str(exc))
        self._enable()

    def _decide_reference(self, approved):
        artifact_id = self.references.currentData()
        if self.services is None or self.busy or artifact_id is None:
            return
        try:
            label = self.approval_label.text()
            if approved:
                self.services.approve_reference(artifact_id, label)
            else:
                self.services.reject_reference(artifact_id, label)
            self._refresh_references()
            index = self.references.findData(artifact_id)
            if index >= 0:
                self.references.setCurrentIndex(index)
            self._refresh_voices()
        except Exception as exc:
            self.status.setText(str(exc))
        self._enable()

    def approve_reference(self):
        self._decide_reference(True)

    def reject_reference(self):
        self._decide_reference(False)

    def select_section(self, section):
        self.stop()
        self.section = section
        self.draft = False
        self._enable()

    def set_draft(self, dirty):
        self.draft = dirty
        self._enable()

    def _start(self, operation, kind):
        if self.busy:
            operation.close()
            return
        self.services.canceled = False
        self.cancel_requested = False
        self.kind = kind
        self.loop = asyncio.new_event_loop()
        self.task = self.loop.create_task(operation)
        self.status.setText("Preparing audio")
        self.timer.start()
        self._enable()

    def preview(self):
        if self.services and not self.busy and self.voices.currentData():
            self._start(self.services.preview(self.voices.currentData(), self.preview_text.text()), "preview")

    def generate(self):
        if self.services and self.section and not self.busy and not self.draft and self.voices.currentData():
            self._start(self.services.generate(self.section, self.voices.currentData()), "generation")

    def _tick(self):
        self.loop.call_soon(self.loop.stop)
        self.loop.run_forever()
        if not self.task.done():
            try:
                self.status.setText("Cancel requested; waiting for cleanup" if self.cancel_requested else self.services.progress())
            except Exception as exc:
                self.status.setText(str(exc))
            return
        self.timer.stop()
        try:
            result = self.task.result()
            if self.cancel_requested and self.kind == "preview":
                self.status.setText("Canceled")
            elif self.kind == "preview":
                self._play(result)
            else:
                self.status.setText("Canceled" if result == "canceled" else str(result))
        except Exception as exc:
            self.status.setText(str(exc))
        finally:
            self.task = None
            self.loop.close()
            self.loop = None
            self._enable()

    def cancel(self):
        if self.busy:
            try:
                self.services.cancel()
                self.cancel_requested = True
                self._enable()
            except Exception as exc:
                self.status.setText(str(exc))

    def play(self):
        try:
            self._play(self.services.playback(self.section, self.variant.currentData(), self.voices.currentData()))
        except Exception as exc:
            self.status.setText(str(exc))

    def _play(self, audio):
        self.stop()
        self.buffer = QBuffer(self)
        self.buffer.setData(QByteArray(audio.payload))
        self.buffer.open(QIODevice.ReadOnly)
        self.player.setSourceDevice(self.buffer, QUrl("retained-audio.wav"))
        self.status.setText(audio.label)
        self.player.play()

    def stop(self):
        self.player.stop()
        self.player.setSource(QUrl())
        if self.buffer:
            self.buffer.close()
            self.buffer.deleteLater()
            self.buffer = None

    def _media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.status.setText(self.status.text() + " — playback finished")

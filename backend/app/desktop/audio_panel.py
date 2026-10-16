"""Responsive Qt audio controls over an injected audio/jobs service port."""

import asyncio

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)


class AudioPanel(QWidget):
    readiness_changed = Signal()

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
        from app.desktop.narration_panel import NarrationSettingsPanel
        self.narration_controls = NarrationSettingsPanel(self)
        self.narration_controls.hide()
        self.narration_controls.changed.connect(self._narration_changed)
        self.narration_scroll = QScrollArea(); self.narration_scroll.setWidgetResizable(True)
        self.narration_scroll.setWidget(self.narration_controls); self.narration_scroll.setMaximumHeight(350)
        self.narration_scroll.hide(); layout.addWidget(self.narration_scroll)
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
        actions = QGridLayout()
        layout.addLayout(actions)
        self.buttons = {}
        for index, (name, callback) in enumerate((("Preview voice", self.preview), ("Generate section audio", self.generate),
                               ("Cancel audio", self.cancel), ("Play audio", self.play), ("Stop audio", self.stop))):
            button = QPushButton(name)
            button.clicked.connect(callback)
            actions.addWidget(button, index // 3, index % 3)
            self.buttons[name] = button
        self.variant = QComboBox()
        self.variant.addItem("Original", "original")
        self.variant.addItem("Tempo processed", "processed")
        actions.addWidget(self.variant, 1, 2)
        self.status = QLabel("Audio services are not configured.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        compare = QHBoxLayout(); layout.addLayout(compare)
        self.history_a, self.history_b = QComboBox(), QComboBox()
        compare.addWidget(QLabel("Compare narration A / B")); compare.addWidget(self.history_a); compare.addWidget(self.history_b)
        compare_actions = QGridLayout(); layout.addLayout(compare_actions)
        self.cloud_buttons = []
        for index, (name, callback) in enumerate((("Preview selected section", self.preview_section), ("Play previous / A", lambda: self.play_history(self.history_a)),
                               ("Play new / B", lambda: self.play_history(self.history_b)), ("Approve preferred B", self.approve_variant))):
            button = QPushButton(name); button.clicked.connect(callback); compare_actions.addWidget(button, index // 2, index % 2); self.cloud_buttons.append(button)
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
        cloud = available and hasattr(self.services, "narration")
        self.narration_scroll.setVisible(cloud)
        self.narration_controls.setEnabled(not self.busy)
        for button in self.cloud_buttons:
            button.setVisible(cloud); button.setEnabled(cloud and self.section is not None and not self.busy and not self.draft)
        for widget in (self.history_a, self.history_b):
            widget.setVisible(cloud); widget.setEnabled(not self.busy)
        for widget in (self.references, self.approval_label, *self.reference_buttons.values()):
            widget.setVisible(not cloud)

    def bind(self, services, language):
        if self.busy:
            raise ValueError("Cancel audio and wait for worker cleanup before switching projects.")
        self.stop()
        self.services, self.section, self.language = services, None, language
        self.narration_controls.section = None
        self.narration_controls.bind(services if hasattr(services, "narration") else None)
        self._refresh_references()
        self._refresh_voices()
        self._enable()
        self.readiness_changed.emit()

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
        if hasattr(self.services, "narration"):
            self.narration_controls.section = section
            self.narration_controls.reload()
            self._refresh_history()
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
        try:
            if self.services and not self.busy and self.voices.currentData():
                choice, text = self.voices.currentData(), self.preview_text.text()
                if hasattr(self.services, "estimate_preview"):
                    self._start(self._paid_operation(lambda: self.services.estimate_preview(choice, text),
                        lambda: self.services.preview(choice, text)), "preview")
                else:
                    self._start(self.services.preview(choice, text), "preview")
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def generate(self):
        try:
            if self.services and self.section and not self.busy and not self.draft and self.voices.currentData():
                section, choice = self.section, self.voices.currentData()
                if hasattr(self.services, "estimate_async"):
                    self._start(self._paid_operation(lambda: self.services.estimate_async((section,), choice),
                        lambda: self.services.generate(section, choice)), "generation")
                else:
                    self._start(self.services.generate(section, choice), "generation")
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

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
            self._refresh_history()
            if hasattr(self.services, "narration"):
                self.narration_controls.reload()
            if self.kind == "generation":
                self.readiness_changed.emit()

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

    def _narration_changed(self):
        self._refresh_voices()
        self.readiness_changed.emit()

    def _refresh_history(self):
        if not hasattr(self.services, "history_audio") or self.section is None:
            return
        entries = self.services.history_audio(self.section)
        for box in (self.history_a, self.history_b):
            box.clear()
            for label, artifact_id, preview_id in entries:
                box.addItem(label, (artifact_id, preview_id))
        if entries:
            self.history_b.setCurrentIndex(len(entries)-1)

    async def authorize_sections(self, sections, choice):
        if not hasattr(self.services, "estimate"):
            return True
        from app.desktop.narration_panel import approve_estimate_async
        estimate = await self.services.estimate_async(sections, choice)
        return await approve_estimate_async(self, estimate)

    async def _paid_operation(self, estimate_factory, operation):
        from app.desktop.narration_panel import approve_estimate_async
        estimate = estimate_factory()
        if asyncio.iscoroutine(estimate):
            estimate = await estimate
        if self.cancel_requested or not await approve_estimate_async(self, estimate):
            self.cancel_requested = True
            return "canceled"
        if self.cancel_requested:
            return "canceled"
        return await operation()

    def preview_section(self):
        try:
            choice = self.voices.currentData()
            if self.section and not self.busy:
                section = self.section
                self._start(self._paid_operation(lambda: self.services.estimate_preview(choice, section.text, section),
                    lambda: self.services.preview(choice, section.text, section=section)), "preview")
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def play_history(self, box):
        if box.currentData() and not self.busy:
            try:
                self._play(self.services.retained_playback(box.currentData()[0]))
            except (ValueError, OSError) as exc:
                self.status.setText(str(exc))

    def approve_variant(self):
        value = self.history_b.currentData()
        if value and value[1] and self.section and not self.busy:
            self._start(self.services.approve_preview(value[1], self.voices.currentData()), "generation")

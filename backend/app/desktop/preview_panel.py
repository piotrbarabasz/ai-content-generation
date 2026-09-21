"""Qt playback adapter for exact scene audio ranges and film proxies."""

import asyncio

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class PreviewPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.services = self.edit = None
        self.task = self.loop = None
        self._progress = ""
        layout = QVBoxLayout(self)
        self.scene_choice = QComboBox()
        layout.addWidget(self.scene_choice)
        self.image = QLabel("Select a saved timeline to preview.")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumHeight(180)
        self.image.setScaledContents(False)
        layout.addWidget(self.image, 1)
        self.video = QVideoWidget(self)
        self.video.setMinimumHeight(180)
        self.video.hide()
        layout.addWidget(self.video, 1)
        controls = QHBoxLayout()
        layout.addLayout(controls)
        self.buttons = {}
        for name, callback in (("Preview scene", self.preview_scene), ("Render/play film proxy", self.preview_film),
                               ("Cancel render", self.cancel), ("Stop", self.stop)):
            button = QPushButton(name)
            button.clicked.connect(callback)
            controls.addWidget(button)
            self.buttons[name] = button
        self.status = QLabel("Preview services are not configured.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.errorOccurred.connect(lambda *_: self.status.setText(self.player.errorString()))
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self._tick)
        self._enable()

    @property
    def busy(self):
        return self.task is not None

    def _enable(self):
        ready = self.services is not None and self.edit is not None
        self.buttons["Preview scene"].setEnabled(ready and not self.busy and self.scene_choice.count() > 0)
        self.buttons["Render/play film proxy"].setEnabled(ready and not self.busy)
        self.buttons["Cancel render"].setEnabled(self.busy)
        self.scene_choice.setEnabled(ready and not self.busy)

    def bind(self, services):
        if self.busy:
            raise ValueError("Cancel preview rendering before switching projects.")
        self.clear()
        self.services, self.edit = services, None
        self.scene_choice.clear()
        self.status.setText("Preview services are not configured." if services is None else "Save a timeline to preview it.")
        self._enable()

    def timeline_changed(self, edit):
        self.clear()
        self.edit = edit
        self.scene_choice.clear()
        if edit is not None:
            for clip in edit.timeline.clips:
                self.scene_choice.addItem(clip.media.scene_id, clip.media.scene_id)
        if self.services is None:
            self.status.setText("Preview services are not configured.")
        elif edit is None:
            self.status.setText("Save a timeline to preview it.")
        elif self.services.is_current(edit.timeline):
            self.status.setText(f"Preview ready for {edit.timeline.id}.")
        else:
            self.status.setText("Preview is stale: selected media changed; refresh the timeline.")
        self._enable()

    def preview_scene(self):
        self.clear()
        try:
            result = self.services.scene(self.edit.timeline, self.scene_choice.currentData())
            pixmap = QPixmap(str(result.image_path))
            if pixmap.isNull():
                raise ValueError("Prepared scene image cannot be decoded by Qt.")
            self.video.hide()
            self.image.show()
            self.image.setPixmap(pixmap.scaled(self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.player.setSource(QUrl.fromLocalFile(str(result.audio_path)))
            self.player.play()
            self.status.setText(f"Playing exact scene range: {result.frame_count} samples at {result.sample_rate} Hz.")
        except Exception as exc:
            self.status.setText(str(exc))

    def preview_film(self):
        if self.busy or self.services is None or self.edit is None:
            return
        self.clear()
        self.loop = asyncio.new_event_loop()
        self.task = self.loop.create_task(self.services.proxy(self.edit.timeline, self._render_progress))
        self._progress = "Starting proxy render"
        self.status.setText(self._progress)
        self.timer.start()
        self._enable()

    def _render_progress(self, phase, completed, total):
        self._progress = f"{phase}: {completed}/{total}"

    def _tick(self):
        self.loop.call_soon(self.loop.stop)
        self.loop.run_forever()
        if not self.task.done():
            self.status.setText(self._progress)
            return
        self.timer.stop()
        try:
            result = self.task.result()
            if not result.current:
                self.clear()
                self.status.setText("Finished proxy is stale and will not be played.")
            else:
                self.image.hide()
                self.video.show()
                self.player.setSource(QUrl.fromLocalFile(str(result.path)))
                self.player.play()
                source = "cache" if result.cached else "new render"
                self.status.setText(f"Playing current film proxy ({source}) for {result.timeline_id}.")
        except Exception as exc:
            self.status.setText("Canceled" if "cancel" in str(exc).lower() else str(exc))
        finally:
            self.task = None
            self.loop.close()
            self.loop = None
            self._enable()

    def cancel(self):
        if self.busy:
            self.services.cancel()
            self.status.setText("Cancel requested; waiting for FFmpeg cleanup.")

    def stop(self):
        self.player.stop()

    def clear(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.video.hide()
        self.image.show()
        self.image.setPixmap(QPixmap())
        self.image.setText("Select a saved timeline to preview.")

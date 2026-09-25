"""Qt playback adapter for exact scene audio ranges and film proxies."""

import asyncio

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class PreviewPanel(QWidget):
    def __init__(self, parent=None, *, player_factory=QMediaPlayer,
                 audio_factory=QAudioOutput, media_devices=QMediaDevices):
        super().__init__(parent)
        self.services = self.edit = None
        self.task = self.loop = None
        self._progress = ""
        layout = QVBoxLayout(self)
        self.scene_choice = QComboBox()
        layout.addWidget(self.scene_choice)
        audio_controls = QHBoxLayout()
        audio_controls.addWidget(QLabel("Audio output"))
        self.audio_choice = QComboBox()
        audio_controls.addWidget(self.audio_choice, 1)
        layout.addLayout(audio_controls)
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
        self.player = player_factory(self)
        self.audio = audio_factory(self)
        self._media_devices_api = media_devices
        self.media_devices = media_devices(self)
        self.audio.setMuted(False)
        self.audio.setVolume(1.0)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self._follow_default_audio = True
        self.audio_choice.currentIndexChanged.connect(self._select_audio_output)
        self.media_devices.audioOutputsChanged.connect(self._refresh_audio_outputs)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
        self.player.playbackStateChanged.connect(self._playback_state_changed)
        self.player.errorOccurred.connect(self._playback_error)
        self._playback_message = ""
        self._refresh_audio_outputs()
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

    @staticmethod
    def _device_id(device):
        return bytes(device.id()) if device is not None and not device.isNull() else b""

    def _refresh_audio_outputs(self):
        devices = list(self._media_devices_api.audioOutputs())
        default = self._media_devices_api.defaultAudioOutput()
        selected_id = (self._device_id(default) if self._follow_default_audio
                       else self._device_id(self.audio_choice.currentData()))
        self.audio_choice.blockSignals(True)
        self.audio_choice.clear()
        for device in sorted(devices, key=lambda item: not item.isDefault()):
            label = device.description() + (" (system default)" if device.isDefault() else "")
            self.audio_choice.addItem(label, device)
        index = next(
            (i for i in range(self.audio_choice.count())
             if self._device_id(self.audio_choice.itemData(i)) == selected_id),
            0 if devices else -1,
        )
        if index >= 0:
            self.audio_choice.setCurrentIndex(index)
        else:
            self.audio_choice.addItem("No audio output devices available", None)
        self.audio_choice.setEnabled(bool(devices))
        self.audio_choice.blockSignals(False)
        self._select_audio_output()

    def _select_audio_output(self, *_):
        if _:
            self._follow_default_audio = False
        device = self.audio_choice.currentData()
        if device is not None and not device.isNull():
            self.audio.setDevice(device)

    def _prepare_audio(self):
        device = self.audio_choice.currentData()
        if device is None or device.isNull():
            raise RuntimeError("No audio output device is available. Connect or enable a Windows playback device.")
        # Reassert the complete route before every source. Qt keeps this attachment
        # across setSource(), but doing so also repairs a backend/device reset.
        self.audio.setDevice(device)
        self.audio.setMuted(False)
        self.audio.setVolume(1.0)
        self.player.setAudioOutput(self.audio)

    def _play(self, path, message):
        self._prepare_audio()
        self._playback_message = f"{message} Output: {self.audio.device().description()}."
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.status.setText(self._playback_message)
        self.player.play()

    def _media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.status.setText("Media failed to load or uses an unsupported audio/video format.")
        elif status == QMediaPlayer.MediaStatus.EndOfMedia and self._playback_message:
            self.status.setText(self._playback_message + " Playback finished.")

    def _playback_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState and self._playback_message:
            self.status.setText(self._playback_message)

    def _playback_error(self, error, message=""):
        detail = message or self.player.errorString() or "unknown playback error"
        if error == QMediaPlayer.Error.FormatError:
            prefix = "Unsupported media/audio format"
        elif error == QMediaPlayer.Error.ResourceError:
            prefix = "Media failed to load"
        else:
            prefix = "Qt multimedia playback error"
        self.status.setText(f"{prefix}: {detail}")

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
            self._play(
                result.audio_path,
                f"Playing exact scene range: {result.frame_count} samples at {result.sample_rate} Hz.",
            )
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
                source = "cache" if result.cached else "new render"
                self._play(result.path, f"Playing current film proxy ({source}) for {result.timeline_id}.")
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
        if not self.player.source().isEmpty():
            self.status.setText("Playback stopped.")

    def clear(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self._playback_message = ""
        self.video.hide()
        self.image.show()
        self.image.setPixmap(QPixmap())
        self.image.setText("Select a saved timeline to preview.")

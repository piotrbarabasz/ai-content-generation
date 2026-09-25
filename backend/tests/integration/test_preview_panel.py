"""D046 Qt controls bind current previews and suppress stale film playback."""

import asyncio
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.application.preview import FilmPreview
from app.desktop.preview_panel import PreviewPanel
from tests.unit.test_timeline import compile_values, media


@pytest.fixture
def qt():
    return QApplication.instance() or QApplication([])


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class Device:
    def __init__(self, identifier=b"", description="", default=False):
        self.identifier, self.name, self.default = identifier, description, default

    def id(self):
        return self.identifier

    def description(self):
        return self.name

    def isDefault(self):
        return self.default

    def isNull(self):
        return not self.identifier


class MediaDevices:
    devices = [Device(b"default", "Default speakers", True), Device(b"headset", "USB headset")]

    def __init__(self, parent):
        self.audioOutputsChanged = Signal()

    @classmethod
    def audioOutputs(cls):
        return cls.devices

    @classmethod
    def defaultAudioOutput(cls):
        return next((device for device in cls.devices if device.isDefault()), Device())


class AudioOutput:
    def __init__(self, parent):
        self.current_device = Device()
        self.muted = True
        self.level = 0.0

    def setDevice(self, device):
        self.current_device = device

    def device(self):
        return self.current_device

    def setMuted(self, muted):
        self.muted = muted

    def isMuted(self):
        return self.muted

    def setVolume(self, level):
        self.level = level

    def volume(self):
        return self.level


class MediaPlayer:
    def __init__(self, parent):
        self.errorOccurred = Signal()
        self.mediaStatusChanged = Signal()
        self.playbackStateChanged = Signal()
        self.current_source = QUrl()
        self.output = None
        self.play_calls = self.stop_calls = 0

    def setAudioOutput(self, output):
        self.output = output

    def setVideoOutput(self, output):
        self.video_output = output

    def setSource(self, source):
        self.current_source = source

    def source(self):
        return self.current_source

    def play(self):
        self.play_calls += 1

    def stop(self):
        self.stop_calls += 1

    def errorString(self):
        return ""


def make_panel():
    return PreviewPanel(
        player_factory=MediaPlayer,
        audio_factory=AudioOutput,
        media_devices=MediaDevices,
    )


class Services:
    def __init__(self, path, current, scene_result=None):
        self.path, self.current, self.scene_result = path, current, scene_result

    def is_current(self, timeline):
        return True

    async def proxy(self, timeline, progress):
        progress("encoding", 1, 1)
        await asyncio.sleep(0)
        return FilmPreview("a" * 64, timeline.id, self.path, self.current, False)

    def scene(self, timeline, scene_id):
        return self.scene_result

    def cancel(self):
        pass


@pytest.mark.parametrize("current", [True, False])
def test_proxy_control_only_loads_current_result(qt, tmp_path, current):
    path = tmp_path / "proxy.mp4"
    path.write_bytes(b"fixture")
    timeline = compile_values(media())
    panel = make_panel()
    panel.bind(Services(path, current))
    panel.timeline_changed(SimpleNamespace(timeline=timeline))
    panel.show()
    try:
        QTest.mouseClick(panel.buttons["Render/play film proxy"], Qt.LeftButton)
        while panel.busy:
            panel._tick()
        if current:
            assert panel.player.source().toLocalFile().replace("\\", "/") == str(path).replace("\\", "/")
            assert "current film proxy" in panel.status.text()
            assert panel.player.play_calls == 1
            assert panel.player.output is panel.audio
            assert not panel.audio.isMuted() and panel.audio.volume() == 1.0
            assert panel.audio.device().id() == b"default"
            panel.timeline_changed(SimpleNamespace(timeline=timeline))
            assert panel.player.source().isEmpty() and panel.video.isHidden()
        else:
            assert panel.player.source().isEmpty()
            assert "stale" in panel.status.text()
    finally:
        panel.close()


def test_scene_audio_uses_selected_device_and_survives_clear_and_stop(qt, tmp_path):
    image = tmp_path / "scene.png"
    pixmap = QPixmap(4, 4)
    assert pixmap.save(str(image), "PNG")
    audio = tmp_path / "scene.wav"
    audio.write_bytes(b"fixture")
    result = SimpleNamespace(image_path=image, audio_path=audio, frame_count=48000, sample_rate=48000)
    timeline = compile_values(media())
    panel = make_panel()
    panel.bind(Services(tmp_path / "proxy.mp4", True, result))
    panel.timeline_changed(SimpleNamespace(timeline=timeline))
    panel.audio_choice.setCurrentIndex(1)
    try:
        panel.preview_scene()
        assert panel.player.source().toLocalFile().replace("\\", "/") == str(audio).replace("\\", "/")
        assert panel.player.play_calls == 1
        assert panel.audio.device().id() == b"headset"
        assert not panel.audio.isMuted() and panel.audio.volume() == 1.0

        panel.stop()
        panel.clear()
        panel.preview_scene()
        assert panel.player.play_calls == 2
        assert panel.player.output is panel.audio
        assert panel.audio.device().id() == b"headset"
    finally:
        panel.close()


def test_system_default_change_is_followed_until_user_selects_a_device(qt):
    previous = MediaDevices.devices
    panel = make_panel()
    try:
        MediaDevices.devices = [Device(b"default", "Old speakers"), Device(b"headset", "USB headset", True)]
        panel._refresh_audio_outputs()
        assert panel.audio.device().id() == b"headset"

        panel.audio_choice.setCurrentIndex(1)
        panel.audio_choice.setCurrentIndex(0)
        MediaDevices.devices = [Device(b"default", "Old speakers", True), Device(b"headset", "USB headset")]
        panel._refresh_audio_outputs()
        assert panel.audio.device().id() == b"headset"
    finally:
        panel.close()
        MediaDevices.devices = previous


def test_playback_failures_surface_useful_status(qt):
    panel = make_panel()
    try:
        panel._playback_error(QMediaPlayer.Error.FormatError, "decoder unavailable")
        assert "Unsupported media/audio format" in panel.status.text()
        panel._media_status_changed(QMediaPlayer.MediaStatus.InvalidMedia)
        assert "failed to load" in panel.status.text()
    finally:
        panel.close()


def test_no_audio_output_prevents_silent_playback(qt, tmp_path):
    previous = MediaDevices.devices
    MediaDevices.devices = []
    panel = make_panel()
    try:
        with pytest.raises(RuntimeError, match="No audio output device"):
            panel._play(tmp_path / "scene.wav", "Playing")
        assert panel.player.play_calls == 0
    finally:
        panel.close()
        MediaDevices.devices = previous

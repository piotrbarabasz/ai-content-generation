"""Controllable decoder boundary for UI tests whose artifact bytes are fake MP4s."""
import pytest
from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtMultimedia import QMediaPlayer


class FakePlayer(QObject):
    mediaStatusChanged = Signal(object)
    seekableChanged = Signal(bool)
    positionChanged = Signal(int)
    errorOccurred = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = QUrl()
        self._status = QMediaPlayer.NoMedia
        self._position = 0
        self._seekable = False
        self._state = QMediaPlayer.StoppedState
        self.auto_ready = True
        self.played = []

    def setAudioOutput(self, _): pass
    def setVideoOutput(self, _): pass
    def source(self): return self._source
    def mediaStatus(self): return self._status
    def isSeekable(self): return self._seekable
    def position(self): return self._position
    def playbackState(self): return self._state
    def error(self): return QMediaPlayer.NoError
    def errorString(self): return ""

    def setSource(self, url):
        self._source = url
        self._position = 0
        self._seekable = False
        self._status = QMediaPlayer.LoadingMedia if not url.isEmpty() else QMediaPlayer.NoMedia
        self.mediaStatusChanged.emit(self._status)
        if not url.isEmpty() and self.auto_ready:
            self.loaded()

    def loaded(self):
        self._status = QMediaPlayer.LoadedMedia
        self.mediaStatusChanged.emit(self._status)
        self._seekable = True
        self.seekableChanged.emit(True)

    def setPosition(self, position):
        if self._seekable:
            self._position = position
            self.positionChanged.emit(position)

    def play(self):
        self._state = QMediaPlayer.PlayingState
        self.played.append((self._source, self._position))

    def pause(self): self._state = QMediaPlayer.PausedState
    def stop(self):
        self._state = QMediaPlayer.StoppedState
        self._position = 0
        self.positionChanged.emit(0)


@pytest.fixture
def fake_players(monkeypatch):
    from app.desktop import shorts_panel, director_panel
    monkeypatch.setattr(shorts_panel, 'QMediaPlayer', FakePlayer)
    monkeypatch.setattr(director_panel, 'QMediaPlayer', FakePlayer)

"""Source-bound playback for the two Shorts panels; no artifact selection writes."""
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Qt
from PySide6.QtMultimedia import QMediaPlayer


class DisplayedMedia(Enum):
    NO_MEDIA = auto()
    ORIGINAL_EXCERPT = auto()
    VERIFIED_SHORT = auto()
    DIRECTED_SHORT = auto()
    NARRATION = auto()
    PLAYBACK_ERROR = auto()


MEDIA_NAMES = {
    DisplayedMedia.NO_MEDIA: "No media loaded",
    DisplayedMedia.ORIGINAL_EXCERPT: "Original excerpt · source framing",
    DisplayedMedia.VERIFIED_SHORT: "Verified Short · portrait MP4",
    DisplayedMedia.DIRECTED_SHORT: "Verified directed Short · portrait MP4",
    DisplayedMedia.NARRATION: "Director narration · audio",
}


@dataclass(frozen=True)
class MediaRequest:
    url: QUrl
    kind: DisplayedMedia
    context: tuple
    start: int
    end: int | None


class ShortsPlayback(QObject):
    changed = Signal()
    failed = Signal(str)

    def __init__(self, player, current_context, parent=None):
        super().__init__(parent)
        self.player, self.current_context = player, current_context
        self.kind = DisplayedMedia.NO_MEDIA
        self.request = None
        self.error_message = ""
        self.pending = False
        self._generation = 0
        self._connections = []
        self._seek_issued = self._autoplay = False

    @property
    def label(self):
        if self.kind == DisplayedMedia.PLAYBACK_ERROR:
            return "Playback error · " + MEDIA_NAMES[self.request.kind]
        label = MEDIA_NAMES[self.kind]
        if self.request and self.request.end is not None:
            label += f"\n{self.request.start / 1000:.3f}–{self.request.end / 1000:.3f} s"
        return "Loading · " + label if self.pending else label

    def _disconnect(self):
        self._generation += 1
        for signal, slot in self._connections:
            signal.disconnect(slot)
        self._connections.clear()

    def clear(self):
        self._disconnect()
        self.request = None
        self.pending = self._autoplay = self._seek_issued = False
        self.kind = DisplayedMedia.NO_MEDIA
        self.player.stop()
        self.player.setSource(QUrl())
        self.changed.emit()

    def dismiss_error(self):
        self.error_message = ""
        if self.kind == DisplayedMedia.PLAYBACK_ERROR:
            self.clear()
        self.changed.emit()

    def stop(self):
        # A late loaded/seekable notification must not restart a stopped request.
        self.pending = self._autoplay = False
        self.player.stop()
        self.changed.emit()

    def load(self, path, kind, context, *, start=0, end=None, autoplay=False, retry=False):
        if self.error_message and not retry:
            return
        reopen = bool(self.error_message) or self.player.mediaStatus() == QMediaPlayer.InvalidMedia
        if retry:
            self.error_message = ""
        self._disconnect()
        self.pending = self._autoplay = False
        self.player.stop()
        if reopen:
            # Qt ignores setSource(the_same_url), even after InvalidMedia. A retry
            # must actually reopen a restored file, with old handlers disconnected.
            self.player.setSource(QUrl())
        self.request = MediaRequest(QUrl.fromLocalFile(str(Path(path))), kind, context, start, end)
        self.kind, self.pending = kind, True
        self._seek_issued, self._autoplay = False, autoplay
        generation = self._generation
        handlers = (
            (self.player.mediaStatusChanged, lambda status: self._status(generation, status)),
            (self.player.seekableChanged, lambda _: self._advance(generation)),
            (self.player.positionChanged, lambda position: self._position(generation, position)),
            (self.player.errorOccurred, lambda code, message: self._error(generation, code, message)),
        )
        for signal, slot in handlers:
            signal.connect(slot)
            self._connections.append((signal, slot))
        if self.player.source() != self.request.url or self.player.mediaStatus() == QMediaPlayer.InvalidMedia:
            self.player.setSource(self.request.url)
        self.changed.emit()
        self._advance(generation)

    def _active(self, generation):
        return (generation == self._generation and self.request is not None
                and self.request.context == self.current_context()
                and self.player.source() == self.request.url)

    def _status(self, generation, status):
        if not self._active(generation):
            return
        if status == QMediaPlayer.InvalidMedia:
            self._error(generation, self.player.error(), self.player.errorString() or "Media loading failed.")
        else:
            self._advance(generation)

    def _advance(self, generation):
        if not self._active(generation) or not self.pending:
            return
        if self.player.mediaStatus() not in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia,
                                             QMediaPlayer.BufferingMedia, QMediaPlayer.EndOfMedia):
            return
        if not self.player.isSeekable():
            return
        if not self._seek_issued:
            self._seek_issued = True
            self.player.setPosition(self.request.start)
        self._position(generation, self.player.position())

    def _position(self, generation, position):
        if not self._active(generation):
            return
        if self.pending:
            if not self._seek_issued or position != self.request.start:
                return
            self.pending = False
            self.changed.emit()
            if self._autoplay:
                self.player.play()
        elif self._autoplay and self.request.end is not None and position >= self.request.end:
            self.player.pause()

    def _error(self, generation, code, message):
        if not self._active(generation) or self.error_message:
            return
        if code == QMediaPlayer.NoError and self.player.mediaStatus() != QMediaPlayer.InvalidMedia:
            return
        self.pending = self._autoplay = False
        self.player.pause()
        attempted = MEDIA_NAMES[self.request.kind].split(" · ")[0].lower()
        recovery = ("The rendered MP4 is still saved. Retry playback or Save MP4 to use another player."
                    if self.request.kind in (DisplayedMedia.VERIFIED_SHORT, DisplayedMedia.DIRECTED_SHORT)
                    else "Check that the source file is available and supported, then retry playback.")
        try:
            available = Path(self.request.url.toLocalFile()).is_file()
        except OSError:
            available = False
        if not available:
            recovery = "The media file is missing or inaccessible. Restore it or select an available source/render, then retry."
        detail = (message or 'media decoder or loading failed').rstrip('. ')
        self.error_message = f"Could not play {attempted}: {detail}. {recovery}"
        self.kind = DisplayedMedia.PLAYBACK_ERROR
        self.changed.emit()
        self.failed.emit(self.error_message)


def present_video(video, kind, *, director=False):
    """Use a portrait canvas for rendered Shorts without stretching the media."""
    original = kind == DisplayedMedia.ORIGINAL_EXCERPT
    size = ((240, 135) if director else (192, 108)) if original else ((225, 400) if director else (117, 208))
    video.setAspectRatioMode(Qt.KeepAspectRatio)
    video.setFixedSize(*size)

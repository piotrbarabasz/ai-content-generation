"""Actions for the selected immutable project video render."""

from hashlib import sha256
from pathlib import Path

from PySide6.QtCore import QIODevice, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)


class ExportPanel(QWidget):
    def __init__(self, parent=None, *, render_resolver=None):
        super().__init__(parent)
        self.session = self.store = self.index = None
        self.render_media = render_resolver
        self.manifest = None
        root = QVBoxLayout(self)
        self.state = QLabel("No selected final render.")
        self.state.setWordWrap(True)
        root.addWidget(self.state)
        self.details = QLabel("")
        self.details.setWordWrap(True)
        root.addWidget(self.details)
        self.path = QLabel("")
        self.path.setWordWrap(True)
        self.path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.path)
        actions = QHBoxLayout()
        root.addLayout(actions)
        self.play_button = QPushButton("Play final video")
        self.folder_button = QPushButton("Open containing folder")
        self.copy_button = QPushButton("Save copy as…")
        for button in (self.play_button, self.folder_button, self.copy_button):
            actions.addWidget(button)
        self.play_button.clicked.connect(self.play)
        self.folder_button.clicked.connect(self.open_folder)
        self.copy_button.clicked.connect(self.save_copy_as)
        self._enable(False)

    def bind(self, session, *, index=None, store=None):
        self.session, self.index, self.store = session, index, store
        if session is not None and not hasattr(session, "repository") and self.index is None:
            self.session = None
        if self.session is not None and self.index is None:
            from app.jobs.repository import JobRepository
            from app.storage.local_store import LocalArtifactStore
            from app.storage.video_render import RenderResultIndex
            from app.storage.video_render import ProjectVideoRender
            self.index = RenderResultIndex(self.session.repository, JobRepository(self.session.repository))
            self.store = LocalArtifactStore(self.index.root, index=self.index)
            self.render_media = ProjectVideoRender(self.index, self.store)
        elif self.render_media is None and self.index is not None and self.store is not None:
            from app.storage.video_render import ProjectVideoRender
            self.render_media = ProjectVideoRender(self.index, self.store)
        self.refresh()

    def refresh(self):
        self.manifest = None
        if self.session is None or self.index is None or self.store is None:
            self.state.setText("No project is open.")
            self.details.clear()
            self.path.clear()
            self._enable(False)
            return None
        selected_id = self.index.selected().get("project:video_render")
        if selected_id is None:
            self.state.setText("No selected final render.")
            self.details.clear()
            self.path.clear()
            self._enable(False)
            return None
        try:
            self.manifest = self.render_media.selected()
        except Exception as exc:
            self.state.setText(f"Selected final render failed verification: {exc}")
            self.details.clear()
            self.path.clear()
            self._enable(False)
            return None
        if self.manifest is None:
            self.state.setText("Selected final render is unavailable in the artifact catalog.")
            self.details.clear()
            self.path.clear()
            self._enable(False)
            return None
        try:
            evidence = self.manifest.metadata.get("render", {})
            duration = evidence.get("video_duration", [0, 1])
            seconds = duration[0] / duration[1] if isinstance(duration, list) and duration[1] else 0
            self.state.setText("Final video ready" if self.render_media.motion_current(self.manifest) else
                               "Retained final video uses older motion settings. Rebuild to apply current motion.")
            self.details.setText(f"Duration {self._clock(seconds)} · {evidence.get('width', '?')}×{evidence.get('height', '?')} · "
                                 f"{evidence.get('video_codec', 'video')}/{evidence.get('audio_codec', 'audio')} · "
                                 f"{evidence.get('profile', 'MP4')}")
            self.path.setText(str(self._path()))
            self._enable(True)
        except Exception as exc:
            self.state.setText(f"Selected final render failed verification: {exc}")
            self.details.clear()
            self.path.clear()
            self._enable(False)
        return self.manifest

    def _path(self):
        from app.storage.paths import contained_path
        return contained_path(self.store.root, self.manifest.storage_key)

    def _validate_source(self):
        current = self.render_media.selected(self.manifest.artifact_id, verify_bytes=True)
        if current != self.manifest:
            raise ValueError("the selected render changed")
        return self._path()

    def play(self):
        if self.manifest is None:
            return False
        path = self._source_for_action()
        if path is None:
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_folder(self):
        if self.manifest is None:
            return False
        path = self._source_for_action()
        if path is None:
            return False
        path = path.parent
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def save_copy_as(self):
        if self.manifest is None:
            return None
        source = self._source_for_action()
        if source is None:
            return None
        suggested = self.manifest.name if self.manifest.name.lower().endswith(".mp4") else "final-video.mp4"
        destination, _ = QFileDialog.getSaveFileName(self, "Save a copy of the final video", suggested,
                                                      "MP4 video (*.mp4)")
        if not destination:
            return None
        destination = Path(destination)
        if destination.suffix.lower() != ".mp4":
            destination = destination.with_suffix(destination.suffix + ".mp4" if destination.suffix else ".mp4")
        from PySide6.QtCore import QSaveFile
        output = QSaveFile(str(destination))
        if not output.open(QIODevice.WriteOnly):
            self.state.setText(output.errorString())
            return None
        try:
            digest, size = sha256(), 0
            current = self.render_media.selected(self.manifest.artifact_id, verify_bytes=True)
            if current != self.manifest:
                raise ValueError("The selected render changed before the copy started.")
            with self.store.open_artifact_id(self.manifest.artifact_id) as stream:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    size += len(block)
                    written = output.write(block)
                    if written != len(block):
                        raise OSError(output.errorString() or "Short write while copying final video.")
            if digest.hexdigest() != self.manifest.checksum or size != self.manifest.size_bytes:
                raise ValueError("Source artifact changed while it was copied.")
            if not output.commit():
                raise OSError(output.errorString() or "Could not complete export copy.")
            self.state.setText(f"Final video copy saved: {destination}")
            return destination
        except Exception as exc:
            output.cancelWriting()
            self.state.setText(f"Could not save final video copy: {exc}")
            return None

    def _enable(self, enabled):
        for button in (self.play_button, self.folder_button, self.copy_button):
            button.setEnabled(enabled)

    def _source_for_action(self):
        try:
            return self._validate_source()
        except Exception as exc:
            self.state.setText(f"Selected final render failed verification: {exc}")
            self._enable(False)
            return None

    @staticmethod
    def _clock(seconds):
        total = int(round(float(seconds)))
        return f"{total // 60}:{total % 60:02d}"

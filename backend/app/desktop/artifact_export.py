"""Shared immutable-media copy action for branding and delivery exports."""

from hashlib import sha256
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile
from PySide6.QtWidgets import QFileDialog


def copy_artifact(parent, store, manifest, *, title, suggested, revalidate):
    destination, _ = QFileDialog.getSaveFileName(parent, title, suggested, "MP4 video (*.mp4)")
    if not destination:
        return None
    path = Path(destination)
    if path.suffix.lower() != ".mp4":
        path = path.with_suffix(".mp4")
    try:
        path.resolve().relative_to(store.root.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("Save exported copies outside the immutable artifact directory.")
    if revalidate() != manifest:
        raise ValueError("The selected media changed while choosing an export destination.")
    output = QSaveFile(str(path))
    if not output.open(QIODevice.WriteOnly):
        raise OSError(output.errorString())
    try:
        digest, size = sha256(), 0
        with store.open_artifact_id(manifest.artifact_id) as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
                size += len(block)
                if output.write(block) != len(block):
                    raise OSError("Short write copying verified media.")
        if digest.hexdigest() != manifest.checksum or size != manifest.size_bytes:
            raise ValueError("Source media changed while copying.")
        if not output.commit():
            raise OSError(output.errorString())
        return path
    except Exception:
        output.cancelWriting()
        raise

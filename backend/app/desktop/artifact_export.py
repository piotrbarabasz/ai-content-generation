"""Shared immutable-media copy action for branding and delivery exports."""

from hashlib import sha256
from pathlib import Path
import inspect
import os
import tempfile

from PySide6.QtCore import QIODevice, QSaveFile
from PySide6.QtWidgets import QFileDialog
from app.runtime.media_worker import media_worker, check_canceled


_active_exports = set()


async def copy_artifact_async(parent, store, manifest, *, title, suggested, revalidate,
                              suffix='.mp4', file_filter='MP4 video (*.mp4)',
                              canceled=lambda:False, progress=lambda *_:None):
    """Choose on Qt; transfer/verify privately in a worker; publish after the gate."""
    destination,_=QFileDialog.getSaveFileName(parent,title,suggested,file_filter)
    if not destination:return None
    path=Path(destination)
    if path.suffix.lower()!=suffix:path=path.with_suffix(suffix)
    path=path.resolve()
    try:path.relative_to(store.root.resolve())
    except ValueError:pass
    else:raise ValueError('Save exported copies outside the immutable artifact directory.')
    if path in _active_exports:raise ValueError('An export to this destination is already running.')
    if store.manifest_by_id(manifest.artifact_id)!=manifest:raise ValueError('Export artifact is missing or changed.')
    source=store._artifact_path(manifest.storage_key)
    temporary={}
    async def verify():
        current=revalidate()
        if inspect.isawaitable(current):current=await current
        if current!=manifest:raise ValueError('Selected media changed before saving; choose the current output.')
        check_canceled(canceled)
    _active_exports.add(path)
    try:
        progress('Checking source')
        await verify()
        progress('Saving')
        def transfer(stop):
            with tempfile.NamedTemporaryFile(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent,delete=False) as output:
                temporary['path']=Path(output.name)
                digest,size=sha256(),0
                with source.open('rb') as stream:
                    while block:=stream.read(1024*1024):
                        check_canceled(stop)
                        if output.write(block)!=len(block):raise OSError('Incomplete export write; check free disk space.')
                        digest.update(block);size+=len(block)
                if (digest.hexdigest(),size)!=(manifest.checksum,manifest.size_bytes):raise ValueError('Source media changed while copying.')
                output.flush();os.fsync(output.fileno())
            # Independently read the destination before replacing an existing copy.
            digest,size=sha256(),0
            with temporary['path'].open('rb') as stream:
                while block:=stream.read(1024*1024):
                    check_canceled(stop);digest.update(block);size+=len(block)
            if (digest.hexdigest(),size)!=(manifest.checksum,manifest.size_bytes):raise ValueError('Destination verification failed; previous copy retained.')
            stat=temporary['path'].stat()
            return stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns
        signature=await media_worker(transfer,canceled=canceled)
        progress('Verifying')
        await verify()
        stat=temporary['path'].stat()
        if (stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)!=signature:raise ValueError('Destination changed before saving; previous copy retained.')
        os.replace(temporary['path'],path)
        progress('Ready')
        return path
    finally:
        _active_exports.discard(path)
        if 'path' in temporary:temporary['path'].unlink(missing_ok=True)


def copy_artifact(parent, store, manifest, *, title, suggested, revalidate,
                  suffix=".mp4", file_filter="MP4 video (*.mp4)"):
    destination, _ = QFileDialog.getSaveFileName(parent, title, suggested, file_filter)
    if not destination:
        return None
    path = Path(destination)
    if path.suffix.lower() != suffix:
        path = path.with_suffix(suffix)
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

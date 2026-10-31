"""Cooperative file workers; cancellation drains the worker before returning."""
import asyncio
from threading import Event
from hashlib import sha256
import errno

from app.runtime.media_process import RenderCanceled


async def media_worker(operation, *, canceled=lambda: False):
    stop = Event()
    if canceled():
        raise RenderCanceled("Canceled before media preparation.")
    task = asyncio.create_task(asyncio.to_thread(operation, stop.is_set))
    try:
        while not task.done():
            if canceled():
                stop.set()
            await asyncio.sleep(.02)
        result = await task
        if canceled():
            raise RenderCanceled("Canceled; previous verified outputs retained.")
        return result
    except asyncio.CancelledError:
        stop.set()
        try:
            await asyncio.shield(task)
        except Exception:
            pass
        raise


def check_canceled(canceled):
    if canceled():
        raise RenderCanceled("Canceled during media preparation; previous outputs retained.")


def file_checksum(path, canceled=lambda:False):
    digest=sha256()
    with open(path,'rb') as stream:
        while block:=stream.read(1024*1024):
            check_canceled(canceled);digest.update(block)
    return digest.hexdigest()


def media_error_text(error):
    message=str(error) or type(error).__name__
    if isinstance(error,FileNotFoundError):
        return 'Media is missing. Restore the source or retained artifact, then refresh. '+message
    if isinstance(error,OSError) and error.errno==errno.ENOSPC:
        return 'Not enough free disk space. Free space and try again; previous outputs are retained. '+message
    if isinstance(error,PermissionError):
        return 'Cannot access media. Check folder permissions or choose another destination. '+message
    return message

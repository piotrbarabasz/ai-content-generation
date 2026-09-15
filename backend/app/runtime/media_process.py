"""Async native media process ownership, bounded pipes and cooperative cancellation."""

import asyncio
from contextlib import suppress
import math
import os
import subprocess


class RenderCanceled(RuntimeError):
    pass


class MediaProcess:
    def __init__(self, *, timeout=600):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Media process deadline must be finite and positive.")
        self.timeout = timeout

    async def run(self, command, *, cwd, canceled=lambda: False, on_line=lambda _: None):
        if canceled():
            raise RenderCanceled("Render canceled before process launch.")
        process = await asyncio.create_subprocess_exec(*map(str, command), cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        owned, tasks, tail, output = None, [], bytearray(), bytearray()

        async def stdout():
            while line := await process.stdout.readline():
                output.extend(line)
                if len(output) > 1024 * 1024:
                    raise ValueError("Media process stdout exceeded 1 MiB.")
                on_line(line.decode("utf-8", errors="replace").strip())

        async def stderr():
            while block := await process.stderr.read(4096):
                tail.extend(block)
                del tail[:-65536]

        async def watch_cancel():
            while not canceled():
                await asyncio.sleep(0.02)
            raise RenderCanceled("Render canceled during process execution.")

        try:
            if os.name == "nt":
                from .windows_job import WindowsJob
                owned = WindowsJob(process.pid)
            pipes = [asyncio.create_task(stdout()), asyncio.create_task(stderr()), asyncio.create_task(process.wait())]
            tasks.extend(pipes)

            async def finish():
                await asyncio.gather(*pipes)

            completed = asyncio.create_task(finish())
            cancel = asyncio.create_task(watch_cancel())
            tasks.extend((completed, cancel))
            done, _ = await asyncio.wait((completed, cancel), timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise TimeoutError("Media process exceeded its execution deadline.")
            if cancel in done:
                cancel.result()
            await completed
            if canceled():
                raise RenderCanceled("Render canceled at process completion.")
            if process.returncode != 0:
                raise RuntimeError(f"Media process exited {process.returncode}: {tail.decode('utf-8', errors='replace')}")
            return bytes(output)
        finally:
            if owned is not None:
                owned.close()
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except asyncio.TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await asyncio.wait_for(process.wait(), 5)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

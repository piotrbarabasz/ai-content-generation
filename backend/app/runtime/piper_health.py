"""Fixed, bounded private-worker probe. No provider imports in the application."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
import os
import subprocess

from .native_piper import system_directory
from .profiles import HealthCheck, HealthObservation, HealthStatus, ProfileHealth
from .protocol import check_identity, encode_frame, message, read_message_async


def private_environment(root):
    # D007 removes PYTHON* and VIRTUAL_ENV as well. The embedded ._pth disables
    # registry, user site, sitecustomize and current-directory module discovery.
    return {"PATH": str(system_directory()), "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1", "TEMP": str(root / "temp"),
            "TMP": str(root / "temp")}


async def _probe(root, profile, timeout):
    from .windows_job import WindowsJob

    process = None
    containment = None
    observed = []
    stage = HealthCheck.WORKER_PROTOCOL
    stderr_task = None
    tail = bytearray()

    async def logs():
        while chunk := await process.stderr.read(8192):
            tail.extend(chunk)
            del tail[:-8192]

    try:
        async with asyncio.timeout(timeout):
            env = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith("PYTHON") and k.upper() != "VIRTUAL_ENV"}
            env.update(private_environment(root))
            process = await asyncio.create_subprocess_exec(
                str(root / "python.exe"), "-I", "-B", "-u",
                str(root / "worker/app/runtime/piper_worker.py"), cwd=root, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            containment = WindowsJob(process.pid)
            stderr_task = asyncio.create_task(logs())

            async def send(kind, payload=None):
                process.stdin.write(encode_frame(message(kind, "runtime-health", "probe", payload)))
                await process.stdin.drain()

            async def receive():
                return check_identity(await read_message_async(process.stdout), "runtime-health", "probe")

            await send("hello")
            ready = await receive()
            if ready["type"] != "ready" or ready["payload"]["pid"] != process.pid:
                raise ValueError("Invalid private worker handshake.")
            observed.append(HealthObservation(stage, True, "Owned worker completed protocol v1 handshake."))
            await send("run", {"job": {"id": "runtime-health", "request": {"operation": "runtime.health"}}})
            for stage in (HealthCheck.INTERPRETER, HealthCheck.PACKAGES, HealthCheck.CPU_BACKEND):
                event = await receive()
                if event["type"] != "progress" or event["payload"] != {"phase": stage.value, "completed": 1, "total": 1}:
                    raise ValueError("Private runtime check failed: " + str(event["payload"])[:2048])
                observed.append(HealthObservation(stage, True, "Private worker verified " + stage.value + "."))
            stage = HealthCheck.WORKER_PROTOCOL
            result = await receive()
            if result["type"] != "completed" or result["payload"] != {"artifact_ids": []}:
                raise ValueError("Private probe did not complete.")
            if await read_message_async(process.stdout) is not None or await process.wait() != 0:
                raise ValueError("Private probe did not exit cleanly.")
            await stderr_task
        return ProfileHealth(profile.profile_id, profile.fingerprint, HealthStatus.READY,
                             datetime.now(UTC), tuple(observed))
    except (OSError, ValueError, TimeoutError) as exc:
        observed = [o for o in observed if o.check != stage]
        observed.append(HealthObservation(stage, False, (str(exc) or type(exc).__name__)[:4096]))
        return ProfileHealth(profile.profile_id, profile.fingerprint, HealthStatus.FAILED,
                             datetime.now(UTC), tuple(observed))
    finally:
        if containment:
            containment.close()
        if process:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            # Drain before joining: a full stdout pipe can otherwise hold the
            # asyncio subprocess transport open even after Windows killed it.
            async def drain():
                while await process.stdout.read(8192):
                    pass
            await asyncio.wait_for(drain(), 5)
            await asyncio.wait_for(process.wait(), 5)
            process.stdin.close()
            with suppress(BrokenPipeError, ConnectionResetError):
                await process.stdin.wait_closed()
        if stderr_task:
            await asyncio.wait_for(stderr_task, 5)


def probe_runtime(root, profile, *, timeout=60):
    """Blocking provisioning API; composition runs it off the UI/event-loop thread."""
    return asyncio.run(_probe(root, profile, timeout))

"""Async one-attempt supervision; queue writes stay on its owning event-loop thread."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import math
import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Callable

from app.domain.generation_job import AttemptStatus, JobAttempt, JobProgress
from app.jobs.coordinator import JobCoordinator
from .protocol import (
    MAX_FRAME_BYTES, ProtocolError, check_identity, encode_frame, message, read_message_async,
)
from .resources import APPLICATION_GPU_RESOURCES, DeviceDecision, _device


logger = logging.getLogger("aics.pipeline")
TRANSIENT_WORKER_CODES = frozenset({"WORKER_HANDSHAKE_TIMEOUT", "WORKER_EXECUTION_TIMEOUT",
                                    "WORKER_TERMINAL_EOF_TIMEOUT", "WORKER_EXIT_TIMEOUT"})
PHASES = frozenset({"PROCESS_START", "RUNTIME_INITIALIZATION", "HANDSHAKE", "MODEL_LOADING",
                   "SYNTHESIS", "FINAL_ARTIFACT_ASSEMBLY", "TERMINAL_MESSAGE", "STDOUT_EOF",
                   "PROCESS_EXIT", "CLEANUP", "PUBLICATION"})


class WorkerPhaseTimeout(TimeoutError):
    def __init__(self, code, phase, operation):
        self.code, self.phase = code, phase
        super().__init__(f"{code}: phase={phase} operation={operation}")


@dataclass(frozen=True)
class WorkerLaunch:
    """Trusted composition paths, never an executable/argument from a job snapshot.

    Supply the actual Python interpreter, not a Windows venv redirector, so the
    owned PID is the worker. A packaged entry point accepts no command arguments.
    """

    executable: Path
    entrypoint: Path | None = None
    environment: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None

    def command(self):
        executable = self.executable.expanduser().resolve(strict=True)
        if not executable.is_file() or os.name == "nt" and executable.suffix.lower() != ".exe":
            raise ValueError("Worker executable must be a native executable file.")
        if self.entrypoint is None:
            return [str(executable)]
        entry = self.entrypoint.expanduser().resolve(strict=True)
        if not entry.is_file() or entry.suffix != ".py":
            raise ValueError("Python worker requires a trusted script entry point.")
        return [str(executable), "-I", "-u", str(entry)]


@dataclass(frozen=True)
class WorkerLimits:
    handshake: float = 5.0
    execution: float = 120.0
    cancel_grace: float = 2.0
    exit_grace: float = 2.0
    reap: float = 5.0
    stderr_bytes: int = 64 * 1024

    def __post_init__(self):
        for name in ("handshake", "execution", "cancel_grace", "exit_grace", "reap"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("Worker deadlines must be finite positive seconds.")
        if type(self.stderr_bytes) is not int or not 0 < self.stderr_bytes <= 1024 * 1024:
            raise ValueError("Worker stderr tail must be bounded to at most 1 MiB.")


@dataclass(frozen=True)
class WorkerRunResult:
    attempt: JobAttempt
    pid: int | None
    returncode: int | None
    stderr_tail: str
    device_identity: dict = field(default_factory=dict)
    phase_durations: dict[str, float] = field(default_factory=dict)
    cleanup_confirmed: bool = False
    terminal_outcome: str | None = None

    @property
    def retryable(self):
        return (self.cleanup_confirmed and self.returncode is not None and self.terminal_outcome in (None, "completed")
                and self.attempt.status == AttemptStatus.FAILED
                and self.attempt.error.split(":", 1)[0] in TRANSIENT_WORKER_CODES)


class WorkerSupervisor:
    """One child per invocation; caller schedules this coroutine outside UI work.

    No queue polling loop, automatic retry, GPU scheduler or active selection.
    Always await run_next/close before closing the D003 session.
    """

    def __init__(self, coordinator: JobCoordinator, launch: WorkerLaunch, *, limits=WorkerLimits(),
                 completion_handler: Callable[[JobAttempt, tuple[str, ...]], object] | None = None,
                 device: DeviceDecision | None = None, resources=None, reservation_device=None):
        # Trusted preflight composition reserves a device without claiming it has
        # passed health. Synthesis still requires its separately pinned decision.
        if reservation_device is not None:
            _device(reservation_device)
            if reservation_device == "cpu" or device is not None:
                raise ValueError("Use either a GPU preflight reservation or a tested device decision.")
        self.coordinator = coordinator
        self.launch = launch
        self.limits = limits
        # Trusted coordinator-side composition validates output bytes and publishes
        # via D040. Worker references alone cannot authorize editorial selection.
        self.completion_handler = completion_handler
        self._task = None
        self._claim = None
        self.process = None
        self._started = None
        self._process_job = None
        self.device = device
        self.reservation_device = device.effective if device is not None else reservation_device
        self.resources = APPLICATION_GPU_RESOURCES if resources is None else resources
        self._resource_lease = None
        self._unresolved_claim = None
        self._phase_name = None
        self._phase_at = None
        self._phase_durations = {}

    def _enter_phase(self, phase):
        if phase is not None and phase not in PHASES:
            raise ValueError("Unknown worker lifecycle phase.")
        now = asyncio.get_running_loop().time()
        if self._phase_name == phase:
            return
        if self._phase_name is not None:
            elapsed = now - self._phase_at
            self._phase_durations[self._phase_name] = self._phase_durations.get(self._phase_name, 0.0) + elapsed
            logger.info("[AICS][WORKER][PHASE] job=%s attempt=%s pid=%s phase=%s elapsed_seconds=%.6f",
                        *self._phase_identity, self.process.pid if self.process else None,
                        self._phase_name, elapsed)
        self._phase_name, self._phase_at = phase, now

    async def _deadline(self, operation, seconds, code, name):
        try:
            return await asyncio.wait_for(operation, seconds)
        except asyncio.TimeoutError as exc:
            raise WorkerPhaseTimeout(code, self._phase_name, name) from exc

    def retry_oom(self, result: WorkerRunResult):
        """Explicit one-retry policy; never mutate a job or substitute devices."""
        if self.device is None or self.device.effective == "cpu" or self._resource_lease is not None:
            raise ValueError("OOM recovery requires a cleaned-up managed GPU worker.")
        previous = self.coordinator.repository.get_attempt(result.attempt.id)
        job = self.coordinator.repository.get_job(previous.job_id)
        self.device.validate(job.request)
        attempts = self.coordinator.repository.attempts(job.id)
        if (previous != result.attempt or previous != attempts[-1]
                or previous.status != AttemptStatus.FAILED or not previous.error.startswith("gpu_oom:")
                or result.returncode is None or result.device_identity != self.device.to_payload()
                or sum(a.error.startswith("gpu_oom:") for a in attempts) != 1):
            raise ValueError("Only the first confirmed GPU OOM permits one explicit retry.")
        return self.coordinator.retry(job.id)

    def retry_transient(self, result: WorkerRunResult):
        """Reuse the exact request/workspace only after confirmed process cleanup."""
        previous = self.coordinator.repository.get_attempt(result.attempt.id)
        if self.device is not None:
            self.device.validate(self.coordinator.repository.get_job(previous.job_id).request)
            if result.device_identity != self.device.to_payload():
                raise ValueError("Transient retry cannot change the pinned device identity.")
        if (not result.retryable or self._resource_lease is not None or self._unresolved_claim is not None
                or self.process is None or self.process.pid != result.pid
                or self.process.returncode != result.returncode or previous != result.attempt
                or self.coordinator.repository.attempts(previous.job_id)[-1] != previous):
            raise ValueError("Transient retry requires the latest failed attempt and confirmed worker cleanup.")
        return self.coordinator.retry(previous.job_id)

    async def unload(self):
        """Unload through process exit; also retry previously uncertain cleanup."""
        await self.close()
        if self._resource_lease is not None:
            await self._cleanup([], None)
            self.resources.release(self._resource_lease)
            self._resource_lease = None
        if self._unresolved_claim is not None:
            claim = self._unresolved_claim
            current = self.coordinator.repository.get_attempt(claim.id)
            if current.status == AttemptStatus.RUNNING:
                if current.cancel_requested:
                    self.coordinator.acknowledge_cancel(claim)
                else:
                    self.coordinator.fail(claim, "worker_cleanup: recovered after uncertain process cleanup")
            self._unresolved_claim = None

    def request_cancel(self):
        if self._claim is not None:
            return self.coordinator.cancel(self._claim.id)
        return None

    async def close(self):
        if self._task is not None:
            self.request_cancel()
            await asyncio.shield(self._task)

    async def _send(self, value):
        self.process.stdin.write(encode_frame(value))
        await self._deadline(self.process.stdin.drain(), self.limits.handshake,
                             "WORKER_HANDSHAKE_TIMEOUT", "send_" + value["type"])

    async def _logs(self, tail):
        while chunk := await self.process.stderr.read(4096):
            tail.extend(chunk)
            if len(tail) > self.limits.stderr_bytes:
                del tail[:-self.limits.stderr_bytes]

    async def _discard_stdout(self):
        # After stopping protocol parsing, drain bounded chunks so a paused
        # StreamReader can observe EOF and close Windows pipe transports.
        while await self.process.stdout.read(4096):
            pass

    async def _cancellation(self, claim):
        while True:
            current = self.coordinator.repository.get_attempt(claim.id)
            if current.cancel_requested:
                return
            await asyncio.sleep(0.02)

    async def _conversation(self, job, claim):
        self._enter_phase("HANDSHAKE")
        await self._send(message("hello", job.id, claim.id))
        async def receive_ready():
            while True:
                value = check_identity(await read_message_async(self.process.stdout), job.id, claim.id)
                if value["type"] != "progress":
                    return value
                if value["payload"] != {"phase": "RUNTIME_INITIALIZATION", "completed": None, "total": None}:
                    raise ProtocolError("Unexpected initialization event.")
                self._enter_phase("RUNTIME_INITIALIZATION")
                self.coordinator.progress(claim, JobProgress(**value["payload"]))
                await asyncio.sleep(0)
        ready = await self._deadline(receive_ready(), self.limits.handshake,
                                     "WORKER_HANDSHAKE_TIMEOUT", "receive_ready")
        if ready["type"] != "ready" or ready["payload"]["pid"] != self.process.pid:
            raise ProtocolError("Expected handshake from the directly owned worker PID.")
        self._enter_phase("HANDSHAKE")
        await self._send(message("run", job.id, claim.id, {"job": job.to_payload()}))
        self._started.set()
        self._enter_phase("SYNTHESIS")
        event = await self._deadline(self._events(job, claim), self.limits.execution,
                                     "WORKER_EXECUTION_TIMEOUT", "receive_terminal")
        self._terminal_outcome = event["type"]
        self._enter_phase("TERMINAL_MESSAGE")
        self.process.stdin.close()
        with suppress(BrokenPipeError, ConnectionResetError):
            await self._deadline(self.process.stdin.wait_closed(), self.limits.exit_grace,
                                 "WORKER_TERMINAL_EOF_TIMEOUT", "close_stdin")
        # No publication from a terminal frame alone: still require EOF and clean exit.
        self._enter_phase("STDOUT_EOF")
        extra = await self._deadline(read_message_async(self.process.stdout), self.limits.exit_grace,
                                     "WORKER_TERMINAL_EOF_TIMEOUT", "receive_eof")
        if extra is not None:
            raise ProtocolError("Worker sent an event after its terminal outcome.")
        self._enter_phase("PROCESS_EXIT")
        code = await self._deadline(self.process.wait(), self.limits.exit_grace,
                                    "WORKER_EXIT_TIMEOUT", "wait_exit")
        if code != 0:
            raise ProtocolError("Worker exited nonzero after reporting an outcome.")
        return event

    async def _events(self, job, claim):
        while True:
            event = check_identity(await read_message_async(self.process.stdout), job.id, claim.id)
            if event["type"] == "progress":
                phase = event["payload"]["phase"]
                if phase in {"RUNTIME_INITIALIZATION", "MODEL_LOADING", "SYNTHESIS", "FINAL_ARTIFACT_ASSEMBLY"}:
                    self._enter_phase(phase)
                self.coordinator.progress(claim, JobProgress(**event["payload"]))
                await asyncio.sleep(0)  # A stdout flood must not starve cancel/deadline tasks.
            elif event["type"] in ("completed", "failed", "canceled"):
                return event
            else:
                raise ProtocolError("Unexpected worker event ordering.")

    async def _stop(self):
        if self.process is None:
            return
        if self._process_job is not None:
            self._process_job.close()
            self._process_job = None
        if self.process.returncode is None:
            with suppress(ProcessLookupError):
                self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), self.limits.exit_grace)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    self.process.kill()
                await asyncio.wait_for(self.process.wait(), self.limits.reap)
        if self.process.stdin is not None:
            self.process.stdin.close()
            with suppress(BrokenPipeError, ConnectionResetError):
                await asyncio.wait_for(self.process.stdin.wait_closed(), self.limits.reap)

    async def _cleanup(self, tasks, logs):
        control_tasks = [task for task in tasks if task is not logs]
        for task in control_tasks:
            if not task.done():
                task.cancel()
        if control_tasks:
            await asyncio.gather(*control_tasks, return_exceptions=True)
        drain = asyncio.create_task(self._discard_stdout()) if self.process is not None else None
        if drain is not None:
            tasks.append(drain)
        try:
            await self._stop()
            if drain is not None:
                await asyncio.wait_for(asyncio.shield(drain), self.limits.reap)
            if logs is not None:
                await asyncio.wait_for(asyncio.shield(logs), self.limits.reap)
        except asyncio.TimeoutError as exc:
            raise WorkerPhaseTimeout("WORKER_CLEANUP_TIMEOUT", "CLEANUP", "reap_process_and_pipes") from exc
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def run_next(self, owner="local-worker") -> WorkerRunResult | None:
        if self._task is not None:
            raise RuntimeError("This supervisor already owns an attempt.")
        if self._resource_lease is not None:
            raise RuntimeError("GPU cleanup is unresolved; unload the previous worker first.")
        if self.reservation_device is not None and self.reservation_device != "cpu":
            lease = self.resources.acquire(owner, self.reservation_device)
            if lease is None:
                return None  # Contention must leave durable work queued.
            self._resource_lease = lease
        self.process = None
        try:
            claim = self.coordinator.claim_next(owner)
        except BaseException:
            if self._resource_lease is not None:
                self.resources.release(self._resource_lease)
                self._resource_lease = None
            raise
        if claim is None:
            if self._resource_lease is not None:
                self.resources.release(self._resource_lease)
                self._resource_lease = None
            return None
        self._task, self._claim = asyncio.current_task(), claim
        self._phase_identity = (claim.job_id, claim.id)
        self._phase_name, self._phase_at, self._phase_durations = None, None, {}
        self._terminal_outcome = None
        self._enter_phase("PROCESS_START")
        self._started = asyncio.Event()
        tasks, tail = [], bytearray()
        event, error, canceled, external_cancel = None, None, False, False
        logs = None
        try:
            job = self.coordinator.repository.get_job(claim.job_id)
            if self.device is not None:
                self.device.validate(job.request)
            elif "runtime_device" in json.loads(job.request.effective_identity_json):
                raise ValueError("A device-bound job requires explicit managed worker composition.")
            environment = {key: value for key, value in os.environ.items()
                           if not key.upper().startswith("PYTHON") and key.upper() != "VIRTUAL_ENV"}
            environment.update(self.launch.environment)
            self.process = await asyncio.create_subprocess_exec(
                *self.launch.command(), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=environment, cwd=self.launch.cwd,
                limit=MAX_FRAME_BYTES + 4,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if self._resource_lease is not None:
                self.resources.attach(self._resource_lease, self.process)
            if os.name == "nt":
                from .windows_job import WindowsJob
                self._process_job = WindowsJob(self.process.pid)
            logs = asyncio.create_task(self._logs(tail))
            conversation = asyncio.create_task(self._conversation(job, claim))
            cancellation = asyncio.create_task(self._cancellation(claim))
            tasks.extend((logs, conversation, cancellation))
            done, _ = await asyncio.wait((conversation, cancellation),
                                         return_when=asyncio.FIRST_COMPLETED)
            if cancellation in done:
                cancellation.result()
                canceled = True
                if self._started.is_set() and not conversation.done():
                    await self._send(message("cancel", job.id, claim.id))
                    with suppress(asyncio.TimeoutError, ProtocolError, BrokenPipeError, ConnectionResetError):
                        event = await asyncio.wait_for(asyncio.shield(conversation), self.limits.cancel_grace)
                # Before run, or after grace expires, cleanup confirms termination.
            elif conversation in done:
                event = conversation.result()
        except asyncio.CancelledError:
            external_cancel = True
            self.request_cancel()
            canceled = True
        except WorkerPhaseTimeout as exc:
            error = str(exc)
            logger.warning("[AICS][WORKER][TIMEOUT] job=%s attempt=%s pid=%s %s",
                           claim.job_id, claim.id, self.process.pid if self.process else None, error)
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            error = f"worker_error: {type(exc).__name__}: {str(exc)[:1024]}"
        finally:
            # No terminal queue outcome until the owned child has actually exited.
            self._enter_phase("CLEANUP")
            cleanup = asyncio.create_task(self._cleanup(tasks, logs))
            try:
                while True:
                    try:
                        await asyncio.shield(cleanup)
                        if self._resource_lease is not None:
                            self.resources.release(self._resource_lease)
                            self._resource_lease = None
                        break
                    except asyncio.CancelledError:
                        # Repeated shutdown requests cannot abandon pipe/process cleanup.
                        external_cancel, canceled = True, True
                        self.request_cancel()
            except BaseException as exc:
                self._enter_phase(None)
                logger.error("[AICS][WORKER][CLEANUP] job=%s attempt=%s pid=%s code=%s quarantine=%s",
                             claim.job_id, claim.id, self.process.pid if self.process else None,
                             getattr(exc, "code", "WORKER_CLEANUP_FAILED"), self._resource_lease is not None)
                raise
            finally:
                if self._resource_lease is not None:
                    self.resources.quarantine(self._resource_lease)
                    self._unresolved_claim = claim
                self._task, self._claim = None, None
        current = self.coordinator.repository.get_attempt(claim.id)
        canceled = canceled or current.cancel_requested
        if canceled:
            outcome = self.coordinator.acknowledge_cancel(claim)
        elif error is not None:
            outcome = self.coordinator.fail(claim, error)
        elif event is not None and event["type"] == "completed":
            self._enter_phase("PUBLICATION")
            try:
                if self.completion_handler is None:
                    outcome = self.coordinator.complete(claim, event["payload"]["artifact_ids"])
                else:
                    self.completion_handler(claim, tuple(event["payload"]["artifact_ids"]))
                    outcome = self.coordinator.repository.get_attempt(claim.id)
                    if outcome.status != AttemptStatus.COMPLETED:
                        raise ValueError("Completion handler did not durably publish the result.")
            except Exception as exc:
                # D004 cleanup can fail AFTER the D040 database commit. Preserve
                # that durable success; recovery will clean its journal on reopen.
                outcome = self.coordinator.repository.get_attempt(claim.id)
                if outcome.status == AttemptStatus.RUNNING:
                    outcome = self.coordinator.fail(claim, f"publication_error: {type(exc).__name__}: {str(exc)[:1024]}")
                elif outcome.status != AttemptStatus.COMPLETED:
                    raise
        elif event is not None and event["type"] == "failed":
            outcome = self.coordinator.fail(claim, f'{event["payload"]["code"]}: {event["payload"]["message"]}')
        else:
            outcome = self.coordinator.fail(claim, "worker_protocol: unsolicited cancellation or missing outcome")
        self._enter_phase(None)
        if external_cancel:
            raise asyncio.CancelledError
        return WorkerRunResult(outcome, self.process.pid if self.process else None,
                               self.process.returncode if self.process else None,
                               bytes(tail).decode("utf-8", errors="replace"),
                               self.device.to_payload() if self.device is not None else {},
                               dict(self._phase_durations), True, self._terminal_outcome)

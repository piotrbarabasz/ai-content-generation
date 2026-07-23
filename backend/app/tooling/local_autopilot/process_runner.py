"""Deterministic subprocess runner for the local autopilot."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[4]
MAX_STDOUT_LINES = 200
MAX_STDERR_LINES = 200
MAX_LINE_LENGTH = 400
WINDOWS_TASKKILL_TIMEOUT_SECONDS = 10
POSIX_TREE_TERMINATION_GRACE_SECONDS = 3.0
_SENSITIVE_PATTERN = re.compile(r"(?i)\b(token|secret|password|api[_-]?key)\b(\s*[:=]\s*)([^\s'\";]+)")
_BEARER_PATTERN = re.compile(r"(?i)\b(authorization\s*:\s*)bearer\s+[^\s'\";]+")


@dataclass(frozen=True)
class ProcessResult:
    command: tuple[str, ...]
    status: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool
    cancelled: bool
    stdout_lines: tuple[str, ...]
    stderr_lines: tuple[str, ...]
    output_truncated: bool
    process_tree_killed: bool
    pid: int | None


def _emit_stderr(message: str) -> None:
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


def redact_sensitive_text(value: str) -> str:
    value = _BEARER_PATTERN.sub(r"\1[REDACTED]", value)
    return _SENSITIVE_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)


def _redact_lines(lines: Sequence[str]) -> tuple[str, ...]:
    return tuple(redact_sensitive_text(line) for line in lines)


def _build_env(env_overrides: Mapping[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "TERM": "dumb",
            "PYTHONUNBUFFERED": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
    )
    if env_overrides:
        env.update({key: str(value) for key, value in env_overrides.items()})
    return env


def _mkstemp_path(suffix: str) -> tuple[int, Path]:
    fd, raw_path = tempfile.mkstemp(prefix="autopilot-process-", suffix=suffix)
    return fd, Path(raw_path)


def _cleanup_path(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _read_limited_lines(path: Path, *, limit: int) -> tuple[tuple[str, ...], bool]:
    if not path.is_file():
        return (), False
    lines: list[str] = []
    truncated = False
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        while len(lines) < limit:
            chunk = handle.readline(MAX_LINE_LENGTH + 1)
            if chunk == "":
                break
            line = chunk.rstrip("\r\n")
            if len(line) > MAX_LINE_LENGTH:
                truncated = True
                lines.append(line[:MAX_LINE_LENGTH])
            else:
                lines.append(line)
        if len(lines) == limit and handle.read(1) != "":
            truncated = True
    return tuple(lines), truncated


def _spawn_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    env_overrides: Mapping[str, str] | None,
    popen_factory: Callable[..., subprocess.Popen[bytes]],
) -> tuple[subprocess.Popen[bytes], Path, Path]:
    stdout_fd, stdout_path = _mkstemp_path(".stdout")
    stderr_fd, stderr_path = _mkstemp_path(".stderr")
    stdout_handle = os.fdopen(stdout_fd, "wb")
    stderr_handle = os.fdopen(stderr_fd, "wb")
    try:
        popen_kwargs: dict[str, object] = {
            "cwd": cwd,
            "shell": False,
            "stdout": stdout_handle,
            "stderr": stderr_handle,
            "env": _build_env(env_overrides),
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
        process = popen_factory(list(argv), **popen_kwargs)
    finally:
        stdout_handle.close()
        stderr_handle.close()
    return process, stdout_path, stderr_path


def _kill_windows_tree(
    process: subprocess.Popen[bytes],
    *,
    taskkill_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    if process.pid is None:
        return False
    taskkill_argv = ["taskkill", "/PID", str(process.pid), "/T", "/F"]
    try:
        result = taskkill_runner(
            taskkill_argv,
            cwd=ROOT,
            shell=False,
            timeout=WINDOWS_TASKKILL_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            env=_build_env(None),
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
        return False
    if result.returncode != 0:
        try:
            process.kill()
        except OSError:
            pass
        return False
    try:
        process.wait(timeout=WINDOWS_TASKKILL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        return False
    return True


def _kill_posix_tree(process: subprocess.Popen[bytes]) -> bool:
    if process.pid is None:
        return False
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
        return False
    try:
        process.wait(timeout=POSIX_TREE_TERMINATION_GRACE_SECONDS)
        return True
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
        return False
    try:
        process.wait(timeout=POSIX_TREE_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        return False
    return True


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    *,
    taskkill_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    if os.name == "nt":
        return _kill_windows_tree(process, taskkill_runner=taskkill_runner)
    return _kill_posix_tree(process)


def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    cancel_event: threading.Event | None = None,
    total_deadline: float | None = None,
    heartbeat_seconds: int = 30,
    env_overrides: Mapping[str, str] | None = None,
    popen_factory: Callable[..., subprocess.Popen[bytes]] | None = None,
    taskkill_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> ProcessResult:
    command = tuple(str(part) for part in argv)
    if not command:
        raise ValueError("argv must not be empty")
    if cancel_event is not None and cancel_event.is_set():
        result = ProcessResult(
            command=command,
            status="CANCELLED",
            exit_code=None,
            duration_ms=0,
            timed_out=False,
            cancelled=True,
            stdout_lines=(),
            stderr_lines=(),
            output_truncated=False,
            process_tree_killed=False,
            pid=None,
        )
        _emit_stderr(f"CANCELLED {Path(command[0]).name}")
        return result

    started_perf = time.perf_counter()
    started_monotonic = time.monotonic()
    deadline = started_monotonic + float(timeout_seconds)
    if total_deadline is not None:
        deadline = min(deadline, total_deadline)

    stdout_path: Path | None = None
    stderr_path: Path | None = None
    process: subprocess.Popen[bytes] | None = None
    pid: int | None = None
    timed_out = False
    cancelled = False
    process_tree_killed = False
    exit_code: int | None = None

    if total_deadline is not None and started_monotonic >= total_deadline:
        duration_ms = max(0, int((time.perf_counter() - started_perf) * 1000))
        result = ProcessResult(
            command=command,
            status="TIMEOUT",
            exit_code=None,
            duration_ms=duration_ms,
            timed_out=True,
            cancelled=False,
            stdout_lines=(),
            stderr_lines=(),
            output_truncated=False,
            process_tree_killed=False,
            pid=None,
        )
        _emit_stderr(f"TIMEOUT {Path(command[0]).name}")
        return result

    try:
        if popen_factory is None:
            popen_factory = subprocess.Popen
        if taskkill_runner is None:
            taskkill_runner = subprocess.run
        process, stdout_path, stderr_path = _spawn_process(
            command,
            cwd=cwd,
            env_overrides=env_overrides,
            popen_factory=popen_factory,
        )
        pid = process.pid
        _emit_stderr(f"START {Path(command[0]).name} pid={pid} timeout={timeout_seconds}s")

        next_heartbeat = started_monotonic + heartbeat_seconds if heartbeat_seconds > 0 else None
        while True:
            exit_code = process.poll()
            now = time.monotonic()
            if exit_code is not None:
                break
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                process_tree_killed = _terminate_process_tree(process, taskkill_runner=taskkill_runner)
                break
            if now >= deadline:
                timed_out = True
                process_tree_killed = _terminate_process_tree(process, taskkill_runner=taskkill_runner)
                break
            if next_heartbeat is not None and now >= next_heartbeat:
                elapsed_seconds = int(now - started_monotonic)
                _emit_stderr(f"HEARTBEAT {Path(command[0]).name} elapsed={elapsed_seconds}s pid={pid}")
                next_heartbeat += heartbeat_seconds
                continue
            sleep_for = 0.05
            if next_heartbeat is not None:
                sleep_for = min(sleep_for, max(0.0, next_heartbeat - now))
            sleep_for = min(sleep_for, max(0.0, deadline - now))
            time.sleep(max(0.01, sleep_for))

        if timed_out or cancelled:
            try:
                process.wait(timeout=WINDOWS_TASKKILL_TIMEOUT_SECONDS if os.name == "nt" else POSIX_TREE_TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
        else:
            exit_code = process.wait()

        duration_ms = max(0, int((time.perf_counter() - started_perf) * 1000))
        stdout_lines, stdout_truncated = _read_limited_lines(stdout_path, limit=MAX_STDOUT_LINES)
        stderr_lines, stderr_truncated = _read_limited_lines(stderr_path, limit=MAX_STDERR_LINES)
        stdout_lines = _redact_lines(stdout_lines)
        stderr_lines = _redact_lines(stderr_lines)
        output_truncated = stdout_truncated or stderr_truncated

        if timed_out:
            status = "TIMEOUT"
            exit_code = None
        elif cancelled:
            status = "CANCELLED"
            exit_code = None
        else:
            status = "PASS" if exit_code == 0 else "FAIL"

        result = ProcessResult(
            command=command,
            status=status,
            exit_code=exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out,
            cancelled=cancelled,
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines,
            output_truncated=output_truncated,
            process_tree_killed=process_tree_killed,
            pid=pid,
        )
        _emit_stderr(f"{status} {Path(command[0]).name} duration={duration_ms}ms")
        return result
    except FileNotFoundError:
        duration_ms = max(0, int((time.perf_counter() - started_perf) * 1000))
        result = ProcessResult(
            command=command,
            status="MISSING",
            exit_code=None,
            duration_ms=duration_ms,
            timed_out=False,
            cancelled=False,
            stdout_lines=(),
            stderr_lines=(),
            output_truncated=False,
            process_tree_killed=False,
            pid=None,
        )
        _emit_stderr(f"MISSING {Path(command[0]).name} duration={duration_ms}ms")
        return result
    except OSError:
        duration_ms = max(0, int((time.perf_counter() - started_perf) * 1000))
        result = ProcessResult(
            command=command,
            status="FAIL",
            exit_code=None,
            duration_ms=duration_ms,
            timed_out=False,
            cancelled=False,
            stdout_lines=(),
            stderr_lines=(),
            output_truncated=False,
            process_tree_killed=False,
            pid=None,
        )
        _emit_stderr(f"FAIL {Path(command[0]).name} duration={duration_ms}ms")
        return result
    finally:
        _cleanup_path(stdout_path)
        _cleanup_path(stderr_path)


__all__ = [
    "MAX_LINE_LENGTH",
    "MAX_STDERR_LINES",
    "MAX_STDOUT_LINES",
    "POSIX_TREE_TERMINATION_GRACE_SECONDS",
    "ProcessResult",
    "ROOT",
    "WINDOWS_TASKKILL_TIMEOUT_SECONDS",
    "redact_sensitive_text",
    "run_process",
]

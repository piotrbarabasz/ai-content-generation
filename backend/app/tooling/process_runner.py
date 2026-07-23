"""Deterministic subprocess runner with bounded output and tree shutdown."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[3]
MAX_STDOUT_LINES = 20
MAX_STDERR_LINES = 20
MAX_LINE_LENGTH = 300
WINDOWS_TASKKILL_TIMEOUT_SECONDS = 10
POSIX_TREE_TERMINATION_GRACE_SECONDS = 3.0


@dataclass(frozen=True)
class ProcessResult:
    command: tuple[str, ...]
    status: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool
    stdout_lines: tuple[str, ...]
    stderr_lines: tuple[str, ...]
    output_truncated: bool
    process_tree_killed: bool
    pid: int | None


def _command_name(argv: Sequence[str]) -> str:
    if len(argv) >= 3 and argv[1] == "-m":
        return argv[2]
    return Path(argv[0]).name if argv else "command"


def _emit_stderr(message: str) -> None:
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


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
    fd, raw_path = tempfile.mkstemp(prefix="process-runner-", suffix=suffix)
    return fd, Path(raw_path)


def _cleanup_path(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _read_limited_lines(path: Path, *, line_limit: int) -> tuple[tuple[str, ...], bool]:
    lines: list[str] = []
    truncated = False
    if not path.is_file():
        return tuple(lines), False
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        while len(lines) < line_limit:
            chunk = handle.readline(MAX_LINE_LENGTH + 1)
            if chunk == "":
                break
            line = chunk.rstrip("\r\n")
            if len(line) > MAX_LINE_LENGTH:
                truncated = True
                lines.append(line[:MAX_LINE_LENGTH])
                if not chunk.endswith(("\n", "\r")):
                    while True:
                        discard = handle.readline(MAX_LINE_LENGTH + 1)
                        if discard == "" or discard.endswith(("\n", "\r")):
                            break
            else:
                lines.append(line)
        if len(lines) == line_limit and handle.read(1) != "":
            truncated = True
    return tuple(lines), truncated


def _spawn_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    env_overrides: Mapping[str, str] | None,
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
        process = subprocess.Popen(list(argv), **popen_kwargs)
    finally:
        stdout_handle.close()
        stderr_handle.close()
    return process, stdout_path, stderr_path


def _kill_windows_tree(process: subprocess.Popen[bytes]) -> bool:
    if process.pid is None:
        return False
    taskkill_argv = ["taskkill", "/PID", str(process.pid), "/T", "/F"]
    try:
        result = subprocess.run(
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


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> bool:
    if os.name == "nt":
        return _kill_windows_tree(process)
    return _kill_posix_tree(process)


def _final_status_line(result: ProcessResult) -> str:
    duration = f"{result.duration_ms}ms"
    command_name = _command_name(result.command)
    if result.status == "PASS":
        return f"PASS {command_name} duration={duration}"
    if result.status == "TIMEOUT":
        tree_killed = "yes" if result.process_tree_killed else "no"
        return f"TIMEOUT {command_name} duration={duration} tree_killed={tree_killed}"
    if result.status == "MISSING":
        return f"MISSING {command_name} duration={duration}"
    exit_code = "None" if result.exit_code is None else str(result.exit_code)
    return f"FAIL {command_name} exit={exit_code} duration={duration}"


def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    total_deadline: float | None = None,
    heartbeat_seconds: int = 30,
    env_overrides: Mapping[str, str] | None = None,
) -> ProcessResult:
    command = tuple(str(part) for part in argv)
    if not command:
        raise ValueError("argv must not be empty")

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
    process_tree_killed = False
    exit_code: int | None = None
    status = "FAIL"

    if total_deadline is not None and started_monotonic >= total_deadline:
        duration_ms = max(0, int((time.perf_counter() - started_perf) * 1000))
        result = ProcessResult(
            command=command,
            status="TIMEOUT",
            exit_code=None,
            duration_ms=duration_ms,
            timed_out=True,
            stdout_lines=(),
            stderr_lines=(),
            output_truncated=False,
            process_tree_killed=False,
            pid=None,
        )
        _emit_stderr(_final_status_line(result))
        return result

    try:
        process, stdout_path, stderr_path = _spawn_process(command, cwd=cwd, env_overrides=env_overrides)
        pid = process.pid
        _emit_stderr(f"START {_command_name(command)} pid={pid} timeout={timeout_seconds}s")

        next_heartbeat = started_monotonic + heartbeat_seconds if heartbeat_seconds > 0 else None
        while True:
            exit_code = process.poll()
            now = time.monotonic()
            if exit_code is not None:
                break
            if now >= deadline:
                timed_out = True
                process_tree_killed = _terminate_process_tree(process)
                break
            if next_heartbeat is not None and now >= next_heartbeat:
                elapsed_seconds = int(now - started_monotonic)
                _emit_stderr(f"HEARTBEAT {_command_name(command)} elapsed={elapsed_seconds}s pid={pid}")
                next_heartbeat += heartbeat_seconds
                continue
            sleep_for = 0.05
            if next_heartbeat is not None:
                sleep_for = min(sleep_for, max(0.0, next_heartbeat - now))
            sleep_for = min(sleep_for, max(0.0, deadline - now))
            time.sleep(max(0.01, sleep_for))

        if timed_out:
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
        stdout_lines, stdout_truncated = _read_limited_lines(stdout_path, line_limit=MAX_STDOUT_LINES)
        stderr_lines, stderr_truncated = _read_limited_lines(stderr_path, line_limit=MAX_STDERR_LINES)
        output_truncated = stdout_truncated or stderr_truncated

        if timed_out:
            status = "TIMEOUT"
            exit_code = None
        else:
            status = "PASS" if exit_code == 0 else "FAIL"

        result = ProcessResult(
            command=command,
            status=status,
            exit_code=exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out,
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines,
            output_truncated=output_truncated,
            process_tree_killed=process_tree_killed,
            pid=pid,
        )
        _emit_stderr(_final_status_line(result))
        return result
    except FileNotFoundError:
        duration_ms = max(0, int((time.perf_counter() - started_perf) * 1000))
        result = ProcessResult(
            command=command,
            status="MISSING",
            exit_code=None,
            duration_ms=duration_ms,
            timed_out=False,
            stdout_lines=(),
            stderr_lines=(),
            output_truncated=False,
            process_tree_killed=False,
            pid=None,
        )
        _emit_stderr(_final_status_line(result))
        return result
    except OSError:
        duration_ms = max(0, int((time.perf_counter() - started_perf) * 1000))
        result = ProcessResult(
            command=command,
            status="FAIL",
            exit_code=None,
            duration_ms=duration_ms,
            timed_out=False,
            stdout_lines=(),
            stderr_lines=(),
            output_truncated=False,
            process_tree_killed=False,
            pid=None,
        )
        _emit_stderr(_final_status_line(result))
        return result
    finally:
        _cleanup_path(stdout_path)
        _cleanup_path(stderr_path)

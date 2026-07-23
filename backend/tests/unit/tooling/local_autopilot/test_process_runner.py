from __future__ import annotations

import subprocess
import threading
import time
import sys
from pathlib import Path

import pytest

from app.tooling.local_autopilot import process_runner as runner


class FakeProcess:
    def __init__(self, *, pid: int = 4321, poll_values: list[int | None] | None = None, final_returncode: int = 0) -> None:
        self.pid = pid
        self._poll_values = list(poll_values or [])
        self._final_returncode = final_returncode
        self.returncode: int | None = None
        self.wait_calls = 0
        self.kill_calls = 0

    def poll(self) -> int | None:
        if self._poll_values:
            value = self._poll_values.pop(0)
            if value is not None:
                self.returncode = value
            return value
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.returncode is None:
            self.returncode = self._final_returncode
        return self.returncode

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9


def _fake_popen_factory(
    calls: list[tuple[tuple[str, ...], dict[str, object]]],
    *,
    process: FakeProcess,
    stdout_text: str = "",
    stderr_text: str = "",
) -> callable:
    def fake_popen(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        stdout_handle = kwargs["stdout"]
        stderr_handle = kwargs["stderr"]
        if stdout_text:
            stdout_handle.write(stdout_text.encode("utf-8"))
        if stderr_text:
            stderr_handle.write(stderr_text.encode("utf-8"))
        stdout_handle.flush()
        stderr_handle.flush()
        return process

    return fake_popen


def _patch_monotonic(monkeypatch, values: list[float]) -> None:
    iterator = iter(values)
    last = values[-1]

    def fake_monotonic() -> float:
        nonlocal last
        try:
            last = next(iterator)
        except StopIteration:
            pass
        return last

    monkeypatch.setattr(runner.time, "monotonic", fake_monotonic)


def _patch_perf_counter(monkeypatch, values: list[float]) -> None:
    iterator = iter(values)
    last = values[-1]

    def fake_perf_counter() -> float:
        nonlocal last
        try:
            last = next(iterator)
        except StopIteration:
            pass
        return last

    monkeypatch.setattr(runner.time, "perf_counter", fake_perf_counter)


def test_run_process_redacts_sensitive_values(monkeypatch):
    calls = []
    process = FakeProcess(poll_values=[0], final_returncode=0)
    monkeypatch.setattr(
        runner.subprocess,
        "Popen",
        _fake_popen_factory(
            calls,
            process=process,
            stdout_text="token=abc password: secret authorization: Bearer xyz api_key=123\n",
            stderr_text="secret=hidden\n",
        ),
    )
    _patch_perf_counter(monkeypatch, [10.0, 10.050])
    _patch_monotonic(monkeypatch, [1.0, 1.0])

    result = runner.run_process(["git", "status"], cwd=runner.ROOT, timeout_seconds=5, heartbeat_seconds=0)

    assert result.status == "PASS"
    assert result.stdout_lines == ("token=[REDACTED] password: [REDACTED] authorization: [REDACTED] api_key=[REDACTED]",)
    assert result.stderr_lines == ("secret=[REDACTED]",)
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["cwd"] == runner.ROOT
    assert calls[0][1]["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_run_process_cancel_event_stops_without_spawn(monkeypatch):
    cancelled = threading.Event()
    cancelled.set()
    calls = []
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))  # pragma: no cover

    result = runner.run_process(["git", "status"], cwd=runner.ROOT, timeout_seconds=5, cancel_event=cancelled, heartbeat_seconds=0)

    assert result.status == "CANCELLED"
    assert result.cancelled is True
    assert result.pid is None
    assert calls == []


def test_run_process_cancels_during_poll(monkeypatch):
    calls = []
    cancel_event = threading.Event()
    process = FakeProcess(poll_values=[None, None], final_returncode=0)

    def fake_poll() -> int | None:
        cancel_event.set()
        return process._poll_values.pop(0) if process._poll_values else None

    process.poll = fake_poll  # type: ignore[assignment]
    monkeypatch.setattr(runner.subprocess, "Popen", _fake_popen_factory(calls, process=process))
    monkeypatch.setattr(runner, "_terminate_process_tree", lambda proc, taskkill_runner=None: True)
    _patch_perf_counter(monkeypatch, [20.0, 20.050])
    _patch_monotonic(monkeypatch, [2.0, 2.1, 2.1, 2.1, 2.1])

    result = runner.run_process(["tool"], cwd=runner.ROOT, timeout_seconds=5, cancel_event=cancel_event, heartbeat_seconds=0)

    assert result.status == "CANCELLED"
    assert result.cancelled is True
    assert result.process_tree_killed is True
    assert len(calls) == 1


def test_run_process_timeout_terminates_tree(monkeypatch):
    calls = []
    process = FakeProcess(poll_values=[None, None], final_returncode=0)
    terminate_calls = []
    monkeypatch.setattr(runner.subprocess, "Popen", _fake_popen_factory(calls, process=process))
    monkeypatch.setattr(runner, "_terminate_process_tree", lambda proc, taskkill_runner=None: terminate_calls.append(proc.pid) or True)
    _patch_perf_counter(monkeypatch, [30.0, 30.100])
    _patch_monotonic(monkeypatch, [3.0, 3.2, 3.4, 3.6, 4.1, 4.1])

    result = runner.run_process(["tool"], cwd=runner.ROOT, timeout_seconds=1, heartbeat_seconds=0)

    assert result.status == "TIMEOUT"
    assert result.timed_out is True
    assert result.process_tree_killed is True
    assert terminate_calls == [4321]
    assert len(calls) == 1


def test_windows_tree_kill_uses_taskkill(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Result()

    process = FakeProcess()
    assert runner._kill_windows_tree(process, taskkill_runner=fake_run) is True
    assert calls[0][0] == ["taskkill", "/PID", "4321", "/T", "/F"]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["cwd"] == runner.ROOT


def test_windows_tree_kill_falls_back_to_process_kill(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 10)

    process = FakeProcess()
    assert runner._kill_windows_tree(process, taskkill_runner=fake_run) is False
    assert process.kill_calls == 1
    assert calls == [["taskkill", "/PID", "4321", "/T", "/F"]]


def test_output_is_limited_and_redacted(monkeypatch, tmp_path):
    script = tmp_path / "writer.py"
    script.write_text(
        "import sys\n"
        "for index in range(220):\n"
        "    print(f'token={index}')\n"
        "    print(f'secret={index}', file=sys.stderr)\n",
        encoding="utf-8",
    )
    result = runner.run_process([sys.executable, str(script)], cwd=tmp_path, timeout_seconds=5, heartbeat_seconds=0)
    assert result.status == "PASS"
    assert len(result.stdout_lines) == runner.MAX_STDOUT_LINES
    assert len(result.stderr_lines) == runner.MAX_STDERR_LINES
    assert result.stdout_lines[0] == "token=[REDACTED]"
    assert result.stderr_lines[0] == "secret=[REDACTED]"

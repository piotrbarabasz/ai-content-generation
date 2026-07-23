from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from app.tooling import process_runner as runner


class FakeProcess:
    def __init__(
        self,
        *,
        pid: int = 4321,
        poll_values: list[int | None] | None = None,
        final_returncode: int = 0,
    ) -> None:
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


def _write_script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


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
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(iterator))


def _patch_perf_counter(monkeypatch, values: list[float]) -> None:
    iterator = iter(values)
    monkeypatch.setattr(runner.time, "perf_counter", lambda: next(iterator))


def test_fast_process_pass(monkeypatch, capsys):
    calls = []
    process = FakeProcess(poll_values=[0], final_returncode=0)
    monkeypatch.setattr(
        runner.subprocess,
        "Popen",
        _fake_popen_factory(calls, process=process, stdout_text="hello\nworld\n", stderr_text="warn\n"),
    )
    _patch_perf_counter(monkeypatch, [10.0, 10.123])
    _patch_monotonic(monkeypatch, [1.0, 1.0])

    result = runner.run_process(
        [sys.executable, "-c", "print('ok')"],
        cwd=runner.ROOT,
        timeout_seconds=5,
        heartbeat_seconds=0,
    )

    assert result.status == "PASS"
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.process_tree_killed is False
    assert result.pid == 4321
    assert result.stdout_lines == ("hello", "world")
    assert result.stderr_lines == ("warn",)
    assert result.output_truncated is False
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["cwd"] == runner.ROOT
    assert calls[0][1]["env"]["PYTHONUNBUFFERED"] == "1"
    output = capsys.readouterr()
    assert "PASS" in output.err


def test_process_exit_code_one(monkeypatch):
    calls = []
    process = FakeProcess(poll_values=[1], final_returncode=1)
    monkeypatch.setattr(runner.subprocess, "Popen", _fake_popen_factory(calls, process=process))
    _patch_perf_counter(monkeypatch, [20.0, 20.045])
    _patch_monotonic(monkeypatch, [2.0, 2.0])

    result = runner.run_process(["git", "status"], cwd=runner.ROOT, timeout_seconds=5, heartbeat_seconds=0)

    assert result.status == "FAIL"
    assert result.exit_code == 1
    assert result.timed_out is False
    assert result.process_tree_killed is False
    assert result.pid == 4321
    assert len(calls) == 1


def test_missing_executable_returns_missing(monkeypatch):
    def fake_popen(*args, **kwargs):
        raise FileNotFoundError("missing executable")

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    _patch_perf_counter(monkeypatch, [30.0, 30.001])
    _patch_monotonic(monkeypatch, [3.0])

    result = runner.run_process(["missing-tool"], cwd=runner.ROOT, timeout_seconds=5, heartbeat_seconds=0)

    assert result.status == "MISSING"
    assert result.exit_code is None
    assert result.pid is None
    assert result.process_tree_killed is False
    assert result.stdout_lines == ()
    assert result.stderr_lines == ()


def test_process_timeout_kills_tree(monkeypatch):
    calls = []
    process = FakeProcess(poll_values=[None, None, None], final_returncode=0)
    terminate_calls = []

    monkeypatch.setattr(runner.subprocess, "Popen", _fake_popen_factory(calls, process=process))
    monkeypatch.setattr(runner, "_terminate_process_tree", lambda proc: terminate_calls.append(proc.pid) or True)
    _patch_perf_counter(monkeypatch, [40.0, 40.100])
    _patch_monotonic(monkeypatch, [4.0, 4.2, 4.4, 4.6, 5.1, 5.1])

    result = runner.run_process([sys.executable, "-c", "import time; time.sleep(60)"], cwd=runner.ROOT, timeout_seconds=1, heartbeat_seconds=0)

    assert result.status == "TIMEOUT"
    assert result.exit_code is None
    assert result.timed_out is True
    assert result.process_tree_killed is True
    assert terminate_calls == [4321]
    assert len(calls) == 1


def test_timeout_does_not_retry(monkeypatch):
    calls = []
    process = FakeProcess(poll_values=[None, None, None], final_returncode=0)
    terminate_calls = []
    monkeypatch.setattr(runner.subprocess, "Popen", _fake_popen_factory(calls, process=process))
    monkeypatch.setattr(runner, "_terminate_process_tree", lambda proc: terminate_calls.append(proc.pid) or True)
    monkeypatch.setattr(runner.time, "sleep", lambda *_args, **_kwargs: None)
    _patch_perf_counter(monkeypatch, [50.0, 50.200])
    _patch_monotonic(monkeypatch, [6.0, 6.5, 7.0, 7.5, 8.1, 8.1])

    result = runner.run_process(["tool"], cwd=runner.ROOT, timeout_seconds=1, heartbeat_seconds=0)

    assert result.status == "TIMEOUT"
    assert len(calls) == 1
    assert terminate_calls == [4321]
    assert process.kill_calls == 0


def test_heartbeat_emits_at_most_once_per_interval(monkeypatch, capsys):
    calls = []
    process = FakeProcess(poll_values=[None, 0], final_returncode=0)
    monkeypatch.setattr(runner.subprocess, "Popen", _fake_popen_factory(calls, process=process))
    monkeypatch.setattr(runner.time, "sleep", lambda *_args, **_kwargs: None)
    _patch_perf_counter(monkeypatch, [60.0, 60.250])
    _patch_monotonic(monkeypatch, [10.0, 11.1, 11.1, 11.1])

    result = runner.run_process(["tool"], cwd=runner.ROOT, timeout_seconds=5, heartbeat_seconds=1)

    assert result.status == "PASS"
    assert result.exit_code == 0
    stderr = capsys.readouterr().err
    assert stderr.count("START tool pid=4321 timeout=5s") == 1
    assert stderr.count("HEARTBEAT tool elapsed=1s pid=4321") == 1
    assert stderr.count("PASS tool duration=") == 1


def test_output_truncation_limits_streams(tmp_path):
    script = _write_script(
        tmp_path,
        "writer.py",
        """
        import sys

        for index in range(40):
            print(f"stdout-{index:02d}")
            print(f"stderr-{index:02d}", file=sys.stderr)
        """,
    )

    result = runner.run_process([sys.executable, str(script)], cwd=tmp_path, timeout_seconds=5, heartbeat_seconds=0)

    assert result.status == "PASS"
    assert len(result.stdout_lines) == 20
    assert len(result.stderr_lines) == 20
    assert result.stdout_lines[0] == "stdout-00"
    assert result.stderr_lines[0] == "stderr-00"
    assert result.output_truncated is True


def test_shell_false_and_environment_overrides_apply(monkeypatch):
    calls = []
    process = FakeProcess(poll_values=[0], final_returncode=0)
    monkeypatch.setattr(runner.subprocess, "Popen", _fake_popen_factory(calls, process=process))
    _patch_perf_counter(monkeypatch, [70.0, 70.040])
    _patch_monotonic(monkeypatch, [12.0, 12.0])

    runner.run_process(
        ["git", "status"],
        cwd=runner.ROOT,
        timeout_seconds=5,
        heartbeat_seconds=0,
        env_overrides={"PAGER": "less", "CUSTOM_FLAG": "1"},
    )

    assert len(calls) == 1
    kwargs = calls[0][1]
    assert kwargs["shell"] is False
    assert kwargs["env"]["GIT_PAGER"] == "cat"
    assert kwargs["env"]["PAGER"] == "less"
    assert kwargs["env"]["TERM"] == "dumb"
    assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["env"]["GCM_INTERACTIVE"] == "Never"
    assert kwargs["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert kwargs["env"]["CUSTOM_FLAG"] == "1"


def test_total_deadline_shorter_than_command_timeout_times_out(tmp_path):
    script = _write_script(
        tmp_path,
        "sleepy.py",
        """
        import time

        time.sleep(5)
        """,
    )

    deadline = time.monotonic() + 0.5
    result = runner.run_process(
        [sys.executable, str(script)],
        cwd=tmp_path,
        timeout_seconds=10,
        total_deadline=deadline,
        heartbeat_seconds=0,
    )

    assert result.status == "TIMEOUT"
    assert result.exit_code is None
    assert result.timed_out is True
    assert result.duration_ms >= 0


def test_process_creates_child_and_kills_entire_tree(tmp_path):
    child_script = _write_script(
        tmp_path,
        "child.py",
        """
        import time

        time.sleep(60)
        """,
    )
    pid_file = tmp_path / "child.pid"
    parent_script = _write_script(
        tmp_path,
        "parent.py",
        """
        import pathlib
        import subprocess
        import sys
        import time

        child_script = pathlib.Path(sys.argv[1])
        pid_file = pathlib.Path(sys.argv[2])
        child = subprocess.Popen([sys.executable, str(child_script)])
        pid_file.write_text(str(child.pid), encoding="utf-8")
        time.sleep(60)
        """,
    )

    result = runner.run_process(
        [sys.executable, str(parent_script), str(child_script), str(pid_file)],
        cwd=tmp_path,
        timeout_seconds=1,
        heartbeat_seconds=0,
    )

    child_pid = int(pid_file.read_text(encoding="utf-8"))
    assert result.status == "TIMEOUT"
    assert result.process_tree_killed is True
    with pytest.raises(OSError):
        os.kill(child_pid, 0)


def test_process_tree_killed_true_after_successful_kill(tmp_path):
    child_script = _write_script(
        tmp_path,
        "child_success.py",
        """
        import time

        time.sleep(60)
        """,
    )
    parent_script = _write_script(
        tmp_path,
        "parent_success.py",
        """
        import subprocess
        import sys
        import time

        child_script = sys.argv[1]
        subprocess.Popen([sys.executable, child_script])
        time.sleep(60)
        """,
    )

    result = runner.run_process(
        [sys.executable, str(parent_script), str(child_script)],
        cwd=tmp_path,
        timeout_seconds=1,
        heartbeat_seconds=0,
    )

    assert result.status == "TIMEOUT"
    assert result.process_tree_killed is True


def test_temp_files_are_removed(monkeypatch, tmp_path):
    created: list[Path] = []
    real_mkstemp = runner.tempfile.mkstemp

    def fake_mkstemp(*args, **kwargs):
        fd, raw_path = real_mkstemp(*args, dir=tmp_path, **kwargs)
        created.append(Path(raw_path))
        return fd, raw_path

    calls = []
    process = FakeProcess(poll_values=[0], final_returncode=0)
    monkeypatch.setattr(runner.tempfile, "mkstemp", fake_mkstemp)
    monkeypatch.setattr(runner.subprocess, "Popen", _fake_popen_factory(calls, process=process, stdout_text="a\n", stderr_text="b\n"))
    _patch_perf_counter(monkeypatch, [80.0, 80.010])
    _patch_monotonic(monkeypatch, [13.0, 13.0])

    result = runner.run_process(["tool"], cwd=tmp_path, timeout_seconds=5, heartbeat_seconds=0)

    assert result.status == "PASS"
    assert created
    assert all(not path.exists() for path in created)


def test_timeout_does_not_wait_forever_for_stdout_and_stderr(tmp_path):
    script = _write_script(
        tmp_path,
        "noisy.py",
        """
        import sys
        import time

        for index in range(5000):
            print(f"stdout-{index}")
            print(f"stderr-{index}", file=sys.stderr)
        time.sleep(60)
        """,
    )

    start = time.perf_counter()
    result = runner.run_process(
        [sys.executable, str(script)],
        cwd=tmp_path,
        timeout_seconds=1,
        heartbeat_seconds=0,
    )
    duration = time.perf_counter() - start

    assert result.status == "TIMEOUT"
    assert result.timed_out is True
    assert len(result.stdout_lines) == 20
    assert len(result.stderr_lines) == 20
    assert duration < 10

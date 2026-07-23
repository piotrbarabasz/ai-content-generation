from __future__ import annotations

import itertools
import json
import sys
from dataclasses import dataclass

from app.tooling import git_hook_runner as runner


@dataclass
class FakeProcessResult:
    status: str
    exit_code: int | None = 0
    duration_ms: int = 12
    timed_out: bool = False
    stdout_lines: tuple[str, ...] = ()
    stderr_lines: tuple[str, ...] = ()
    output_truncated: bool = False
    process_tree_killed: bool = False
    pid: int | None = 1234


def _clock(values: list[float]) -> callable:
    iterator = itertools.chain(values, itertools.repeat(values[-1]))
    return lambda: next(iterator)


def _patch_run_process(monkeypatch, responses: list[FakeProcessResult], calls: list[tuple[tuple[str, ...], dict[str, object]]]) -> None:
    iterator = iter(responses)

    def fake_run_process(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        return next(iterator)

    monkeypatch.setattr(runner.process_runner, "run_process", fake_run_process)


def test_pre_commit_uses_process_runner_and_disables_heartbeat(monkeypatch, capsys):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        calls,
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([200.0, 200.01, 201.0, 201.01, 202.0, 202.01]))

    exit_code = runner.main(["pre-commit", "--json", "--no-heartbeat"])

    assert exit_code == 0
    assert "subprocess" not in runner.__dict__
    assert [call[0] for call in calls] == [
        (sys.executable, "-m", "backend.app.tooling.workstream_validation"),
        (sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
        ("git", "--no-pager", "diff", "--cached", "--check"),
    ]
    assert [call[1]["timeout_seconds"] for call in calls] == [20, 20, 20]
    assert all(call[1]["heartbeat_seconds"] == 0 for call in calls)
    assert all(call[1]["cwd"] == runner.ROOT for call in calls)

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["global_timeout"] is False
    assert len(payload["commands"]) == 3
    assert "output" not in payload["commands"][0]


def test_pre_push_removes_tooling_pytest_and_runs_full_pytest_once(monkeypatch):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        calls,
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([300.0, 300.01, 301.0, 301.01, 302.0, 302.01, 303.0, 303.01]))

    result = runner.run_hook("pre-push")

    assert result.status == "PASS"
    assert [call[0] for call in calls] == [
        (sys.executable, "-m", "backend.app.tooling.workstream_validation"),
        (sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
        (sys.executable, "-m", "pytest"),
        ("git", "--no-pager", "diff", "--check"),
    ]
    assert all("backend/tests/unit/tooling" not in call[0] for call in calls)
    assert sum(1 for call in calls if call[0] == (sys.executable, "-m", "pytest")) == 1
    assert [call[1]["timeout_seconds"] for call in calls] == [20, 20, 300, 20]


def test_ci_uses_commit_range_and_full_pytest_once(monkeypatch):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        calls,
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([500.0, 501.0, 502.0, 503.0, 504.0, 505.0, 506.0, 507.0, 508.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([600.0, 600.01, 601.0, 601.01, 602.0, 602.01, 603.0, 603.01]))

    result = runner.run_hook("ci", base_sha="b" * 40, head_sha="c" * 40)

    assert result.status == "PASS"
    assert [call[0] for call in calls] == [
        (sys.executable, "-m", "backend.app.tooling.workstream_validation"),
        (sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
        (sys.executable, "-m", "pytest"),
        ("git", "--no-pager", "diff", "--check", "b" * 40 + "..." + "c" * 40),
    ]
    assert sum(1 for call in calls if call[0] == (sys.executable, "-m", "pytest")) == 1
    assert [call[1]["timeout_seconds"] for call in calls] == [30, 30, 600, 30]


def test_global_timeout_pre_commit_prints_global_timeout_and_stops(monkeypatch, capsys):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    _patch_run_process(monkeypatch, [FakeProcessResult(status="PASS")], calls)
    monkeypatch.setattr(runner.time, "monotonic", _clock([0.0, 1.0, 2.0, 61.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([700.0, 700.01, 700.02, 700.03]))

    exit_code = runner.main(["pre-commit"])

    assert exit_code == 1
    assert len(calls) == 1
    output = capsys.readouterr().out
    assert "GLOBAL_TIMEOUT" in output
    assert "status: TIMEOUT" in output


def test_global_timeout_pre_push(monkeypatch):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    _patch_run_process(monkeypatch, [FakeProcessResult(status="PASS")], calls)
    monkeypatch.setattr(runner.time, "monotonic", _clock([0.0, 1.0, 2.0, 481.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([800.0, 800.01, 800.02, 800.03]))

    result = runner.run_hook("pre-push")

    assert result.status == "TIMEOUT"
    assert result.global_timeout is True
    assert len(calls) == 1


def test_global_timeout_ci(monkeypatch):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    _patch_run_process(monkeypatch, [FakeProcessResult(status="PASS")], calls)
    monkeypatch.setattr(runner.time, "monotonic", _clock([0.0, 1.0, 2.0, 901.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([900.0, 900.01, 900.02, 900.03]))

    result = runner.run_hook("ci", base_sha="b" * 40, head_sha="c" * 40)

    assert result.status == "TIMEOUT"
    assert result.global_timeout is True
    assert len(calls) == 1


def test_timeout_stops_following_commands(monkeypatch):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    _patch_run_process(monkeypatch, [FakeProcessResult(status="PASS")], calls)
    monkeypatch.setattr(runner.time, "monotonic", _clock([0.0, 1.0, 2.0, 481.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([1000.0, 1000.01, 1000.02, 1000.03]))

    result = runner.run_hook("pre-push")

    assert result.status == "TIMEOUT"
    assert len(calls) == 1


def test_heartbeat_and_json_output_to_correct_streams(monkeypatch, capsys):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_run_process(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        print("START fake pid=123 timeout=20s", file=sys.stderr)
        print("HEARTBEAT fake elapsed=30s pid=123", file=sys.stderr)
        print("PASS fake duration=42ms", file=sys.stderr)
        return FakeProcessResult(status="PASS")

    monkeypatch.setattr(runner.process_runner, "run_process", fake_run_process)
    monkeypatch.setattr(runner.time, "monotonic", _clock([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([1100.0, 1100.01, 1101.0, 1101.01, 1102.0, 1102.01]))

    exit_code = runner.main(["pre-commit", "--json"])

    assert exit_code == 0
    assert len(calls) == 3
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "PASS"
    assert "START fake" in captured.err
    assert "HEARTBEAT fake" in captured.err
    assert "PASS fake" in captured.err


def test_command_results_include_limited_failure_output(monkeypatch):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="FAIL", exit_code=1, stdout_lines=("first line", "second line"), stderr_lines=("stderr line",)),
        ],
        calls,
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([20.0, 21.0, 22.0, 23.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([1200.0, 1200.01, 1201.0, 1201.01]))

    result = runner.run_hook("pre-commit")

    assert result.status == "FAIL"
    assert len(result.commands) == 2
    assert result.commands[1].output == "first line | stderr line"


def test_invalid_mode_returns_usage_error():
    exit_code = runner.main(["bad-mode"])

    assert exit_code == 2

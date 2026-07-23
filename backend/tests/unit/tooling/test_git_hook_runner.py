from __future__ import annotations

import json
import sys
import subprocess

from app.tooling import git_hook_runner as runner


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _patch_run(monkeypatch, responses, calls):
    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        index = len(calls) - 1
        response = responses[index]
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(runner.subprocess, "run", fake_run)


def _patch_perf_counter(monkeypatch, values):
    iterator = iter(values)
    monkeypatch.setattr(runner.time, "perf_counter", lambda: next(iterator))


def test_pre_commit_runs_expected_commands_and_renders_text(monkeypatch, capsys):
    calls = []
    responses = [
        _completed(stdout="guard ok"),
        _completed(stdout="metadata ok"),
        _completed(stdout="diff ok"),
    ]
    _patch_run(monkeypatch, responses, calls)
    _patch_perf_counter(monkeypatch, [1.0, 1.123, 2.0, 2.250, 3.0, 3.375])

    exit_code = runner.main(["pre-commit"])

    assert exit_code == 0
    assert [call[0] for call in calls] == [
        (sys.executable, "-m", "backend.app.tooling.workstream_validation"),
        (sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
        ("git", "--no-pager", "diff", "--cached", "--check"),
    ]
    for _, kwargs in calls:
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        assert kwargs["cwd"] == runner.ROOT
        assert kwargs["env"]["GIT_PAGER"] == "cat"
        assert kwargs["env"]["PAGER"] == "cat"
        assert kwargs["env"]["TERM"] == "dumb"
        assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
        assert kwargs["text"] is True
    assert [kwargs["timeout"] for _, kwargs in calls] == [
        runner.COMMAND_TIMEOUTS["workstream_validation"],
        runner.COMMAND_TIMEOUTS["repository_checks_task_metadata"],
        runner.COMMAND_TIMEOUTS["git_diff_cached_check"],
    ]

    output = capsys.readouterr().out.splitlines()
    assert len(output) <= 20
    assert all(len(line) <= 300 for line in output)
    assert output[0] == "mode: pre-commit"
    assert output[1] == "status: PASS"
    assert any("workstream_validation" in line for line in output)
    assert any("repository_checks_task_metadata" in line for line in output)
    assert any("git_diff_cached_check" in line for line in output)
    assert "timeout=30s" in output[2]
    assert "duration=" in output[2]


def test_pre_push_remains_local_and_ci_uses_commit_range(monkeypatch):
    pre_push_calls = []
    ci_calls = []
    pre_push_responses = [
        _completed(stdout="unit ok"),
        _completed(stdout="guard ok"),
        _completed(stdout="metadata ok"),
        _completed(stdout="full ok"),
        _completed(stdout="diff ok"),
    ]
    ci_responses = list(pre_push_responses)

    _patch_run(monkeypatch, pre_push_responses, pre_push_calls)
    _patch_perf_counter(monkeypatch, [1.0, 1.100, 2.0, 2.200, 3.0, 3.300, 4.0, 4.400, 5.0, 5.500])
    pre_push_result = runner.run_hook("pre-push")

    _patch_run(monkeypatch, ci_responses, ci_calls)
    _patch_perf_counter(monkeypatch, [6.0, 6.100, 7.0, 7.200, 8.0, 8.300, 9.0, 9.400, 10.0, 10.500])
    ci_result = runner.run_hook("ci", base_sha="b" * 40, head_sha="c" * 40)

    expected = [
        (sys.executable, "-m", "pytest", "backend/tests/unit/tooling"),
        (sys.executable, "-m", "backend.app.tooling.workstream_validation"),
        (sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
        (sys.executable, "-m", "pytest"),
        ("git", "--no-pager", "diff", "--check"),
    ]
    assert [call[0] for call in pre_push_calls] == expected
    assert [call[0] for call in ci_calls] == [
        (sys.executable, "-m", "pytest", "backend/tests/unit/tooling"),
        (sys.executable, "-m", "backend.app.tooling.workstream_validation"),
        (sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
        (sys.executable, "-m", "pytest"),
        ("git", "--no-pager", "diff", "--check", "b" * 40 + "..." + "c" * 40),
    ]
    assert pre_push_result.status == "PASS"
    assert ci_result.status == "PASS"
    assert [command.name for command in pre_push_result.commands] == [
        "pytest_unit_tooling",
        "workstream_validation",
        "repository_checks_task_metadata",
        "pytest_full",
        "git_diff_check",
    ]
    assert [command.name for command in ci_result.commands] == [
        "pytest_unit_tooling",
        "workstream_validation",
        "repository_checks_task_metadata",
        "pytest_full",
        "git_diff_check",
    ]
    assert [command.timeout_seconds for command in pre_push_result.commands] == [
        runner.COMMAND_TIMEOUTS["pytest_unit_tooling"],
        runner.COMMAND_TIMEOUTS["workstream_validation"],
        runner.COMMAND_TIMEOUTS["repository_checks_task_metadata"],
        runner.COMMAND_TIMEOUTS["pytest_full"],
        runner.COMMAND_TIMEOUTS["git_diff_check"],
    ]
    assert all(command.duration_ms >= 0 for command in pre_push_result.commands)


def test_ci_falls_back_to_single_commit_when_base_sha_is_zero(monkeypatch):
    calls = []
    responses = [
        _completed(stdout="unit ok"),
        _completed(stdout="guard ok"),
        _completed(stdout="metadata ok"),
        _completed(stdout="full ok"),
        _completed(stdout="diff ok"),
    ]
    _patch_run(monkeypatch, responses, calls)
    _patch_perf_counter(monkeypatch, [1.0, 1.100, 2.0, 2.200, 3.0, 3.300, 4.0, 4.400, 5.0, 5.500])

    runner.run_hook("ci", base_sha="0" * 40, head_sha="c" * 40)

    assert calls[-1][0] == ("git", "--no-pager", "diff", "--check", "c" * 40 + "^!")


def test_timeout_returns_timeout_without_retry(monkeypatch, capsys):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    _patch_perf_counter(monkeypatch, [1.0, 1.5])

    exit_code = runner.main(["pre-commit"])

    assert exit_code == 1
    assert len(calls) == 1
    output = capsys.readouterr().out
    assert "TIMEOUT" in output
    assert "workstream_validation" in output
    assert "exit=None" in output
    assert "timeout=30s" in output


def test_failure_stops_after_first_failing_command(monkeypatch, capsys):
    calls = []
    responses = [
        _completed(stdout="guard ok"),
        _completed(returncode=1, stderr="repository checks failed"),
        _completed(stdout="diff ok"),
    ]
    _patch_run(monkeypatch, responses, calls)
    _patch_perf_counter(monkeypatch, [1.0, 1.1, 2.0, 2.2])

    exit_code = runner.main(["pre-commit"])

    assert exit_code == 1
    assert len(calls) == 2
    output = capsys.readouterr().out
    assert "repository_checks_task_metadata" in output
    assert "diff ok" not in output


def test_json_output_is_parseable_and_bounded(monkeypatch, capsys):
    calls = []
    responses = [
        _completed(stdout="unit ok"),
        _completed(stdout="guard ok"),
        _completed(stdout="metadata ok"),
        _completed(stdout="full ok"),
        _completed(stdout="diff ok"),
    ]
    _patch_run(monkeypatch, responses, calls)
    _patch_perf_counter(monkeypatch, [1.0, 1.05, 2.0, 2.1, 3.0, 3.15, 4.0, 4.2, 5.0, 5.25])

    exit_code = runner.main(["pre-push", "--json"])

    assert exit_code == 0
    output = capsys.readouterr().out.splitlines()
    assert len(output) <= 20
    assert all(len(line) <= 300 for line in output)
    payload = json.loads("\n".join(output))
    assert payload["mode"] == "pre-push"
    assert payload["status"] == "PASS"
    assert len(payload["commands"]) == 5
    assert payload["commands"][0]["name"] == "pytest_unit_tooling"
    assert payload["commands"][-1]["name"] == "git_diff_check"
    assert payload["commands"][0]["timeout_seconds"] == runner.COMMAND_TIMEOUTS["pytest_unit_tooling"]
    assert payload["commands"][0]["duration_ms"] >= 0
    assert payload["commands"][0]["timed_out"] is False
    assert "output" not in payload["commands"][0]


def test_json_output_includes_limited_output_on_failure(monkeypatch, capsys):
    calls = []
    responses = [
        _completed(stdout="guard ok"),
        _completed(returncode=1, stdout="first line\nsecond line", stderr="stderr line"),
    ]
    _patch_run(monkeypatch, responses, calls)
    _patch_perf_counter(monkeypatch, [1.0, 1.1, 2.0, 2.2])

    exit_code = runner.main(["pre-commit", "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAIL"
    assert len(payload["commands"]) == 2
    assert payload["commands"][1]["exit_code"] == 1
    assert "output" in payload["commands"][1]
    assert len(payload["commands"][1]["output"]) <= 300
    assert "first line" in payload["commands"][1]["output"]
    assert "second line" not in payload["commands"][1]["output"]


def test_timeout_does_not_retry_and_sets_exit_code_null(monkeypatch, capsys):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    _patch_perf_counter(monkeypatch, [1.0, 1.25])

    exit_code = runner.main(["pre-push", "--json"])

    assert exit_code == 1
    assert len(calls) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "TIMEOUT"
    assert payload["commands"][0]["exit_code"] is None
    assert payload["commands"][0]["timed_out"] is True
    assert payload["commands"][0]["timeout_seconds"] == runner.COMMAND_TIMEOUTS["pytest_unit_tooling"]
    assert payload["commands"][0]["duration_ms"] >= 0
    assert "output" in payload["commands"][0]


def test_command_env_and_check_flags_are_strict(monkeypatch):
    calls = []
    responses = [_completed(stdout="guard ok"), _completed(stdout="metadata ok"), _completed(stdout="diff ok")]
    _patch_run(monkeypatch, responses, calls)
    _patch_perf_counter(monkeypatch, [1.0, 1.1, 2.0, 2.1, 3.0, 3.1])

    runner.run_hook("pre-commit")

    assert len(calls) == 3
    for _, kwargs in calls:
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        assert kwargs["env"]["GIT_PAGER"] == "cat"
        assert kwargs["env"]["PAGER"] == "cat"
        assert kwargs["env"]["TERM"] == "dumb"
        assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"


def test_invalid_mode_returns_usage_error(capsys):
    exit_code = runner.main(["bad-mode"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.err or captured.out

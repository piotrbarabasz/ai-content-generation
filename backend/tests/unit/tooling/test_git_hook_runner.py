from __future__ import annotations

import json
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


def test_pre_commit_runs_expected_commands_and_renders_text(monkeypatch, capsys):
    calls = []
    responses = [
        _completed(stdout="guard ok"),
        _completed(stdout="metadata ok"),
        _completed(stdout="diff ok"),
    ]
    _patch_run(monkeypatch, responses, calls)

    exit_code = runner.main(["pre-commit"])

    assert exit_code == 0
    assert [call[0] for call in calls] == [
        ("python", "-m", "backend.app.tooling.workstream_validation"),
        ("python", "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
        ("git", "--no-pager", "diff", "--cached", "--check"),
    ]
    for _, kwargs in calls:
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == runner.TIMEOUT_SECONDS
        assert kwargs["cwd"] == runner.ROOT
        assert kwargs["env"]["GIT_PAGER"] == "cat"
        assert kwargs["env"]["PAGER"] == "cat"
        assert kwargs["env"]["TERM"] == "dumb"
        assert kwargs["text"] is True

    output = capsys.readouterr().out.splitlines()
    assert len(output) <= 20
    assert all(len(line) <= 300 for line in output)
    assert output[0] == "mode: pre-commit"
    assert output[1] == "status: PASS"
    assert any("workstream_validation" in line for line in output)
    assert any("repository_checks_task_metadata" in line for line in output)
    assert any("git_diff_cached_check" in line for line in output)


def test_pre_push_and_ci_use_identical_command_sequences(monkeypatch):
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
    pre_push_result = runner.run_hook("pre-push")

    _patch_run(monkeypatch, ci_responses, ci_calls)
    ci_result = runner.run_hook("ci")

    expected = [
        ("python", "-m", "pytest", "backend/tests/unit/tooling"),
        ("python", "-m", "backend.app.tooling.workstream_validation"),
        ("python", "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
        ("python", "-m", "pytest"),
        ("git", "--no-pager", "diff", "--check"),
    ]
    assert [call[0] for call in pre_push_calls] == expected
    assert [call[0] for call in ci_calls] == expected
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


def test_timeout_returns_timeout_without_retry(monkeypatch, capsys):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    exit_code = runner.main(["pre-commit"])

    assert exit_code == 1
    assert len(calls) == 1
    output = capsys.readouterr().out
    assert "TIMEOUT" in output
    assert "workstream_validation" in output


def test_failure_stops_after_first_failing_command(monkeypatch, capsys):
    calls = []
    responses = [
        _completed(stdout="guard ok"),
        _completed(returncode=1, stderr="repository checks failed"),
        _completed(stdout="diff ok"),
    ]
    _patch_run(monkeypatch, responses, calls)

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


def test_invalid_mode_returns_usage_error(capsys):
    exit_code = runner.main(["bad-mode"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.err or captured.out

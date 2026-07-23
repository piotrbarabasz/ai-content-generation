from __future__ import annotations

from pathlib import Path

import pytest

from app.tooling.local_autopilot import process_runner
from app.tooling.local_autopilot.repository import GitStatus, Repository


def _result(command: tuple[str, ...], *, status: str = "PASS", exit_code: int | None = 0, stdout_lines: tuple[str, ...] = (), stderr_lines: tuple[str, ...] = ()) -> process_runner.ProcessResult:
    return process_runner.ProcessResult(
        command=command,
        status=status,
        exit_code=exit_code,
        duration_ms=5,
        timed_out=False,
        cancelled=False,
        stdout_lines=stdout_lines,
        stderr_lines=stderr_lines,
        output_truncated=False,
        process_tree_killed=False,
        pid=1234,
    )


def test_status_and_clean_tree_detection(monkeypatch, tmp_path):
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command == ("git", "status", "--porcelain=v1", "--branch", "--untracked-files=all"):
            return _result(command, stdout_lines=("## epic/test", " M changed.txt", "?? new.txt"))
        if command == ("git", "rev-parse", "HEAD"):
            return _result(command, stdout_lines=("a" * 40,))
        raise AssertionError(command)

    repo = Repository(tmp_path, process_runner_fn=fake_run)
    status = repo.status()

    assert status.branch == "epic/test"
    assert status.clean is False
    assert status.tracked == ("changed.txt",)
    assert status.untracked == ("new.txt",)
    assert calls[0] == ("git", "status", "--porcelain=v1", "--branch", "--untracked-files=all")

    with pytest.raises(RuntimeError):
        repo.require_clean_tree()


def test_head_and_branch_management(monkeypatch, tmp_path):
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command == ("git", "show-ref", "--verify", "--quiet", "refs/heads/feat/local-autopilot-ui"):
            return _result(command, status="FAIL", exit_code=1)
        if command == ("git", "switch", "master"):
            return _result(command)
        if command == ("git", "pull", "--ff-only", "origin", "master"):
            return _result(command)
        if command == ("git", "switch", "-c", "feat/local-autopilot-ui", "master"):
            return _result(command)
        if command == ("git", "rev-parse", "HEAD"):
            return _result(command, stdout_lines=("b" * 40,))
        return _result(command)

    repo = Repository(tmp_path, process_runner_fn=fake_run)
    assert repo.head_sha() == "b" * 40
    repo.switch_to_master_and_pull()
    repo.create_branch("feat/local-autopilot-ui")

    assert ("git", "switch", "master") in calls
    assert ("git", "pull", "--ff-only", "origin", "master") in calls
    assert ("git", "switch", "-c", "feat/local-autopilot-ui", "master") in calls


def test_create_branch_switches_existing_branch(monkeypatch, tmp_path):
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command == ("git", "show-ref", "--verify", "--quiet", "refs/heads/feat/local-autopilot-ui"):
            return _result(command)
        if command == ("git", "switch", "feat/local-autopilot-ui"):
            return _result(command)
        raise AssertionError(command)

    repo = Repository(tmp_path, process_runner_fn=fake_run)
    repo.create_branch("feat/local-autopilot-ui")

    assert ("git", "switch", "feat/local-autopilot-ui") in calls
    assert ("git", "switch", "-c", "feat/local-autopilot-ui", "master") not in calls


def test_stage_diff_commit_and_push(monkeypatch, tmp_path):
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        return _result(command)

    repo = Repository(tmp_path, process_runner_fn=fake_run)
    repo.stage_allowlist(["backend/app/tooling/local_autopilot/models.py", "backend/tests/unit/tooling/local_autopilot/test_models.py"])
    repo.diff_check()
    repo.diff_check(cached=True)
    repo.commit("feat(autopilot): add safe local git operations")
    repo.push("feat/local-autopilot-ui")

    assert ("git", "add", "--", "backend/app/tooling/local_autopilot/models.py", "backend/tests/unit/tooling/local_autopilot/test_models.py") in calls
    assert ("git", "--no-pager", "diff", "--check") in calls
    assert ("git", "--no-pager", "diff", "--cached", "--check") in calls
    assert ("git", "commit", "-m", "feat(autopilot): add safe local git operations") in calls
    assert ("git", "push", "-u", "origin", "feat/local-autopilot-ui") in calls


def test_commit_rejects_empty_message(tmp_path):
    repo = Repository(tmp_path, process_runner_fn=lambda *args, **kwargs: _result(tuple(args[0])))

    with pytest.raises(ValueError):
        repo.commit(" ")


def test_forbidden_git_commands_are_rejected(tmp_path):
    repo = Repository(tmp_path, process_runner_fn=lambda *args, **kwargs: _result(tuple(args[0])))

    with pytest.raises(ValueError):
        repo._git("git", "merge", "main")


def test_normalize_allowlist_eof_only_changes_text_files(tmp_path):
    text_file = tmp_path / "backend" / "app" / "tooling" / "local_autopilot" / "notes.txt"
    text_file.parent.mkdir(parents=True, exist_ok=True)
    text_file.write_bytes(b"alpha\r\nbeta\r\n\r\n")
    binary_file = tmp_path / "assets" / "blob.bin"
    binary_file.parent.mkdir(parents=True, exist_ok=True)
    binary_file.write_bytes(b"\x00\x01\x02")

    repo = Repository(tmp_path, process_runner_fn=lambda *args, **kwargs: _result(tuple(args[0])))
    changed = repo.normalize_allowlist_eof([text_file.relative_to(tmp_path), binary_file.relative_to(tmp_path)])

    assert changed == [text_file.as_posix()]
    assert text_file.read_text(encoding="utf-8") == "alpha\nbeta\n"
    assert binary_file.read_bytes() == b"\x00\x01\x02"

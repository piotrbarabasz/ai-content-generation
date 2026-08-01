from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.tooling.local_autopilot import process_runner
from app.tooling.local_autopilot.repository import Repository


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


def test_validate_remote_returns_url_and_probes_head(tmp_path):
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command == ("git", "remote", "get-url", "origin"):
            return _result(command, stdout_lines=("https://example.invalid/repo.git",))
        if command == ("git", "ls-remote", "--exit-code", "origin", "HEAD"):
            return _result(command, stdout_lines=("a" * 40 + "\tHEAD",))
        raise AssertionError(command)

    repo = Repository(tmp_path, process_runner_fn=fake_run)
    remote_url = repo.validate_remote("origin", probe_timeout_seconds=30)

    assert remote_url == "https://example.invalid/repo.git"
    assert calls == [
        ("git", "remote", "get-url", "origin"),
        ("git", "ls-remote", "--exit-code", "origin", "HEAD"),
    ]


def test_validate_remote_rejects_missing_origin(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("git", "remote", "get-url", "origin"):
            return _result(command, status="FAIL", exit_code=1, stderr_lines=("error: No such remote 'origin'",))
        raise AssertionError(command)

    repo = Repository(tmp_path, process_runner_fn=fake_run)

    with pytest.raises(RuntimeError, match="No such remote 'origin'"):
        repo.validate_remote("origin")


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
    kwargs_seen: list[dict[str, object]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        kwargs_seen.append(dict(kwargs))
        return _result(command)

    repo = Repository(tmp_path, process_runner_fn=fake_run)
    repo.stage_allowlist(["backend/app/tooling/local_autopilot/models.py", "backend/tests/unit/tooling/local_autopilot/test_models.py"])
    repo.diff_check()
    repo.diff_check(cached=True)
    repo.commit("feat(autopilot): add safe local git operations")
    repo.push("feat/local-autopilot-ui", timeout_seconds=1200)

    assert ("git", "add", "--", "backend/app/tooling/local_autopilot/models.py", "backend/tests/unit/tooling/local_autopilot/test_models.py") in calls
    assert ("git", "--no-pager", "diff", "--check") in calls
    assert ("git", "--no-pager", "diff", "--cached", "--check") in calls
    assert ("git", "commit", "-m", "feat(autopilot): add safe local git operations") in calls
    assert ("git", "push", "-u", "origin", "feat/local-autopilot-ui") in calls
    assert kwargs_seen[-1]["timeout_seconds"] == 1200


def test_commit_rejects_empty_message(tmp_path):
    repo = Repository(tmp_path, process_runner_fn=lambda *args, **kwargs: _result(tuple(args[0])))

    with pytest.raises(ValueError):
        repo.commit(" ")


def test_forbidden_git_commands_are_rejected(tmp_path):
    repo = Repository(tmp_path, process_runner_fn=lambda *args, **kwargs: _result(tuple(args[0])))

    with pytest.raises(ValueError):
        repo._git("git", "merge", "main")


def test_merge_ff_only_is_allowed(tmp_path):
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command == ("git", "merge", "--ff-only", "master"):
            return _result(command)
        raise AssertionError(command)

    repo = Repository(tmp_path, process_runner_fn=fake_run)
    repo.merge_ff_only("master")

    assert ("git", "merge", "--ff-only", "master") in calls


def test_merge_base_into_active_branch_uses_controlled_no_edit_merge(tmp_path):
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command == ("git", "status", "--porcelain=v1", "--branch", "--untracked-files=all"):
            return _result(command, stdout_lines=("## feature/E002",))
        if command == ("git", "rev-parse", "HEAD"):
            return _result(command, stdout_lines=("a" * 40,))
        if command == ("git", "switch", "master"):
            return _result(command)
        if command == ("git", "pull", "--ff-only", "origin", "master"):
            return _result(command)
        if command == ("git", "switch", "feature/E002"):
            return _result(command)
        if command == ("git", "merge", "--no-edit", "master"):
            return _result(command)
        raise AssertionError(command)

    repo = Repository(tmp_path, process_runner_fn=fake_run)
    repo.merge_base_into_active_branch("feature/E002", "master", timeout_seconds=90)

    assert ("git", "merge", "--no-edit", "master") in calls
    assert ("git", "reset", "master") not in calls
    assert ("git", "rebase", "master") not in calls


def test_merge_base_into_active_branch_rejects_invalid_branch_names(tmp_path):
    repo = Repository(tmp_path, process_runner_fn=lambda *args, **kwargs: _result(tuple(args[0])))

    with pytest.raises(ValueError):
        repo.merge_base_into_active_branch("feature/../evil", "master")


def test_sync_branch_with_base_fast_forwards_when_branch_is_behind(tmp_path):
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command == ("git", "status", "--porcelain=v1", "--branch", "--untracked-files=all"):
            return _result(command, stdout_lines=("## feature/E002",))
        if command == ("git", "rev-parse", "HEAD"):
            return _result(command, stdout_lines=("a" * 40,))
        if command == ("git", "rev-parse", "master"):
            return _result(command, stdout_lines=("b" * 40,))
        if command == ("git", "merge-base", "--is-ancestor", "a" * 40, "b" * 40):
            return _result(command)
        if command == ("git", "merge", "--ff-only", "master"):
            return _result(command)
        raise AssertionError(command)

    repo = Repository(tmp_path, process_runner_fn=fake_run)
    repo.sync_branch_with_base("feature/E002", base_branch="master", base_head_sha="b" * 40)

    assert ("git", "merge", "--ff-only", "master") in calls


def test_sync_branch_with_base_noops_when_branch_contains_base(tmp_path):
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command == ("git", "status", "--porcelain=v1", "--branch", "--untracked-files=all"):
            return _result(command, stdout_lines=("## feature/E002",))
        if command == ("git", "rev-parse", "HEAD"):
            return _result(command, stdout_lines=("b" * 40,))
        if command == ("git", "rev-parse", "master"):
            return _result(command, stdout_lines=("a" * 40,))
        if command == ("git", "merge-base", "--is-ancestor", "b" * 40, "a" * 40):
            return _result(command, status="FAIL", exit_code=1)
        if command == ("git", "merge-base", "--is-ancestor", "a" * 40, "b" * 40):
            return _result(command)
        raise AssertionError(command)

    repo = Repository(tmp_path, process_runner_fn=fake_run)
    repo.sync_branch_with_base("feature/E002", base_branch="master", base_head_sha="a" * 40)

    assert ("git", "merge", "--ff-only", "master") not in calls


def test_sync_branch_with_base_rejects_diverged_branches(tmp_path):
    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command == ("git", "status", "--porcelain=v1", "--branch", "--untracked-files=all"):
            return _result(command, stdout_lines=("## feature/E002",))
        if command == ("git", "rev-parse", "HEAD"):
            return _result(command, stdout_lines=("a" * 40,))
        if command == ("git", "rev-parse", "master"):
            return _result(command, stdout_lines=("b" * 40,))
        if command == ("git", "merge-base", "--is-ancestor", "a" * 40, "b" * 40):
            return _result(command, status="FAIL", exit_code=1)
        if command == ("git", "merge-base", "--is-ancestor", "b" * 40, "a" * 40):
            return _result(command, status="FAIL", exit_code=1)
        raise AssertionError(command)

    repo = Repository(tmp_path, process_runner_fn=fake_run)

    with pytest.raises(RuntimeError):
        repo.sync_branch_with_base("feature/E002", base_branch="master", base_head_sha="b" * 40)


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


def test_status_ignores_tmp_pytest_temp_untracked_files(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is required for this test")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    git("init")
    git("config", "user.email", "tester@example.invalid")
    git("config", "user.name", "Tester")
    (tmp_path / ".gitignore").write_text(".tmp/\n", encoding="utf-8")
    (tmp_path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    temp_file = tmp_path / ".tmp" / "pytest-temp" / "generated.txt"
    temp_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file.write_text("generated\n", encoding="utf-8")
    git("add", ".gitignore", "tracked.txt")
    git("commit", "-m", "initial commit")

    repo = Repository(tmp_path)
    status = repo.status()

    assert ".tmp/pytest-temp/generated.txt" not in status.untracked
    assert status.clean is True

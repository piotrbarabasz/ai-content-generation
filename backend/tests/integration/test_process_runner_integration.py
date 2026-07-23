from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from app.tooling import process_runner


def _write_script(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return str(pid) in output and "No tasks are running" not in output
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_for_exit(pid: int, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.1)
    return not _pid_exists(pid)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"})
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )


def test_timeout_kills_parent_child_and_grandchild_tree(tmp_path: Path) -> None:
    grandchild = _write_script(
        tmp_path / "grandchild.py",
        """
        import time

        time.sleep(300)
        """,
    )
    child = _write_script(
        tmp_path / "child.py",
        """
        import pathlib
        import subprocess
        import sys
        import time

        grandchild = pathlib.Path(sys.argv[1])
        grandchild_pid_file = pathlib.Path(sys.argv[2])
        proc = subprocess.Popen([sys.executable, str(grandchild)])
        grandchild_pid_file.write_text(str(proc.pid), encoding="utf-8")
        time.sleep(300)
        """,
    )
    parent = _write_script(
        tmp_path / "parent.py",
        """
        import pathlib
        import subprocess
        import sys
        import time

        child = pathlib.Path(sys.argv[1])
        grandchild = pathlib.Path(sys.argv[2])
        child_pid_file = pathlib.Path(sys.argv[3])
        grandchild_pid_file = pathlib.Path(sys.argv[4])
        proc = subprocess.Popen([sys.executable, str(child), str(grandchild), str(grandchild_pid_file)])
        child_pid_file.write_text(str(proc.pid), encoding="utf-8")
        time.sleep(300)
        """,
    )
    child_pid_file = tmp_path / "child.pid"
    grandchild_pid_file = tmp_path / "grandchild.pid"

    start = time.perf_counter()
    result = process_runner.run_process(
        [sys.executable, str(parent), str(child), str(grandchild), str(child_pid_file), str(grandchild_pid_file)],
        cwd=tmp_path,
        timeout_seconds=2,
        heartbeat_seconds=0,
    )
    duration = time.perf_counter() - start

    assert result.status == "TIMEOUT"
    assert result.timed_out is True
    assert result.process_tree_killed is True
    assert duration < 15

    parent_pid = result.pid
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    grandchild_pid = int(grandchild_pid_file.read_text(encoding="utf-8"))

    assert parent_pid is not None
    assert _wait_for_exit(parent_pid)
    assert _wait_for_exit(child_pid)
    assert _wait_for_exit(grandchild_pid)


def test_git_commit_is_non_interactive_with_local_hook_path(tmp_path: Path) -> None:
    hooks_dir = tmp_path / "empty-hooks"
    hooks_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()

    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "credential.interactive", "never")
    _git(repo, "config", "core.hooksPath", str(hooks_dir))
    (repo / "example.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "example.txt")
    _git(repo, "commit", "--no-gpg-sign", "-m", "test")

    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert len(head) == 40
    assert head

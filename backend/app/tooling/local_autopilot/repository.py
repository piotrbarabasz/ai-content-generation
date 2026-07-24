"""Local Git repository operations for the autopilot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from . import process_runner

ROOT = Path(__file__).resolve().parents[4]
FORBIDDEN_GIT_COMMANDS = {"merge", "rebase", "stash", "reset"}
TEXT_SUFFIXES = {".txt", ".md", ".py", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".ps1", ".cmd", ".bat"}


@dataclass(frozen=True)
class GitStatus:
    branch: str
    head_sha: str
    tracked: tuple[str, ...]
    staged: tuple[str, ...]
    untracked: tuple[str, ...]
    deleted: tuple[str, ...]
    renamed: tuple[tuple[str, str], ...]

    @property
    def clean(self) -> bool:
        return not (self.tracked or self.staged or self.untracked or self.deleted or self.renamed)


class Repository:
    def __init__(
        self,
        root: Path | str = ROOT,
        *,
        process_runner_fn=process_runner.run_process,
    ) -> None:
        self.root = Path(root)
        self._run = process_runner_fn

    def _git(self, *args: str, timeout_seconds: int = 20) -> process_runner.ProcessResult:
        self._validate_command(args)
        return self._run(list(args), cwd=self.root, timeout_seconds=timeout_seconds, heartbeat_seconds=0)

    def _validate_command(self, args: Sequence[str]) -> None:
        for index, part in enumerate(args):
            if index == 0 and part == "git":
                continue
            if part in FORBIDDEN_GIT_COMMANDS:
                raise ValueError(f"forbidden git command: {part}")

    def status(self) -> GitStatus:
        result = self._git("git", "status", "--porcelain=v1", "--branch", "--untracked-files=all")
        if result.status != "PASS":
            raise RuntimeError("git status failed")
        status = _parse_status(result.stdout_lines)
        head = self._git("git", "rev-parse", "HEAD")
        head_sha = head.stdout_lines[0].strip() if head.status == "PASS" and head.stdout_lines else ""
        return GitStatus(
            branch=status.branch,
            head_sha=head_sha,
            tracked=status.tracked,
            staged=status.staged,
            untracked=status.untracked,
            deleted=status.deleted,
            renamed=status.renamed,
        )

    def require_clean_tree(self) -> GitStatus:
        status = self.status()
        if not status.clean:
            raise RuntimeError("working tree must be clean")
        return status

    def head_sha(self) -> str:
        result = self._git("git", "rev-parse", "HEAD")
        if result.status != "PASS" or not result.stdout_lines:
            raise RuntimeError("cannot resolve HEAD")
        return result.stdout_lines[0].strip()

    def branch_exists(self, branch: str) -> bool:
        result = self._git("git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
        return result.status == "PASS"

    def switch_to_master_and_pull(self, base_branch: str = "master", remote: str = "origin") -> None:
        switch_result = self._git("git", "switch", base_branch)
        if switch_result.status != "PASS":
            raise RuntimeError(f"git switch {base_branch} failed")
        pull_result = self._git("git", "pull", "--ff-only", remote, base_branch)
        if pull_result.status != "PASS":
            raise RuntimeError(f"git pull --ff-only {remote} {base_branch} failed")

    def create_branch(self, branch: str, *, base_branch: str = "master") -> None:
        if self.branch_exists(branch):
            switch_result = self._git("git", "switch", branch)
            if switch_result.status != "PASS":
                raise RuntimeError(f"git switch {branch} failed")
            return
        create_result = self._git("git", "switch", "-c", branch, base_branch)
        if create_result.status != "PASS":
            raise RuntimeError(f"git switch -c {branch} {base_branch} failed")

    def stage_allowlist(self, allowlist: Sequence[str]) -> None:
        paths = [str(path) for path in allowlist]
        if not paths:
            return
        self._git("git", "add", "--", *paths)

    def diff_check(self, *, cached: bool = False) -> process_runner.ProcessResult:
        if cached:
            return self._git("git", "--no-pager", "diff", "--cached", "--check")
        return self._git("git", "--no-pager", "diff", "--check")

    def commit(self, message: str) -> process_runner.ProcessResult:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("commit message must be a non-empty string")
        return self._git("git", "commit", "-m", message.strip())

    def push(self, branch: str, remote: str = "origin") -> process_runner.ProcessResult:
        return self._git("git", "push", "-u", remote, branch)

    def normalize_allowlist_eof(self, text_paths: Sequence[Path | str]) -> list[str]:
        changed: list[str] = []
        for raw_path in text_paths:
            path = self.root / Path(raw_path)
            if not path.is_file():
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"tasks.md", "spec.md", "plan.md", "quickstart.md", "research.md", "data-model.md"}:
                continue
            try:
                raw = path.read_bytes()
            except UnicodeDecodeError:
                continue
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            normalized = _normalize_trailing_newline_bytes(raw)
            if normalized != raw:
                path.write_bytes(normalized)
                changed.append(path.as_posix())
        return changed


def _normalize_trailing_newline(text: str) -> str:
    if text == "":
        return text
    return text.rstrip("\r\n") + "\n"


def _normalize_trailing_newline_bytes(raw: bytes) -> bytes:
    if not raw:
        return raw
    return raw.rstrip(b"\r\n") + b"\n"


def _parse_status(lines: Sequence[str]) -> GitStatus:
    branch = ""
    tracked: list[str] = []
    staged: list[str] = []
    untracked: list[str] = []
    deleted: list[str] = []
    renamed: list[tuple[str, str]] = []

    for index, line in enumerate(lines):
        if index == 0 and line.startswith("## "):
            branch = line[3:].strip()
            if "..." in branch:
                branch = branch.split("...", 1)[0].strip()
            continue
        if line.startswith("?? "):
            _append_unique(untracked, line[3:])
            continue
        if len(line) < 3:
            continue
        status = line[:2]
        path = line[3:].strip()
        if "->" in path and (status.startswith("R") or status.endswith("R")):
            old_path, new_path = [part.strip() for part in path.split("->", 1)]
            renamed.append((old_path, new_path))
            _append_unique(staged, old_path)
            _append_unique(tracked, new_path)
            continue
        if status[0] == "D" or status[1] == "D":
            _append_unique(deleted, path)
        if status[0] not in {" ", "?"}:
            _append_unique(staged, path)
        if status[1] not in {" ", "?"}:
            _append_unique(tracked, path)

    return GitStatus(
        branch=branch,
        head_sha="",
        tracked=tuple(tracked),
        staged=tuple(staged),
        untracked=tuple(untracked),
        deleted=tuple(deleted),
        renamed=tuple(renamed),
    )


def _append_unique(bucket: list[str], value: str) -> None:
    normalized = value.replace("\\", "/").strip()
    if normalized and normalized not in bucket:
        bucket.append(normalized)


__all__ = ["GitStatus", "Repository", "ROOT"]

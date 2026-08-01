"""Local Git repository operations for the autopilot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from . import process_runner

ROOT = Path(__file__).resolve().parents[4]
FORBIDDEN_GIT_COMMANDS = {"rebase", "stash", "reset"}
BRANCH_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,249}$")
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
            if part == "merge":
                if not (_is_allowed_ff_only_merge(args) or _is_allowed_no_edit_merge(args)):
                    raise ValueError("forbidden git command: merge")
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

    def validate_remote(
        self,
        remote: str = "origin",
        *,
        timeout_seconds: int = 20,
        probe_timeout_seconds: int | None = None,
    ) -> str:
        normalized_remote = remote.strip()
        if not normalized_remote:
            raise ValueError("remote must be a non-empty string")
        result = self._git("git", "remote", "get-url", normalized_remote, timeout_seconds=timeout_seconds)
        if result.status != "PASS" or not result.stdout_lines:
            raise RuntimeError(_result_detail(result) or f"{normalized_remote} remote is missing")
        remote_url = result.stdout_lines[0].strip()
        if not remote_url:
            raise RuntimeError(f"{normalized_remote} remote is missing")
        if probe_timeout_seconds is not None:
            probe = self._git(
                "git",
                "ls-remote",
                "--exit-code",
                normalized_remote,
                "HEAD",
                timeout_seconds=probe_timeout_seconds,
            )
            if probe.status != "PASS":
                raise RuntimeError(_result_detail(probe) or f"{normalized_remote} remote HEAD probe failed")
        return remote_url

    def branch_exists(self, branch: str) -> bool:
        result = self._git("git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
        return result.status == "PASS"

    def switch_to_master_and_pull(self, base_branch: str = "master", remote: str = "origin", *, timeout_seconds: int = 20) -> None:
        _validate_branch_name(base_branch, field_name="base_branch")
        switch_result = self._git("git", "switch", base_branch, timeout_seconds=timeout_seconds)
        if switch_result.status != "PASS":
            raise RuntimeError(_result_detail(switch_result) or f"git switch {base_branch} failed")
        pull_result = self._git("git", "pull", "--ff-only", remote, base_branch, timeout_seconds=timeout_seconds)
        if pull_result.status != "PASS":
            raise RuntimeError(_result_detail(pull_result) or f"git pull --ff-only {remote} {base_branch} failed")

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = self._git("git", "merge-base", "--is-ancestor", ancestor, descendant)
        return result.status == "PASS"

    def find_commit_shas_by_subject(self, subject: str, ref: str = "HEAD") -> tuple[str, ...]:
        subject = subject.strip()
        if not subject:
            raise ValueError("subject must be a non-empty string")
        result = self._git("git", "log", ref, "--no-merges", "--format=%H%x1f%s")
        if result.status != "PASS":
            raise RuntimeError(f"git log {ref} failed")
        shas: list[str] = []
        for line in result.stdout_lines:
            if "\x1f" not in line:
                continue
            sha, commit_subject = line.split("\x1f", 1)
            if commit_subject.strip() == subject:
                shas.append(sha.strip())
        return tuple(shas)

    def list_commit_history(self, ref: str = "HEAD") -> tuple[tuple[str, str], ...]:
        result = self._git("git", "log", ref, "--no-merges", "--format=%H%x1f%s")
        if result.status != "PASS":
            raise RuntimeError(f"git log {ref} failed")
        entries: list[tuple[str, str]] = []
        for line in result.stdout_lines:
            if "\x1f" not in line:
                continue
            sha, subject = line.split("\x1f", 1)
            sha = sha.strip()
            subject = subject.strip()
            if sha and subject:
                entries.append((sha, subject))
        return tuple(entries)

    def find_commits_adding_path(self, path: Path | str, ref: str = "HEAD") -> tuple[str, ...]:
        normalized = str(path).replace("\\", "/").strip()
        if not normalized:
            raise ValueError("path must be a non-empty string")
        result = self._git("git", "log", ref, "--diff-filter=A", "--format=%H", "--", normalized)
        if result.status != "PASS":
            raise RuntimeError(f"git log {ref} -- {normalized} failed")
        return tuple(line.strip() for line in result.stdout_lines if line.strip())

    def list_commit_files(self, commit_sha: str) -> tuple[str, ...]:
        commit_sha = commit_sha.strip()
        if not commit_sha:
            raise ValueError("commit_sha must be a non-empty string")
        result = self._git("git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha)
        if result.status != "PASS":
            raise RuntimeError(f"git diff-tree {commit_sha} failed")
        return tuple(line.replace("\\", "/").strip() for line in result.stdout_lines if line.strip())

    def merge_ff_only(self, branch: str) -> None:
        _validate_branch_name(branch)
        result = self._git("git", "merge", "--ff-only", branch)
        if result.status != "PASS":
            raise RuntimeError(_result_detail(result) or f"git merge --ff-only {branch} failed")

    def merge_base_into_active_branch(self, branch: str, base_branch: str, *, timeout_seconds: int = 20) -> None:
        _validate_branch_name(branch)
        _validate_branch_name(base_branch, field_name="base_branch")
        current = self.require_clean_tree()
        if current.branch != branch:
            raise RuntimeError(f"current branch {current.branch!r} does not match {branch!r}")
        switch_result = self._git("git", "switch", base_branch, timeout_seconds=timeout_seconds)
        if switch_result.status != "PASS":
            raise RuntimeError(_result_detail(switch_result) or f"git switch {base_branch} failed")
        pull_result = self._git("git", "pull", "--ff-only", "origin", base_branch, timeout_seconds=timeout_seconds)
        if pull_result.status != "PASS":
            raise RuntimeError(_result_detail(pull_result) or f"git pull --ff-only origin {base_branch} failed")
        switch_back = self._git("git", "switch", branch, timeout_seconds=timeout_seconds)
        if switch_back.status != "PASS":
            raise RuntimeError(_result_detail(switch_back) or f"git switch {branch} failed")
        merge_result = self._git("git", "merge", "--no-edit", base_branch, timeout_seconds=timeout_seconds)
        if merge_result.status != "PASS":
            raise RuntimeError(_result_detail(merge_result) or f"git merge --no-edit {base_branch} failed")

    def sync_branch_with_base(self, branch: str, *, base_branch: str = "master", base_head_sha: str | None = None) -> None:
        _validate_branch_name(branch)
        _validate_branch_name(base_branch, field_name="base_branch")
        current = self.status()
        if current.branch != branch:
            raise RuntimeError(f"current branch {current.branch!r} does not match {branch!r}")
        branch_head_sha = self.head_sha()
        resolved_base_result = self._git("git", "rev-parse", base_branch)
        resolved_base_head = base_head_sha or (resolved_base_result.stdout_lines[0].strip() if resolved_base_result.status == "PASS" and resolved_base_result.stdout_lines else "")
        if not resolved_base_head:
            raise RuntimeError(f"cannot resolve {base_branch} head")
        if self.is_ancestor(branch_head_sha, resolved_base_head):
            self.merge_ff_only(base_branch)
            return
        if self.is_ancestor(resolved_base_head, branch_head_sha):
            return
        raise RuntimeError(f"{branch} and {base_branch} have diverged")

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

    def push(self, branch: str, remote: str = "origin", *, timeout_seconds: int = 20) -> process_runner.ProcessResult:
        _validate_branch_name(branch)
        _validate_branch_name(remote, field_name="remote")
        return self._git("git", "push", "-u", remote, branch, timeout_seconds=timeout_seconds)

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


def _is_allowed_ff_only_merge(args: Sequence[str]) -> bool:
    parts = tuple(args)
    return len(parts) == 4 and parts[:3] == ("git", "merge", "--ff-only") and bool(parts[3].strip())


def _is_allowed_no_edit_merge(args: Sequence[str]) -> bool:
    parts = tuple(args)
    return len(parts) == 4 and parts[:3] == ("git", "merge", "--no-edit") and bool(parts[3].strip())


def _validate_branch_name(branch: str, *, field_name: str = "branch") -> str:
    if not isinstance(branch, str) or not branch.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    normalized = branch.strip()
    if not BRANCH_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} is not a valid git branch name")
    forbidden = ("..", "//", "@{", "\\", " ", "\t", "\n", "\r", "~", "^", ":", "?", "*", "[", "]")
    if normalized.startswith("-") or normalized.endswith("/") or normalized.endswith(".") or normalized.endswith(".lock"):
        raise ValueError(f"{field_name} is not a valid git branch name")
    if any(token in normalized for token in forbidden):
        raise ValueError(f"{field_name} is not a valid git branch name")
    return normalized


def _result_detail(result: process_runner.ProcessResult) -> str | None:
    for line in (*result.stderr_lines, *result.stdout_lines):
        cleaned = line.strip()
        if cleaned:
            return cleaned
    return None


__all__ = ["GitStatus", "Repository", "ROOT"]

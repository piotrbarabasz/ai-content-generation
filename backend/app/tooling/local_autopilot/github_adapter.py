"""Adapter for the local GitHub CLI used by the autopilot."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from . import process_runner
from .models import PullRequestInfo

ROOT = Path(__file__).resolve().parents[4]
GITHUB_PR_FIELDS = "number,url,title,baseRefName,headRefName,isDraft,state,mergedAt"


@dataclass(frozen=True)
class GitHubAuthResult:
    available: bool
    authenticated: bool
    command: tuple[str, ...]
    status: str
    exit_code: int | None
    reason: str | None = None
    stdout_lines: tuple[str, ...] = ()
    stderr_lines: tuple[str, ...] = ()
    raw_output: str = ""


class GitHubAdapter:
    def __init__(
        self,
        root: Path | str = ROOT,
        *,
        process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
    ) -> None:
        self.root = Path(root)
        self._run = process_runner_fn

    def validate_auth(self, *, timeout_seconds: int = 20) -> GitHubAuthResult:
        result = self._run(
            ["gh", "auth", "status"],
            cwd=self.root,
            timeout_seconds=timeout_seconds,
            heartbeat_seconds=0,
        )
        raw_output = _combine_output(result.stdout_lines, result.stderr_lines)
        if result.status == "MISSING":
            return GitHubAuthResult(
                available=False,
                authenticated=False,
                command=tuple(result.command),
                status=result.status,
                exit_code=result.exit_code,
                reason="gh CLI is missing",
                stdout_lines=result.stdout_lines,
                stderr_lines=result.stderr_lines,
                raw_output=raw_output,
            )
        authenticated = result.status == "PASS" and result.exit_code == 0
        reason = None
        if not authenticated:
            reason = _short_reason(raw_output) or "gh authentication check failed"
        return GitHubAuthResult(
            available=True,
            authenticated=authenticated,
            command=tuple(result.command),
            status=result.status,
            exit_code=result.exit_code,
            reason=reason,
            stdout_lines=result.stdout_lines,
            stderr_lines=result.stderr_lines,
            raw_output=raw_output,
        )

    def find_pr(
        self,
        base: str,
        head: str,
        *,
        timeout_seconds: int = 30,
    ) -> PullRequestInfo | None:
        payload = self._pr_list_json(base, head, timeout_seconds=timeout_seconds)
        if not isinstance(payload, list):
            return None
        for item in payload:
            pr = _pull_request_from_json(item)
            if pr is None:
                continue
            if pr.base_branch == base and pr.head_branch == head:
                return pr
        return None

    def create_draft_pr(
        self,
        base: str,
        head: str,
        title: str,
        body: str,
        *,
        timeout_seconds: int = 120,
    ) -> PullRequestInfo:
        existing = self.find_pr(base, head, timeout_seconds=min(timeout_seconds, 30))
        if existing is not None:
            return existing
        result = self._run(
            [
                "gh",
                "pr",
                "create",
                "--draft",
                "--base",
                base,
                "--head",
                head,
                "--title",
                title,
                "--body",
                body,
            ],
            cwd=self.root,
            timeout_seconds=timeout_seconds,
        )
        if result.status != "PASS" or result.exit_code not in (0, None):
            raise RuntimeError(_short_reason(_combine_output(result.stdout_lines, result.stderr_lines)) or "gh pr create failed")
        created = self.find_pr(base, head, timeout_seconds=min(timeout_seconds, 30))
        if created is None:
            raise RuntimeError("created pull request could not be verified")
        return created

    def get_pr_status(self, number: int, *, timeout_seconds: int = 30) -> PullRequestInfo | None:
        if number <= 0:
            raise ValueError("pull request number must be positive")
        result = self._run(
            ["gh", "pr", "view", str(number), "--json", GITHUB_PR_FIELDS],
            cwd=self.root,
            timeout_seconds=timeout_seconds,
            heartbeat_seconds=0,
        )
        if result.status != "PASS":
            return None
        payload = _parse_json_object(_combine_output(result.stdout_lines, result.stderr_lines))
        return _pull_request_from_json(payload)

    def is_pr_merged(self, number: int, *, timeout_seconds: int = 30) -> bool:
        pr = self.get_pr_status(number, timeout_seconds=timeout_seconds)
        return bool(pr and pr.merged)

    def open_pr_in_browser(self, number: int, *, timeout_seconds: int = 30) -> bool:
        if number <= 0:
            raise ValueError("pull request number must be positive")
        result = self._run(
            ["gh", "pr", "view", str(number), "--web"],
            cwd=self.root,
            timeout_seconds=timeout_seconds,
            heartbeat_seconds=0,
        )
        return result.status == "PASS"

    def _pr_list_json(self, base: str, head: str, *, timeout_seconds: int) -> Any:
        result = self._run(
            [
                "gh",
                "pr",
                "list",
                "--base",
                base,
                "--head",
                head,
                "--state",
                "all",
                "--json",
                GITHUB_PR_FIELDS,
            ],
            cwd=self.root,
            timeout_seconds=timeout_seconds,
            heartbeat_seconds=0,
        )
        if result.status != "PASS":
            return None
        return _parse_json_object(_combine_output(result.stdout_lines, result.stderr_lines))


def validate_auth(
    root: Path | str = ROOT,
    *,
    timeout_seconds: int = 20,
    process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
) -> GitHubAuthResult:
    return GitHubAdapter(root, process_runner_fn=process_runner_fn).validate_auth(timeout_seconds=timeout_seconds)


def find_pr(
    base: str,
    head: str,
    *,
    root: Path | str = ROOT,
    timeout_seconds: int = 30,
    process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
) -> PullRequestInfo | None:
    return GitHubAdapter(root, process_runner_fn=process_runner_fn).find_pr(base, head, timeout_seconds=timeout_seconds)


def create_draft_pr(
    base: str,
    head: str,
    title: str,
    body: str,
    *,
    root: Path | str = ROOT,
    timeout_seconds: int = 120,
    process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
) -> PullRequestInfo:
    return GitHubAdapter(root, process_runner_fn=process_runner_fn).create_draft_pr(
        base,
        head,
        title,
        body,
        timeout_seconds=timeout_seconds,
    )


def get_pr_status(
    number: int,
    *,
    root: Path | str = ROOT,
    timeout_seconds: int = 30,
    process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
) -> PullRequestInfo | None:
    return GitHubAdapter(root, process_runner_fn=process_runner_fn).get_pr_status(number, timeout_seconds=timeout_seconds)


def is_pr_merged(
    number: int,
    *,
    root: Path | str = ROOT,
    timeout_seconds: int = 30,
    process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
) -> bool:
    return GitHubAdapter(root, process_runner_fn=process_runner_fn).is_pr_merged(number, timeout_seconds=timeout_seconds)


def open_pr_in_browser(
    number: int,
    *,
    root: Path | str = ROOT,
    timeout_seconds: int = 30,
    process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
) -> bool:
    return GitHubAdapter(root, process_runner_fn=process_runner_fn).open_pr_in_browser(number, timeout_seconds=timeout_seconds)


def _combine_output(stdout_lines: Sequence[str], stderr_lines: Sequence[str]) -> str:
    parts = [line for line in list(stdout_lines) + list(stderr_lines) if line]
    return "\n".join(parts)


def _short_reason(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    return text.splitlines()[0].strip()


def _parse_json_object(text: str) -> Any:
    if not text.strip():
        return None
    return json.loads(text)


def _pull_request_from_json(payload: Any) -> PullRequestInfo | None:
    if not isinstance(payload, dict):
        return None
    number = payload.get("number")
    url = payload.get("url")
    title = payload.get("title")
    base_branch = payload.get("baseRefName")
    head_branch = payload.get("headRefName")
    if not all(
        isinstance(value, str) and value.strip() for value in (url, title, base_branch, head_branch)
    ):
        return None
    if not isinstance(number, int) or number <= 0:
        return None
    draft = bool(payload.get("isDraft", True))
    merged = payload.get("mergedAt") is not None or str(payload.get("state") or "").lower() == "merged"
    return PullRequestInfo(
        number=number,
        url=url,
        title=title,
        base_branch=base_branch,
        head_branch=head_branch,
        draft=draft,
        merged=merged,
    )


__all__ = [
    "GitHubAdapter",
    "GitHubAuthResult",
    "create_draft_pr",
    "find_pr",
    "get_pr_status",
    "is_pr_merged",
    "open_pr_in_browser",
    "validate_auth",
]

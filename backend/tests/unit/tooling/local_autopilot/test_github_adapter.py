from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.tooling.local_autopilot.github_adapter import (
    GitHubAdapter,
    GitHubAuthResult,
    create_draft_pr,
    find_pr,
    get_pr_status,
    is_pr_merged,
    open_pr_in_browser,
    validate_auth,
)
from app.tooling.local_autopilot.models import PullRequestInfo


@dataclass
class FakeProcessResult:
    command: tuple[str, ...]
    status: str = "PASS"
    exit_code: int | None = 0
    timed_out: bool = False
    cancelled: bool = False
    stdout_lines: tuple[str, ...] = ()
    stderr_lines: tuple[str, ...] = ()
    output_truncated: bool = False
    process_tree_killed: bool = False
    pid: int | None = 1234


def test_validate_auth_reports_missing_cli(tmp_path):
    def fake_run(argv, **kwargs):
        return FakeProcessResult(command=tuple(argv), status="MISSING", exit_code=None)

    result = validate_auth(tmp_path, process_runner_fn=fake_run)

    assert isinstance(result, GitHubAuthResult)
    assert result.available is False
    assert result.authenticated is False
    assert result.reason == "gh CLI is missing"
    assert result.command == ("gh", "auth", "status")


def test_validate_auth_reports_authenticated_state(tmp_path):
    def fake_run(argv, **kwargs):
        return FakeProcessResult(
            command=tuple(argv),
            status="PASS",
            exit_code=0,
            stdout_lines=("github.com", "  Logged in to github.com as tester"),
        )

    result = validate_auth(tmp_path, process_runner_fn=fake_run)

    assert result.available is True
    assert result.authenticated is True
    assert result.reason is None


def test_find_pr_returns_exact_match_from_json_list(tmp_path):
    calls: list[tuple[str, ...]] = []
    payload = [
        {
            "number": 1,
            "url": "https://example.invalid/pr/1",
            "title": "Wrong branch",
            "baseRefName": "main",
            "headRefName": "other",
            "isDraft": True,
            "state": "OPEN",
            "mergedAt": None,
        },
        {
            "number": 7,
            "url": "https://example.invalid/pr/7",
            "title": "Target PR",
            "baseRefName": "main",
            "headRefName": "feature/autopilot",
            "isDraft": True,
            "state": "OPEN",
            "mergedAt": None,
        },
    ]

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command[:3] == ("gh", "pr", "list"):
            return FakeProcessResult(command=command, stdout_lines=(json.dumps(payload),))
        raise AssertionError(command)

    pr = find_pr("main", "feature/autopilot", root=tmp_path, process_runner_fn=fake_run)

    assert pr == PullRequestInfo(
        number=7,
        url="https://example.invalid/pr/7",
        title="Target PR",
        base_branch="main",
        head_branch="feature/autopilot",
        draft=True,
        merged=False,
    )
    assert calls == [("gh", "pr", "list", "--base", "main", "--head", "feature/autopilot", "--state", "all", "--json", "number,url,title,baseRefName,headRefName,isDraft,state,mergedAt")]


def test_create_draft_pr_is_idempotent_when_pr_already_exists(tmp_path):
    calls: list[tuple[str, ...]] = []
    payload = [
        {
            "number": 9,
            "url": "https://example.invalid/pr/9",
            "title": "Existing PR",
            "baseRefName": "main",
            "headRefName": "feature/autopilot",
            "isDraft": True,
            "state": "OPEN",
            "mergedAt": None,
        }
    ]

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command[:3] == ("gh", "pr", "list"):
            return FakeProcessResult(command=command, stdout_lines=(json.dumps(payload),))
        raise AssertionError(f"unexpected create path: {command}")

    pr = create_draft_pr(
        "main",
        "feature/autopilot",
        "Autopilot PR",
        "Body",
        root=tmp_path,
        process_runner_fn=fake_run,
    )

    assert pr.number == 9
    assert all(command[:3] == ("gh", "pr", "list") for command in calls)


def test_create_draft_pr_creates_then_refetches_status(tmp_path):
    calls: list[tuple[str, ...]] = []
    payload = [
        {
            "number": 11,
            "url": "https://example.invalid/pr/11",
            "title": "Autopilot PR",
            "baseRefName": "main",
            "headRefName": "feature/autopilot",
            "isDraft": True,
            "state": "OPEN",
            "mergedAt": None,
        }
    ]

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command[:3] == ("gh", "pr", "list"):
            if len(calls) == 1:
                return FakeProcessResult(command=command, stdout_lines=("[]",))
            return FakeProcessResult(command=command, stdout_lines=(json.dumps(payload),))
        if command[:3] == ("gh", "pr", "create"):
            return FakeProcessResult(command=command, stdout_lines=("created",))
        raise AssertionError(command)

    pr = create_draft_pr(
        "main",
        "feature/autopilot",
        "Autopilot PR",
        "Body",
        root=tmp_path,
        process_runner_fn=fake_run,
    )

    assert pr.number == 11
    assert calls[0][:3] == ("gh", "pr", "list")
    assert calls[1][:3] == ("gh", "pr", "create")
    assert calls[2][:3] == ("gh", "pr", "list")
    assert calls[1][3:] == ("--draft", "--base", "main", "--head", "feature/autopilot", "--title", "Autopilot PR", "--body", "Body")


def test_get_pr_status_and_is_pr_merged(tmp_path):
    payload = {
        "number": 22,
        "url": "https://example.invalid/pr/22",
        "title": "Merged PR",
        "baseRefName": "main",
        "headRefName": "feature/autopilot",
        "isDraft": False,
        "state": "MERGED",
        "mergedAt": "2026-07-23T10:00:00Z",
    }

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        if command[:3] == ("gh", "pr", "view"):
            return FakeProcessResult(command=command, stdout_lines=(json.dumps(payload),))
        raise AssertionError(command)

    status = get_pr_status(22, root=tmp_path, process_runner_fn=fake_run)

    assert status == PullRequestInfo(
        number=22,
        url="https://example.invalid/pr/22",
        title="Merged PR",
        base_branch="main",
        head_branch="feature/autopilot",
        draft=False,
        merged=True,
    )
    assert is_pr_merged(22, root=tmp_path, process_runner_fn=fake_run) is True


def test_open_pr_in_browser_uses_web_flag(tmp_path):
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        return FakeProcessResult(command=command, stdout_lines=("opened",))

    result = open_pr_in_browser(22, root=tmp_path, process_runner_fn=fake_run)

    assert result is True
    assert calls == [("gh", "pr", "view", "22", "--web")]

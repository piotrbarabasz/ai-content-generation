"""Typed runtime models for the local autopilot."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

EPIC_ID_PATTERN = re.compile(r"^E\d{3}$")
MILESTONE_ID_PATTERN = re.compile(r"^M\d{3}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ScopeType(str, Enum):
    EPIC = "epic"
    MILESTONE = "milestone"


class RunMode(str, Enum):
    FULL = "full"
    STOP_BEFORE_PUSH = "stop_before_push"


class RunStatus(str, Enum):
    IDLE = "idle"
    PREFLIGHT = "preflight"
    ACTIVATING = "activating"
    BRANCHING = "branching"
    TASK_RUNNING = "task_running"
    TASK_VALIDATING = "task_validating"
    TASK_COMMITTING = "task_committing"
    EPIC_REVIEW = "epic_review"
    PUSHING = "pushing"
    PR_CREATING = "pr_creating"
    WAITING_FOR_MERGE = "waiting_for_merge"
    CLOSING = "closing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AutopilotRequest:
    scope_type: ScopeType
    scope_id: str
    run_mode: RunMode
    repo_path: str
    created_by: str = "user"
    human_authorized: bool = True

    def __post_init__(self) -> None:
        _validate_scope_id(self.scope_type, self.scope_id)
        _validate_repo_path(self.repo_path)


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    status: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool
    stdout_lines: tuple[str, ...] = ()
    stderr_lines: tuple[str, ...] = ()
    output_truncated: bool = False

    def __post_init__(self) -> None:
        _validate_command(self.command)


@dataclass(frozen=True)
class PullRequestInfo:
    number: int
    url: str
    title: str
    base_branch: str
    head_branch: str
    draft: bool = True
    merged: bool = False

    def __post_init__(self) -> None:
        if self.number <= 0:
            raise ValueError("pull request number must be positive")
        for field_name, value in {
            "url": self.url,
            "title": self.title,
            "base_branch": self.base_branch,
            "head_branch": self.head_branch,
        }.items():
            _validate_non_empty_text(field_name, value)


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    status: RunStatus
    command_results: tuple[CommandResult, ...] = ()
    commit_sha: str | None = None
    title: str | None = None

    def __post_init__(self) -> None:
        _validate_task_id(self.task_id)
        if self.title is not None:
            _validate_non_empty_text("title", self.title)
        if self.commit_sha is not None:
            _validate_commit_sha(self.commit_sha)


@dataclass(frozen=True)
class AutopilotRun:
    run_id: str
    request: AutopilotRequest
    status: RunStatus
    created_at: str
    updated_at: str
    epic_id: str | None = None
    milestone_id: str | None = None
    branch_name: str | None = None
    current_task_id: str | None = None
    task_results: tuple[TaskResult, ...] = ()
    command_results: tuple[CommandResult, ...] = ()
    pull_request: PullRequestInfo | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        _validate_run_id(self.run_id)
        _validate_timestamp(self.created_at)
        _validate_timestamp(self.updated_at)
        if self.epic_id is not None:
            _validate_scope_id(ScopeType.EPIC, self.epic_id)
        if self.milestone_id is not None:
            _validate_scope_id(ScopeType.MILESTONE, self.milestone_id)
        if self.current_task_id is not None:
            _validate_task_id(self.current_task_id)
        if self.branch_name is not None:
            _validate_non_empty_text("branch_name", self.branch_name)
        if self.last_error is not None:
            _validate_non_empty_text("last_error", self.last_error)


def _validate_non_empty_text(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _validate_command(command: Sequence[str]) -> None:
    if not isinstance(command, Sequence) or isinstance(command, (str, bytes, bytearray)):
        raise ValueError("command must be a sequence of strings")
    if not command:
        raise ValueError("command must not be empty")
    for part in command:
        if not isinstance(part, str) or not part.strip():
            raise ValueError("command entries must be non-empty strings")


def _validate_task_id(task_id: str) -> None:
    _validate_non_empty_text("task_id", task_id)
    if not re.fullmatch(r"T\d{3}[A-Z]?", task_id):
        raise ValueError("task_id must match T### or T###A")


def _validate_commit_sha(commit_sha: str) -> None:
    _validate_non_empty_text("commit_sha", commit_sha)
    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise ValueError("commit_sha must be a 40-character lowercase hexadecimal SHA")


def _validate_scope_id(scope_type: ScopeType, scope_id: str) -> None:
    _validate_non_empty_text("scope_id", scope_id)
    pattern = EPIC_ID_PATTERN if scope_type is ScopeType.EPIC else MILESTONE_ID_PATTERN
    if not pattern.fullmatch(scope_id):
        raise ValueError(f"{scope_type.value} scope_id must match {pattern.pattern}")


def _validate_run_id(run_id: str) -> None:
    _validate_non_empty_text("run_id", run_id)
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id must be a safe filename-style identifier")


def _validate_repo_path(repo_path: str) -> None:
    _validate_non_empty_text("repo_path", repo_path)


def _validate_timestamp(value: str) -> None:
    _validate_non_empty_text("timestamp", value)


__all__ = [
    "AutopilotRequest",
    "AutopilotRun",
    "CommandResult",
    "PullRequestInfo",
    "RunMode",
    "RunStatus",
    "ScopeType",
    "TaskResult",
]

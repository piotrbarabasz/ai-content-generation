"""Merge evidence validation for closing a completed epic."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[3]
TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class MergeEvidenceResult:
    """Structured merge evidence assessment for epic close."""

    valid: bool
    strategy: str
    squash_supported: bool
    local_fallback: bool
    reasons: tuple[str, ...]
    details: dict[str, Any]


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({"GIT_PAGER": "cat", "PAGER": "cat", "TERM": "dumb"})
    return env


def _run_git(command: list[str], git_runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run) -> subprocess.CompletedProcess[Any]:
    return git_runner(
        command,
        cwd=ROOT,
        timeout=TIMEOUT_SECONDS,
        check=False,
        capture_output=True,
        text=True,
        env=_git_env(),
    )


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_pr_field(pr_metadata: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in pr_metadata:
            return pr_metadata[key]
    return None


def _extract_nested_field(pr_metadata: Mapping[str, Any], *keys: str) -> Any:
    current: Any = pr_metadata
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _validate_github_pr_metadata(
    *,
    epic_id: str,
    epic_branch: str,
    base_branch: str,
    epic_head_sha: str | None,
    base_sha: str | None,
    pr_metadata: Mapping[str, Any],
) -> MergeEvidenceResult:
    reasons: list[str] = []
    state = str(_extract_pr_field(pr_metadata, "state") or "").lower()
    merged = _extract_pr_field(pr_metadata, "merged")
    if not (merged is True or state == "merged"):
        reasons.append("GitHub PR metadata does not indicate a merged PR")
    if state == "closed" and merged is not True:
        reasons.append("closed PR metadata is not merge evidence")
    head_ref = _normalize_text(
        _extract_pr_field(pr_metadata, "headRefName") or _extract_nested_field(pr_metadata, "head", "ref")
    )
    base_ref = _normalize_text(
        _extract_pr_field(pr_metadata, "baseRefName") or _extract_nested_field(pr_metadata, "base", "ref")
    )
    if head_ref != epic_branch:
        reasons.append(f"head branch must be {epic_branch!r}")
    if base_ref != base_branch:
        reasons.append(f"base branch must be {base_branch!r}")
    merged_at = _extract_pr_field(pr_metadata, "mergedAt") or _extract_pr_field(pr_metadata, "merged_at")
    merge_commit = (
        _extract_pr_field(pr_metadata, "mergeCommit")
        or _extract_pr_field(pr_metadata, "merge_commit")
        or _extract_pr_field(pr_metadata, "merge_commit_sha")
        or _extract_nested_field(pr_metadata, "mergeCommit", "oid")
        or _extract_nested_field(pr_metadata, "mergeCommit", "sha")
    )
    if merged_at is None and merge_commit is None:
        reasons.append("mergedAt or merge commit metadata is required")
    if epic_head_sha is not None:
        observed_head = _normalize_text(
            _extract_pr_field(pr_metadata, "headRefOid")
            or _extract_pr_field(pr_metadata, "head_sha")
            or _extract_nested_field(pr_metadata, "head", "sha")
        )
        if observed_head and observed_head != epic_head_sha:
            reasons.append("head SHA does not match the epic branch HEAD")
    if base_sha is not None:
        observed_base = _normalize_text(
            _extract_pr_field(pr_metadata, "baseRefOid")
            or _extract_pr_field(pr_metadata, "base_sha")
            or _extract_nested_field(pr_metadata, "base", "sha")
        )
        if observed_base and observed_base != base_sha:
            reasons.append("base SHA does not match the manifest base")
    valid = not reasons
    return MergeEvidenceResult(
        valid=valid,
        strategy="github_pr_metadata",
        squash_supported=True,
        local_fallback=False,
        reasons=tuple(reasons),
        details={
            "epic_id": epic_id,
            "epic_branch": epic_branch,
            "base_branch": base_branch,
            "state": state,
            "merged": merged,
            "merged_at": merged_at,
            "merge_commit": merge_commit,
        },
    )


def _validate_local_ancestry(
    *,
    epic_id: str,
    epic_head_sha: str | None,
    base_branch: str,
    git_runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> MergeEvidenceResult:
    reasons: list[str] = []
    if epic_head_sha is None:
        reasons.append("local ancestry fallback requires the epic HEAD SHA")
        return MergeEvidenceResult(
            valid=False,
            strategy="local_ancestry",
            squash_supported=False,
            local_fallback=True,
            reasons=tuple(reasons),
            details={"epic_id": epic_id, "base_branch": base_branch},
        )
    ancestry_result = _run_git(["git", "merge-base", "--is-ancestor", epic_head_sha, base_branch], git_runner)
    if ancestry_result.returncode != 0:
        reasons.append("local ancestry does not prove that the epic HEAD is in the base branch history")
        reasons.append("squash merges cannot be proven by local ancestry alone")
    history_result = _run_git(["git", "log", "--oneline", "--first-parent", base_branch], git_runner)
    if history_result.returncode != 0:
        reasons.append("cannot read base branch first-parent history")
    valid = not reasons
    return MergeEvidenceResult(
        valid=valid,
        strategy="local_ancestry",
        squash_supported=False,
        local_fallback=True,
        reasons=tuple(reasons),
        details={
            "epic_id": epic_id,
            "epic_head_sha": epic_head_sha,
            "base_branch": base_branch,
            "first_parent_history": history_result.stdout or "",
        },
    )


def evaluate_merge_evidence(
    *,
    epic_id: str,
    epic_branch: str,
    base_branch: str,
    epic_head_sha: str | None = None,
    base_sha: str | None = None,
    github_pr: Mapping[str, Any] | None = None,
    github_integration_available: bool = False,
    git_runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> MergeEvidenceResult:
    """Evaluate merge evidence, preferring GitHub metadata over local ancestry."""
    if github_pr is not None:
        return _validate_github_pr_metadata(
            epic_id=epic_id,
            epic_branch=epic_branch,
            base_branch=base_branch,
            epic_head_sha=epic_head_sha,
            base_sha=base_sha,
            pr_metadata=github_pr,
        )
    if github_integration_available:
        return MergeEvidenceResult(
            valid=False,
            strategy="github_pr_metadata",
            squash_supported=True,
            local_fallback=False,
            reasons=("GitHub PR metadata is unavailable",),
            details={"epic_id": epic_id, "epic_branch": epic_branch, "base_branch": base_branch},
        )
    return _validate_local_ancestry(
        epic_id=epic_id,
        epic_head_sha=epic_head_sha,
        base_branch=base_branch,
        git_runner=git_runner,
    )

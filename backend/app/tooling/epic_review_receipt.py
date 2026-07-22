"""Utilities for storing and validating active epic review receipts."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[3]
RECEIPTS_DIR = ROOT / ".specify" / "runtime" / "reviews"
TIMEOUT_SECONDS = 20
RECEIPT_SCHEMA_VERSION = 1
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({"GIT_PAGER": "cat", "PAGER": "cat", "TERM": "dumb"})
    return env


def _run_git_rev_parse(ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=ROOT,
        timeout=TIMEOUT_SECONDS,
        check=False,
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if result.returncode != 0:
        output = (result.stdout or "").strip() or (result.stderr or "").strip()
        raise RuntimeError(f"cannot resolve Git SHA for {ref!r}: {output or 'unknown error'}")
    sha = result.stdout.strip()
    if not SHA_PATTERN.fullmatch(sha):
        raise ValueError(f"git rev-parse {ref!r} returned invalid SHA {sha!r}")
    return sha


def capture_review_shas(base_branch: str) -> tuple[str, str]:
    """Return the current HEAD SHA and the declared base SHA using separate Git commands."""
    head_sha = _run_git_rev_parse("HEAD")
    base_sha = _run_git_rev_parse(base_branch)
    return head_sha, base_sha


def review_receipt_path(epic_id: str, root: Path = ROOT) -> Path:
    return root / ".specify" / "runtime" / "reviews" / f"{epic_id}.json"


def _normalize_required_checks(required_checks: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in required_checks or []:
        checks.append(dict(item))
    return checks


def build_review_receipt(
    *,
    epic_id: str,
    milestone_id: str,
    head_sha: str,
    base_sha: str,
    branch: str,
    base_branch: str,
    verdict: str,
    safe_to_create_pr: bool,
    required_checks: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Build the canonical receipt payload for an approved epic review."""
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "epic_id": epic_id,
        "milestone_id": milestone_id,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "branch": branch,
        "base_branch": base_branch,
        "verdict": verdict,
        "safe_to_create_pr": safe_to_create_pr,
        "required_checks": _normalize_required_checks(required_checks),
    }


def _validate_required_checks(required_checks: Any, expected_commands: Sequence[str] | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(required_checks, list) or not required_checks:
        errors.append("required_checks must be a non-empty list")
        return errors
    normalized_commands: list[str] = []
    for index, item in enumerate(required_checks):
        if not isinstance(item, dict):
            errors.append(f"required_checks[{index}] must be a mapping")
            continue
        command = item.get("command")
        exit_code = item.get("exit_code")
        if not isinstance(command, str) or not command.strip():
            errors.append(f"required_checks[{index}].command must be a non-empty string")
        else:
            normalized_commands.append(command)
        if not isinstance(exit_code, int):
            errors.append(f"required_checks[{index}].exit_code must be an integer")
        elif exit_code != 0:
            errors.append(f"required_checks[{index}].exit_code must be 0")
    if expected_commands is not None and list(expected_commands) != normalized_commands:
        errors.append("required_checks commands do not match the epic manifest")
    return errors


def validate_review_receipt(
    receipt: Any,
    *,
    epic_id: str,
    milestone_id: str,
    branch: str,
    base_branch: str,
    head_sha: str,
    base_sha: str,
    expected_required_commands: Sequence[str] | None = None,
) -> list[str]:
    """Validate a parsed review receipt against the current epic context."""
    errors: list[str] = []
    if not isinstance(receipt, dict):
        return ["review receipt must be a mapping"]
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {RECEIPT_SCHEMA_VERSION}")
    if receipt.get("epic_id") != epic_id:
        errors.append(f"epic_id must be {epic_id!r}")
    if receipt.get("milestone_id") != milestone_id:
        errors.append(f"milestone_id must be {milestone_id!r}")
    if receipt.get("branch") != branch:
        errors.append(f"branch must be {branch!r}")
    if receipt.get("base_branch") != base_branch:
        errors.append(f"base_branch must be {base_branch!r}")
    if receipt.get("head_sha") != head_sha:
        errors.append("head_sha does not match the current HEAD")
    if receipt.get("base_sha") != base_sha:
        errors.append("base_sha does not match the current base branch")
    if receipt.get("verdict") != "PASS":
        errors.append("verdict must be PASS")
    if receipt.get("safe_to_create_pr") is not True:
        errors.append("safe_to_create_pr must be true")
    errors.extend(_validate_required_checks(receipt.get("required_checks"), expected_required_commands))
    return errors


def load_review_receipt(path: Path) -> dict[str, Any]:
    """Load a receipt from disk and raise a deterministic error for invalid JSON."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"review receipt does not exist: {path}") from exc
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name}: review receipt must be a JSON object")
    return loaded


def validate_review_receipt_file(
    path: Path,
    *,
    epic_id: str,
    milestone_id: str,
    branch: str,
    base_branch: str,
    head_sha: str,
    base_sha: str,
    expected_required_commands: Sequence[str] | None = None,
) -> list[str]:
    """Load and validate a receipt file against the current epic context."""
    try:
        receipt = load_review_receipt(path)
    except (FileNotFoundError, ValueError) as exc:
        return [str(exc)]
    return validate_review_receipt(
        receipt,
        epic_id=epic_id,
        milestone_id=milestone_id,
        branch=branch,
        base_branch=base_branch,
        head_sha=head_sha,
        base_sha=base_sha,
        expected_required_commands=expected_required_commands,
    )


def write_review_receipt(
    *,
    epic_id: str,
    milestone_id: str,
    branch: str,
    base_branch: str,
    verdict: str,
    safe_to_create_pr: bool,
    required_checks: Sequence[Mapping[str, Any]] | None,
    receipt_root: Path = ROOT,
    head_sha: str | None = None,
    base_sha: str | None = None,
) -> Path:
    """Persist a PASS review receipt after verifying the review result and Git context."""
    if verdict != "PASS":
        raise ValueError("review receipt can only be written for a PASS verdict")
    if safe_to_create_pr is not True:
        raise ValueError("review receipt can only be written when safe_to_create_pr is true")
    check_errors = _validate_required_checks(required_checks)
    if check_errors:
        raise ValueError("; ".join(check_errors))
    if head_sha is None or base_sha is None:
        captured_head_sha, captured_base_sha = capture_review_shas(base_branch)
        head_sha = head_sha or captured_head_sha
        base_sha = base_sha or captured_base_sha
    receipt = build_review_receipt(
        epic_id=epic_id,
        milestone_id=milestone_id,
        head_sha=head_sha,
        base_sha=base_sha,
        branch=branch,
        base_branch=base_branch,
        verdict=verdict,
        safe_to_create_pr=safe_to_create_pr,
        required_checks=required_checks,
    )
    path = review_receipt_path(epic_id, receipt_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def delete_review_receipt(epic_id: str, receipt_root: Path = ROOT) -> bool:
    """Delete a receipt for the selected epic if it exists."""
    path = review_receipt_path(epic_id, receipt_root)
    if not path.exists():
        return False
    path.unlink()
    return True

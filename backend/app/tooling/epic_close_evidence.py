"""Merge evidence validation for closing a completed epic."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .workstream_validation import _load_yaml_manifest as _load_workstream_manifest

ROOT = Path(__file__).resolve().parents[3]
WORKSTREAMS_DIR = ROOT / ".specify" / "workstreams"
TIMEOUT_SECONDS = 20
EPIC_PATTERN = re.compile(r"^E\d{3}$")


@dataclass(frozen=True)
class MergeEvidenceResult:
    """Structured merge evidence assessment for epic close."""

    valid: bool
    strategy: str
    squash_supported: bool
    rebase_supported: bool
    local_fallback: bool
    reasons: tuple[str, ...]
    details: dict[str, Any]


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({"GIT_PAGER": "cat", "PAGER": "cat", "TERM": "dumb"})
    return env


def _run_git(
    command: list[str],
    git_runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> subprocess.CompletedProcess[Any]:
    return git_runner(
        command,
        cwd=ROOT,
        timeout=TIMEOUT_SECONDS,
        check=False,
        capture_output=True,
        text=True,
        env=_git_env(),
    )


def _git_stdout(command: list[str]) -> str:
    result = _run_git(command)
    if result.returncode != 0:
        output = (result.stdout or "").strip() or (result.stderr or "").strip()
        raise RuntimeError(f"git command failed: {' '.join(command)}: {output or 'unknown error'}")
    return result.stdout.strip()


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _validate_epic_id(epic_id: str) -> str:
    epic_id = epic_id.strip()
    if not EPIC_PATTERN.fullmatch(epic_id):
        raise ValueError("epic must match E###")
    return epic_id


def _epic_argument(value: str) -> str:
    try:
        return _validate_epic_id(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _load_json_mapping(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"PR metadata JSON does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name}: PR metadata must be a JSON object")
    return loaded


def _load_epic_manifest(epic_id: str, workstreams_dir: Path | None = None) -> tuple[Path, dict[str, Any]]:
    workstreams_dir = WORKSTREAMS_DIR if workstreams_dir is None else workstreams_dir
    if not workstreams_dir.is_dir():
        raise FileNotFoundError(f"workstreams directory does not exist: {workstreams_dir}")
    for path in sorted(workstreams_dir.glob("*.yml")):
        manifest = _load_workstream_manifest(path)
        if manifest.get("id") == epic_id:
            return path, manifest
    raise FileNotFoundError(f"epic manifest does not exist: {epic_id}")


def _current_context(epic_id: str, workstreams_dir: Path | None = None) -> dict[str, Any]:
    epic_path, epic_manifest = _load_epic_manifest(epic_id, workstreams_dir)
    branch = _normalize_text(epic_manifest.get("branch"))
    base_branch = _normalize_text(epic_manifest.get("base_branch"))
    milestone = _normalize_text(epic_manifest.get("milestone"))
    if not branch:
        raise ValueError(f"{epic_path.name}: epic manifest missing branch")
    if not base_branch:
        raise ValueError(f"{epic_path.name}: epic manifest missing base_branch")
    if not milestone:
        raise ValueError(f"{epic_path.name}: epic manifest missing milestone")
    return {
        "epic_path": str(epic_path),
        "epic_id": epic_id,
        "milestone": milestone,
        "epic_branch": branch,
        "base_branch": base_branch,
    }


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
    details = {
        "epic_id": epic_id,
        "epic_branch": epic_branch,
        "base_branch": base_branch,
        "state": state,
        "merged": merged,
        "merged_at": merged_at,
        "merge_commit": merge_commit,
        "source": "github_pr_metadata",
    }
    return MergeEvidenceResult(
        valid=valid,
        strategy="github_pr_metadata",
        squash_supported=True,
        rebase_supported=True,
        local_fallback=False,
        reasons=tuple(reasons),
        details=details,
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
            rebase_supported=False,
            local_fallback=True,
            reasons=tuple(reasons),
            details={"epic_id": epic_id, "base_branch": base_branch, "source": "local_ancestry"},
        )
    ancestry_result = _run_git(["git", "merge-base", "--is-ancestor", epic_head_sha, base_branch], git_runner)
    if ancestry_result.returncode != 0:
        reasons.append("local ancestry does not prove that the epic HEAD is in the base branch history")
        reasons.append("squash merges cannot be proven by local ancestry alone")
    history_result = _run_git(["git", "log", "--oneline", "--first-parent", base_branch], git_runner)
    if history_result.returncode != 0:
        reasons.append("cannot read base branch first-parent history")
    valid = not reasons
    first_parent_history = history_result.stdout or ""
    history_kind = "fast_forward" if epic_head_sha in first_parent_history else "merge_commit"
    return MergeEvidenceResult(
        valid=valid,
        strategy="local_ancestry",
        squash_supported=False,
        rebase_supported=False,
        local_fallback=True,
        reasons=tuple(reasons),
        details={
            "epic_id": epic_id,
            "epic_head_sha": epic_head_sha,
            "base_branch": base_branch,
            "history_kind": history_kind,
            "supported_histories": ["merge_commit", "fast_forward"],
            "first_parent_history": first_parent_history,
            "source": "local_ancestry",
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
            rebase_supported=True,
            local_fallback=False,
            reasons=("GitHub PR metadata is unavailable",),
            details={"epic_id": epic_id, "epic_branch": epic_branch, "base_branch": base_branch, "source": "github_pr_metadata"},
        )
    return _validate_local_ancestry(
        epic_id=epic_id,
        epic_head_sha=epic_head_sha,
        base_branch=base_branch,
        git_runner=git_runner,
    )


def _load_pr_metadata_arg(path: Path | None) -> Mapping[str, Any] | None:
    if path is None:
        return None
    return _load_json_mapping(path)


def _result_payload(result: MergeEvidenceResult) -> dict[str, Any]:
    return {
        "valid": result.valid,
        "strategy": result.strategy,
        "squash_supported": result.squash_supported,
        "rebase_supported": result.rebase_supported,
        "reasons": list(result.reasons),
        "details": result.details,
    }


def _print_result(result: MergeEvidenceResult, *, json_mode: bool) -> None:
    payload = _result_payload(result)
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backend.app.tooling.epic_close_evidence")
    parser.add_argument("--epic", required=True, type=_epic_argument)
    parser.add_argument("--pr-metadata-json", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        context = _current_context(args.epic)
        epic_head_sha = _git_stdout(["git", "rev-parse", "HEAD"])
        base_sha = _git_stdout(["git", "rev-parse", context["base_branch"]])
        current_branch = _git_stdout(["git", "branch", "--show-current"])
        pr_metadata = _load_pr_metadata_arg(args.pr_metadata_json)
        result = evaluate_merge_evidence(
            epic_id=context["epic_id"],
            epic_branch=context["epic_branch"],
            base_branch=context["base_branch"],
            epic_head_sha=epic_head_sha,
            base_sha=base_sha,
            github_pr=pr_metadata,
            github_integration_available=pr_metadata is not None,
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError, RuntimeError, OSError) as exc:
        payload = {
            "valid": False,
            "strategy": "invalid_usage",
            "squash_supported": False,
            "rebase_supported": False,
            "reasons": [str(exc)],
            "details": {"epic_id": args.epic if hasattr(args, "epic") else None},
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for key, value in payload.items():
                print(f"{key}: {value}")
        return 1

    if current_branch != context["epic_branch"]:
        result = MergeEvidenceResult(
            valid=False,
            strategy=result.strategy,
            squash_supported=result.squash_supported,
            rebase_supported=result.rebase_supported,
            local_fallback=result.local_fallback,
            reasons=result.reasons + (f"current branch is {current_branch!r}, expected {context['epic_branch']!r}",),
            details={**result.details, "current_branch": current_branch},
        )

    final_details = {
        **result.details,
        "epic_id": context["epic_id"],
        "epic_branch": context["epic_branch"],
        "base_branch": context["base_branch"],
        "current_branch": current_branch,
        "head_sha": epic_head_sha,
        "base_sha": base_sha,
    }
    result = MergeEvidenceResult(
        valid=result.valid,
        strategy=result.strategy,
        squash_supported=result.squash_supported,
        rebase_supported=result.rebase_supported,
        local_fallback=result.local_fallback,
        reasons=result.reasons,
        details=final_details,
    )
    _print_result(result, json_mode=args.json)
    return 0 if result.valid else 1


if __name__ == "__main__":
    sys.exit(main())

"""Recovery helpers for interrupted local autopilot task attempts."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from .models import AutopilotRequest, AutopilotRun, RunMode, RunStatus, ScopeType
from .repository import GitStatus, Repository
from .state_store import load_run_state
from .scope_proposal import supersede_scope_expansion_proposal
from .task_state_machine import (
    TERMINAL_STATES,
    TaskLifecycleState,
    TaskReceiptRecord,
    TaskSnapshot,
    TaskStateRecord,
    _load_task_snapshot,
    load_task_receipt,
    load_task_state,
    save_task_receipt,
    save_task_state,
)
from .workstreams import get_epic

ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class TaskRecoveryAssessment:
    task_id: str
    epic_id: str
    branch: str
    current_state: TaskLifecycleState
    dirty_paths: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    unexpected_paths: tuple[str, ...]
    baseline_path: str
    baseline_head_sha: str
    current_head_sha: str
    can_resume: bool
    requires_baseline_refresh: bool
    reason: str


@dataclass(frozen=True)
class TaskRecoveryResult:
    assessment: TaskRecoveryAssessment
    backup_path: str | None
    archived_state_path: str | None
    archived_receipt_path: str | None
    archived_task_run_path: str | None
    prepared_state: TaskStateRecord | None
    prepared_receipt: TaskReceiptRecord | None
    codex_skipped: bool
    prepared: bool
    already_prepared: bool
    reason: str | None


def assess_task_recovery(
    task_id: str,
    run_id: str,
    root: Path | str = ROOT,
    *,
    repository: Repository | None = None,
) -> TaskRecoveryAssessment:
    root_path = Path(root)
    repo = repository or Repository(root_path)
    state_record = load_task_state(task_id, root=root_path)
    receipt_record = load_task_receipt(task_id, root=root_path)
    run = _load_run_or_default(run_id, root_path)
    status = repo.status()
    current_head_sha = status.head_sha or repo.head_sha()

    snapshot, epic_id, branch = _load_task_snapshot_with_context(task_id, state_record, run, root_path)
    allowed_paths = tuple(_normalize_paths(snapshot.allowlist))
    dirty_paths = _dirty_paths_from_status(status)
    unexpected_paths = tuple(path for path in dirty_paths if path not in allowed_paths)
    baseline_path = _baseline_path_from_state(state_record, root_path)
    baseline_head_sha, baseline_branch, baseline_exists = _load_baseline_context(baseline_path)
    active_epic = _load_active_epic(root_path)

    reasons: list[str] = []
    current_state = state_record.state if state_record is not None else TaskLifecycleState.PENDING
    if state_record is None:
        reasons.append("task state is missing")
    elif state_record.run_id and state_record.run_id != run_id:
        reasons.append(f"task state belongs to run {state_record.run_id!r}, expected {run_id!r}")

    if receipt_record is None:
        reasons.append("task receipt is missing")
    elif receipt_record.task_id and receipt_record.task_id != task_id:
        reasons.append(f"task receipt belongs to {receipt_record.task_id!r}, expected {task_id!r}")

    if run.epic_id and run.epic_id != epic_id:
        reasons.append(f"run epic {run.epic_id!r} does not match task epic {epic_id!r}")
    if run.branch_name and run.branch_name != branch:
        reasons.append(f"run branch {run.branch_name!r} does not match task branch {branch!r}")
    if active_epic and active_epic != epic_id:
        reasons.append(f"active epic {active_epic!r} does not match task epic {epic_id!r}")
    if status.branch != branch:
        reasons.append(f"current branch {status.branch!r} does not match task branch {branch!r}")
    epic_manifest = _load_epic_manifest_or_none(epic_id, root_path)
    if epic_manifest is None:
        reasons.append(f"epic manifest does not exist: {epic_id}")
        manifest_branch = ""
        manifest_status = ""
    else:
        manifest_branch = str(epic_manifest.get("branch") or "")
        manifest_status = str(epic_manifest.get("status") or "")
        if manifest_status != "active":
            reasons.append(f"epic {epic_id} is not active")
        if manifest_branch and manifest_branch != branch:
            reasons.append(f"epic branch {manifest_branch!r} does not match task branch {branch!r}")

    task_checkbox = snapshot.checkbox.upper()
    if task_checkbox != " ":
        reasons.append(f"task checkbox is [{snapshot.checkbox}]")
    if unexpected_paths:
        reasons.append("unexpected dirty paths: " + ", ".join(unexpected_paths))
    if not dirty_paths:
        reasons.append("worktree is clean; no recoverable dirty changes")
    if not baseline_exists:
        reasons.append("baseline is missing")
    elif baseline_branch and baseline_branch != branch:
        reasons.append(f"baseline branch {baseline_branch!r} does not match task branch {branch!r}")
    elif baseline_head_sha and baseline_head_sha != current_head_sha:
        reasons.append(f"baseline head {baseline_head_sha} does not match current head {current_head_sha}")

    can_resume = (
        state_record is not None
        and receipt_record is not None
        and current_state in TERMINAL_STATES
        and not unexpected_paths
        and bool(dirty_paths)
        and snapshot.checkbox.upper() == " "
        and baseline_exists
        and (not baseline_branch or baseline_branch == branch)
        and (not baseline_head_sha or baseline_head_sha == current_head_sha)
        and (not run.epic_id or run.epic_id == epic_id)
        and (not run.branch_name or run.branch_name == branch)
        and (not active_epic or active_epic == epic_id)
        and epic_manifest is not None
        and manifest_status == "active"
        and (not manifest_branch or manifest_branch == branch)
        and state_record.run_id == run_id
    )

    requires_baseline_refresh = bool(
        not baseline_exists
        or (baseline_branch and baseline_branch != branch)
        or (baseline_head_sha and baseline_head_sha != current_head_sha)
    )

    if can_resume:
        reasons = [f"task {task_id} can resume from terminal state {current_state.value}"]
        if dirty_paths:
            reasons.append(f"dirty paths: {', '.join(dirty_paths)}")
        reasons.append(f"allowed paths: {', '.join(allowed_paths) or 'none'}")
    elif not reasons:
        reasons.append(f"task {task_id} cannot resume")

    return TaskRecoveryAssessment(
        task_id=task_id,
        epic_id=epic_id,
        branch=branch,
        current_state=current_state,
        dirty_paths=dirty_paths,
        allowed_paths=allowed_paths,
        unexpected_paths=unexpected_paths,
        baseline_path=str(baseline_path),
        baseline_head_sha=baseline_head_sha,
        current_head_sha=current_head_sha,
        can_resume=can_resume,
        requires_baseline_refresh=requires_baseline_refresh,
        reason="; ".join(reason for reason in reasons if reason),
    )


def archive_task_attempt(task_id: str, root: Path | str = ROOT, *, reason: str, restore_baseline: bool = True) -> Path:
    root_path = Path(root)
    timestamp = _utc_timestamp()
    runtime_root = root_path / ".specify" / "runtime"
    backup_root = _unique_backup_dir(
        runtime_root / "recovery-backups",
        f"{_normalize_task_id(task_id)}-{_safe_timestamp_component(timestamp)}",
    )
    backup_root.mkdir(parents=True, exist_ok=False)

    state_path = runtime_root / "task-state" / f"{_normalize_task_id(task_id)}.json"
    receipt_path = runtime_root / "task-receipts" / f"{_normalize_task_id(task_id)}.json"
    task_run_path = runtime_root / "task-runs" / _normalize_task_id(task_id)

    if state_path.is_file():
        shutil.move(str(state_path), str(backup_root / f"{_normalize_task_id(task_id)}-state.json"))
    if receipt_path.is_file():
        shutil.move(str(receipt_path), str(backup_root / f"{_normalize_task_id(task_id)}-receipt.json"))
    archived_task_run_path = backup_root / f"task-run-{_normalize_task_id(task_id)}"
    if task_run_path.exists():
        shutil.move(str(task_run_path), str(archived_task_run_path))
        baseline_path = archived_task_run_path / "baseline.json"
        if restore_baseline and baseline_path.is_file():
            task_run_path.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(baseline_path), str(task_run_path / "baseline.json"))

    metadata = {
        "schema_version": 1,
        "task_id": _normalize_task_id(task_id),
        "reason": reason,
        "created_at": timestamp,
        "state_path": str(state_path),
        "receipt_path": str(receipt_path),
        "task_run_path": str(task_run_path),
    }
    (backup_root / "archive.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return backup_root


def archive_completed_epic_runtime(epic_id: str, root: Path | str = ROOT, *, reason: str) -> Path:
    root_path = Path(root)
    timestamp = _utc_timestamp()
    runtime_root = root_path / ".specify" / "runtime"
    archive_root = _unique_backup_dir(
        runtime_root / "archive" / _normalize_epic_id(epic_id),
        _safe_timestamp_component(timestamp),
    )
    archive_root.mkdir(parents=True, exist_ok=False)

    epic_manifest = _load_epic_manifest_or_none(epic_id, root_path)
    task_ids = tuple(_task_ids_from_epic_manifest(epic_manifest)) if epic_manifest is not None else ()
    archived_task_runs: list[str] = []
    task_runs_root = runtime_root / "task-runs"
    archive_task_runs_root = archive_root / "task-runs"
    for task_id in task_ids:
        task_run_path = task_runs_root / _normalize_task_id(task_id)
        if not task_run_path.exists():
            continue
        target = archive_task_runs_root / _normalize_task_id(task_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(task_run_path), str(target))
        archived_task_runs.append(_normalize_task_id(task_id))

    metadata = {
        "schema_version": 1,
        "epic_id": _normalize_epic_id(epic_id),
        "reason": reason,
        "created_at": timestamp,
        "task_ids": list(task_ids),
        "archived_task_runs": archived_task_runs,
    }
    (archive_root / "archive.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return archive_root


def prepare_task_retry(
    task_id: str,
    run_id: str,
    root: Path | str = ROOT,
    *,
    repository: Repository | None = None,
) -> TaskRecoveryResult:
    root_path = Path(root)
    repo = repository or Repository(root_path)
    assessment = assess_task_recovery(task_id, run_id, root_path, repository=repo)
    state_record = load_task_state(task_id, root=root_path)
    receipt_record = load_task_receipt(task_id, root=root_path)

    if state_record is not None and state_record.state == TaskLifecycleState.IMPLEMENTED and receipt_record is not None and receipt_record.state == TaskLifecycleState.IMPLEMENTED:
        supersede_scope_expansion_proposal(task_id, root_path)
        return TaskRecoveryResult(
            assessment=assessment,
            backup_path=None,
            archived_state_path=None,
            archived_receipt_path=None,
            archived_task_run_path=None,
            prepared_state=state_record,
            prepared_receipt=receipt_record,
            codex_skipped=True,
            prepared=False,
            already_prepared=True,
            reason=assessment.reason,
        )

    if not assessment.can_resume:
        return TaskRecoveryResult(
            assessment=assessment,
            backup_path=None,
            archived_state_path=None,
            archived_receipt_path=None,
            archived_task_run_path=None,
            prepared_state=state_record,
            prepared_receipt=receipt_record,
            codex_skipped=False,
            prepared=False,
            already_prepared=False,
            reason=assessment.reason,
        )

    backup_path = archive_task_attempt(task_id, root_path, reason=assessment.reason)
    if state_record is None or receipt_record is None:
        raise RuntimeError(f"unable to recover task attempt for {task_id}")

    current_status = repo.status()
    task_snapshot = _load_task_snapshot(task_id, Path(state_record.tasks_path))
    prepared_state = replace(
        state_record,
        state=TaskLifecycleState.IMPLEMENTED,
        updated_at=_utc_timestamp(),
        branch=current_status.branch,
        head_sha=current_status.head_sha,
        reason=assessment.reason,
        allowlist=task_snapshot.allowlist,
        validation_commands=task_snapshot.validation_commands,
    )
    save_task_state(prepared_state, root=root_path)

    dirty_allowlisted_paths = tuple(path for path in assessment.dirty_paths if path in assessment.allowed_paths)
    prepared_receipt = TaskReceiptRecord(
        schema_version=1,
        run_id=run_id,
        task_id=task_id,
        updated_at=_utc_timestamp(),
        state=TaskLifecycleState.IMPLEMENTED,
        agent_outcome="recovered",
        summary="task recovery prepared",
        files_touched=dirty_allowlisted_paths,
        notes=(
            "recovery prepared",
            f"dirty paths: {', '.join(dirty_allowlisted_paths) or 'none'}",
        ),
        validation=(),
        commit_sha="",
        stages=(),
        review_verdict="",
        safe_to_close=False,
        closure_checkbox_before="",
        closure_checkbox_after="",
        closure_task_line=0,
    )
    save_task_receipt(prepared_receipt, root=root_path)
    supersede_scope_expansion_proposal(task_id, root_path)

    return TaskRecoveryResult(
        assessment=assessment,
        backup_path=str(backup_path),
        archived_state_path=str(backup_path / f"{_normalize_task_id(task_id)}-state.json"),
        archived_receipt_path=str(backup_path / f"{_normalize_task_id(task_id)}-receipt.json"),
        archived_task_run_path=str(backup_path / f"task-run-{_normalize_task_id(task_id)}"),
        prepared_state=prepared_state,
        prepared_receipt=prepared_receipt,
        codex_skipped=True,
        prepared=True,
        already_prepared=False,
        reason=assessment.reason,
    )


def _load_run_or_default(run_id: str, root: Path) -> AutopilotRun:
    try:
        return load_run_state(run_id, root=root)
    except Exception:
        return AutopilotRun(
            run_id=run_id,
            request=_empty_request(root),
            status=RunStatus.IDLE,
            created_at=_utc_timestamp(),
            updated_at=_utc_timestamp(),
        )


def _empty_request(root: Path) -> AutopilotRequest:
    return AutopilotRequest(
        scope_type=ScopeType.EPIC,
        scope_id="E000",
        run_mode=RunMode.FULL,
        repo_path=str(root),
    )


def _load_task_snapshot_with_context(
    task_id: str,
    state_record: TaskStateRecord | None,
    run: AutopilotRun,
    root: Path,
) -> tuple[TaskSnapshot, str, str]:
    tasks_path: Path | None = None
    epic_id = ""
    branch = ""
    if state_record is not None and state_record.tasks_path:
        tasks_path = Path(state_record.tasks_path)
        branch = state_record.branch
    if tasks_path is None:
        tasks_path = root / "specs" / "001-ai-content-studio" / "tasks.md"
    snapshot = _load_task_snapshot(task_id, tasks_path)
    if not epic_id:
        epic_id = snapshot.epic_id
    if not branch:
        branch = state_record.branch if state_record is not None and state_record.branch else run.branch_name or ""
    if not epic_id:
        epic_id = run.epic_id or run.request.scope_id
    if not branch:
        branch = run.branch_name or ""
    return snapshot, epic_id, branch


def _load_epic_manifest_or_none(epic_id: str, root: Path) -> dict[str, object] | None:
    if not epic_id:
        return None
    try:
        return get_epic(epic_id, root / ".specify" / "workstreams")
    except Exception:
        return None


def _task_ids_from_epic_manifest(epic_manifest: dict[str, object] | None) -> Sequence[str]:
    if epic_manifest is None:
        return ()
    task_ids = epic_manifest.get("tasks") or ()
    if not isinstance(task_ids, Sequence) or isinstance(task_ids, (str, bytes, bytearray)):
        return ()
    return tuple(str(task_id).strip() for task_id in task_ids if str(task_id).strip())


def _baseline_path_from_state(state_record: TaskStateRecord | None, root: Path) -> Path:
    if state_record is not None and state_record.baseline_path:
        return Path(state_record.baseline_path)
    return root / ".specify" / "runtime" / "task-runs" / "unknown" / "baseline.json"


def _load_baseline_context(path: Path) -> tuple[str, str, bool]:
    if not path.is_file():
        return "", "", False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "", "", False
    if not isinstance(payload, dict):
        return "", "", False
    return str(payload.get("head_sha") or ""), str(payload.get("branch") or ""), True


def _dirty_paths_from_status(status: GitStatus) -> tuple[str, ...]:
    paths: list[str] = []
    for path in (*status.tracked, *status.staged, *status.untracked, *status.deleted):
        normalized = _normalize_path(path)
        if normalized and normalized not in paths:
            paths.append(normalized)
    for old_path, new_path in status.renamed:
        for path in (_normalize_path(old_path), _normalize_path(new_path)):
            if path and path not in paths:
                paths.append(path)
    return tuple(sorted(paths))


def _normalize_paths(paths: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for path in paths:
        candidate = _normalize_path(path)
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return tuple(normalized)


def _normalize_path(path: str) -> str:
    return str(path).replace("\\", "/").strip()


def _load_active_epic(root: Path) -> str:
    active_epic_file = Path(root) / ".specify" / "runtime" / "active-epic"
    if not active_epic_file.is_file():
        return ""
    return active_epic_file.read_text(encoding="utf-8").strip()


def _unique_backup_dir(base_dir: Path, name: str) -> Path:
    candidate = base_dir / name
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        candidate = base_dir / f"{name}-{index:02d}"
        if not candidate.exists():
            return candidate
        index += 1


def _normalize_task_id(task_id: str) -> str:
    value = str(task_id).strip()
    if not value:
        raise ValueError("task_id must be a non-empty string")
    return value


def _normalize_epic_id(epic_id: str) -> str:
    value = str(epic_id).strip()
    if not value or not re.fullmatch(r"E\d{3}", value):
        raise ValueError("epic_id must match E###")
    return value


def _utc_timestamp() -> str:
    from time import gmtime, strftime

    return strftime("%Y-%m-%dT%H:%M:%SZ", gmtime())


def _safe_timestamp_component(timestamp: str) -> str:
    return timestamp.replace(":", "-")


__all__ = [
    "TaskRecoveryAssessment",
    "TaskRecoveryResult",
    "assess_task_recovery",
    "archive_completed_epic_runtime",
    "archive_task_attempt",
    "prepare_task_retry",
]

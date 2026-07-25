"""Deterministic task lifecycle state machine and persistence helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from app.tooling import task_consistency

from .repository import GitStatus, Repository

ROOT = Path(__file__).resolve().parents[4]
TASK_ID_PATTERN = re.compile(r"^T\d{3}[A-Z]?$")
TASK_STATE_DIR = ROOT / ".specify" / "runtime" / "task-state"
TASK_RECEIPT_DIR = ROOT / ".specify" / "runtime" / "task-receipts"


class TaskLifecycleState(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    IMPLEMENTED = "IMPLEMENTED"
    VALIDATED = "VALIDATED"
    REVIEWED = "REVIEWED"
    CLOSED = "CLOSED"
    COMMITTED = "COMMITTED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ORDERED_STATES: tuple[TaskLifecycleState, ...] = (
    TaskLifecycleState.PENDING,
    TaskLifecycleState.READY,
    TaskLifecycleState.IMPLEMENTED,
    TaskLifecycleState.VALIDATED,
    TaskLifecycleState.REVIEWED,
    TaskLifecycleState.CLOSED,
    TaskLifecycleState.COMMITTED,
)

TERMINAL_STATES = {
    TaskLifecycleState.BLOCKED,
    TaskLifecycleState.FAILED,
    TaskLifecycleState.CANCELLED,
}


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    task_line: int
    checkbox: str
    title: str
    epic_id: str
    milestone_id: str
    implementation_files: tuple[str, ...]
    test_files: tuple[str, ...]
    validation_commands: tuple[str, ...]
    tasks_path: str

    @property
    def allowlist(self) -> tuple[str, ...]:
        return tuple([*self.implementation_files, *self.test_files])


@dataclass(frozen=True)
class TaskStateRecord:
    schema_version: int
    run_id: str
    task_id: str
    state: TaskLifecycleState
    updated_at: str
    branch: str = ""
    head_sha: str = ""
    baseline_path: str = ""
    baseline_branch: str = ""
    baseline_head_sha: str = ""
    tasks_path: str = ""
    feature_dir: str = ""
    allowlist: tuple[str, ...] = ()
    validation_commands: tuple[str, ...] = ()
    task_line: int = 0
    reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "state": self.state.value,
            "updated_at": self.updated_at,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "baseline_path": self.baseline_path,
            "baseline_branch": self.baseline_branch,
            "baseline_head_sha": self.baseline_head_sha,
            "tasks_path": self.tasks_path,
            "feature_dir": self.feature_dir,
            "allowlist": list(self.allowlist),
            "validation_commands": list(self.validation_commands),
            "task_line": self.task_line,
            "reason": self.reason,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TaskStateRecord":
        return cls(
            schema_version=int(payload.get("schema_version", 1)),
            run_id=str(payload.get("run_id") or ""),
            task_id=str(payload.get("task_id") or ""),
            state=TaskLifecycleState(str(payload.get("state") or "PENDING")),
            updated_at=str(payload.get("updated_at") or ""),
            branch=str(payload.get("branch") or ""),
            head_sha=str(payload.get("head_sha") or ""),
            baseline_path=str(payload.get("baseline_path") or ""),
            baseline_branch=str(payload.get("baseline_branch") or ""),
            baseline_head_sha=str(payload.get("baseline_head_sha") or ""),
            tasks_path=str(payload.get("tasks_path") or ""),
            feature_dir=str(payload.get("feature_dir") or ""),
            allowlist=tuple(str(item) for item in payload.get("allowlist", ()) or ()),
            validation_commands=tuple(str(item) for item in payload.get("validation_commands", ()) or ()),
            task_line=int(payload.get("task_line") or 0),
            reason=str(payload.get("reason") or ""),
        )


@dataclass(frozen=True)
class TaskReceiptStage:
    name: str
    status: str
    updated_at: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskReceiptRecord:
    schema_version: int
    run_id: str
    task_id: str
    updated_at: str
    state: TaskLifecycleState
    agent_outcome: str = ""
    summary: str = ""
    files_touched: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    validation: tuple[dict[str, Any], ...] = ()
    commit_sha: str = ""
    stages: tuple[TaskReceiptStage, ...] = ()
    review_verdict: str = ""
    safe_to_close: bool = False
    closure_checkbox_before: str = ""
    closure_checkbox_after: str = ""
    closure_task_line: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "updated_at": self.updated_at,
            "state": self.state.value,
            "agent_outcome": self.agent_outcome,
            "summary": self.summary,
            "files_touched": list(self.files_touched),
            "notes": list(self.notes),
            "validation": list(self.validation),
            "commit_sha": self.commit_sha,
            "stages": [
                {
                    "name": stage.name,
                    "status": stage.status,
                    "updated_at": stage.updated_at,
                    "details": stage.details,
                }
                for stage in self.stages
            ],
            "review_verdict": self.review_verdict,
            "safe_to_close": self.safe_to_close,
            "closure_checkbox_before": self.closure_checkbox_before,
            "closure_checkbox_after": self.closure_checkbox_after,
            "closure_task_line": self.closure_task_line,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TaskReceiptRecord":
        stages_payload = payload.get("stages", ()) or []
        stages: list[TaskReceiptStage] = []
        for item in stages_payload:
            if not isinstance(item, dict):
                continue
            stages.append(
                TaskReceiptStage(
                    name=str(item.get("name") or ""),
                    status=str(item.get("status") or ""),
                    updated_at=str(item.get("updated_at") or ""),
                    details=dict(item.get("details") or {}),
                )
            )
        return cls(
            schema_version=int(payload.get("schema_version", 1)),
            run_id=str(payload.get("run_id") or ""),
            task_id=str(payload.get("task_id") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            state=TaskLifecycleState(str(payload.get("state") or "PENDING")),
            agent_outcome=str(payload.get("agent_outcome") or ""),
            summary=str(payload.get("summary") or ""),
            files_touched=tuple(str(item) for item in payload.get("files_touched", ()) or ()),
            notes=tuple(str(item) for item in payload.get("notes", ()) or ()),
            validation=tuple(dict(item) for item in payload.get("validation", ()) or () if isinstance(item, dict)),
            commit_sha=str(payload.get("commit_sha") or ""),
            stages=tuple(stages),
            review_verdict=str(payload.get("review_verdict") or ""),
            safe_to_close=bool(payload.get("safe_to_close", False)),
            closure_checkbox_before=str(payload.get("closure_checkbox_before") or ""),
            closure_checkbox_after=str(payload.get("closure_checkbox_after") or ""),
            closure_task_line=int(payload.get("closure_task_line") or 0),
        )


@dataclass(frozen=True)
class TaskReconcileResult:
    snapshot: TaskSnapshot
    task_state: TaskStateRecord | None
    receipt: TaskReceiptRecord | None
    inferred_state: TaskLifecycleState
    stale: bool
    issues: tuple[str, ...]


class TaskStateMachineError(RuntimeError):
    pass


def _normalize_task_id(task_id: str) -> str:
    if not isinstance(task_id, str) or not task_id.strip():
        raise TaskStateMachineError("task_id must be a non-empty string")
    normalized = task_id.strip()
    if not TASK_ID_PATTERN.fullmatch(normalized):
        raise TaskStateMachineError("task_id must match T### or T###A")
    return normalized


def task_state_path(task_id: str, root: Path | str = ROOT) -> Path:
    return Path(root) / ".specify" / "runtime" / "task-state" / f"{_normalize_task_id(task_id)}.json"


def task_receipt_path(task_id: str, root: Path | str = ROOT) -> Path:
    return Path(root) / ".specify" / "runtime" / "task-receipts" / f"{_normalize_task_id(task_id)}.json"


def load_task_state(task_id: str, root: Path | str = ROOT) -> TaskStateRecord | None:
    path = task_state_path(task_id, root)
    if not path.is_file():
        return None
    payload = _load_json_object(path)
    return TaskStateRecord.from_payload(payload)


def save_task_state(record: TaskStateRecord, root: Path | str = ROOT) -> Path:
    path = task_state_path(record.task_id, root)
    _write_atomic_json(path, record.to_payload())
    return path


def load_task_receipt(task_id: str, root: Path | str = ROOT) -> TaskReceiptRecord | None:
    path = task_receipt_path(task_id, root)
    if not path.is_file():
        return None
    payload = _load_json_object(path)
    return TaskReceiptRecord.from_payload(payload)


def save_task_receipt(record: TaskReceiptRecord, root: Path | str = ROOT) -> Path:
    path = task_receipt_path(record.task_id, root)
    _write_atomic_json(path, record.to_payload())
    return path


class TaskStateMachine:
    def __init__(self, root: Path | str = ROOT, *, repository: Repository | None = None) -> None:
        self.root = Path(root)
        self.repository = repository or Repository(self.root)

    def reconcile(
        self,
        *,
        task_id: str,
        run_id: str,
        tasks_path: Path,
        baseline_path: Path | None = None,
    ) -> TaskReconcileResult:
        snapshot = self.repository.status()
        snapshot_task = _load_task_snapshot(task_id, tasks_path)
        state_record = load_task_state(task_id, root=self.root)
        receipt_record = load_task_receipt(task_id, root=self.root)

        inferred_state, issues = self._infer_state(
            task_id=task_id,
            run_id=run_id,
            snapshot=snapshot,
            task_snapshot=snapshot_task,
            state_record=state_record,
            receipt_record=receipt_record,
            baseline_path=baseline_path,
        )

        stale = state_record is not None and state_record.state != inferred_state
        if state_record is not None and not self._compatible_state(state_record.state, inferred_state, receipt_record):
            raise TaskStateMachineError(
                "task state is contradictory: "
                f"stored={state_record.state.value} inferred={inferred_state.value} "
                f"run_id={run_id} task_id={task_id}"
            )

        reconciled_state = self._build_state_record(
            task_id=task_id,
            run_id=run_id,
            state=inferred_state,
            snapshot=snapshot,
            task_snapshot=snapshot_task,
            baseline_path=baseline_path,
            reason="; ".join(issues),
        )
        save_task_state(reconciled_state, root=self.root)
        return TaskReconcileResult(
            snapshot=snapshot_task,
            task_state=reconciled_state,
            receipt=receipt_record,
            inferred_state=inferred_state,
            stale=stale,
            issues=tuple(issues),
        )

    def transition(
        self,
        *,
        task_id: str,
        run_id: str,
        state: TaskLifecycleState,
        tasks_path: Path,
        snapshot: GitStatus | None = None,
        baseline_path: Path | None = None,
        reason: str = "",
        receipt: TaskReceiptRecord | None = None,
        commit_sha: str = "",
    ) -> TaskStateRecord:
        task_snapshot = _load_task_snapshot(task_id, tasks_path)
        current = load_task_state(task_id, root=self.root)
        if current is not None and not self._can_advance(current.state, state):
            raise TaskStateMachineError(
                f"invalid task state transition: {current.state.value} -> {state.value} for {task_id}"
            )
        record = self._build_state_record(
            task_id=task_id,
            run_id=run_id,
            state=state,
            snapshot=snapshot or self.repository.status(),
            task_snapshot=task_snapshot,
            baseline_path=baseline_path,
            reason=reason,
        )
        save_task_state(record, root=self.root)
        if receipt is not None or commit_sha:
            saved_receipt = receipt or TaskReceiptRecord(
                schema_version=1,
                run_id=run_id,
                task_id=task_id,
                updated_at=record.updated_at,
                state=state,
            )
            if commit_sha:
                saved_receipt = _replace_receipt(saved_receipt, commit_sha=commit_sha)
            save_task_receipt(saved_receipt, root=self.root)
        return record

    def record_receipt(self, record: TaskReceiptRecord) -> Path:
        return save_task_receipt(record, root=self.root)

    def _infer_state(
        self,
        *,
        task_id: str,
        run_id: str,
        snapshot: GitStatus,
        task_snapshot: TaskSnapshot,
        state_record: TaskStateRecord | None,
        receipt_record: TaskReceiptRecord | None,
        baseline_path: Path | None,
    ) -> tuple[TaskLifecycleState, list[str]]:
        issues: list[str] = []
        task_checked = task_snapshot.checkbox.upper() == "X"
        commit_sha = receipt_record.commit_sha if receipt_record is not None else ""
        review_passed = (
            receipt_record is not None
            and receipt_record.review_verdict.upper() == "PASS"
            and receipt_record.safe_to_close
        )
        validation_passed = self._receipt_stage_passed(receipt_record, "validated")
        implementation_passed = self._receipt_stage_passed(receipt_record, "implemented") or self._has_task_changes(snapshot, task_snapshot.allowlist)

        if commit_sha and snapshot.head_sha and commit_sha == snapshot.head_sha and task_checked:
            return TaskLifecycleState.COMMITTED, issues
        if task_checked and review_passed and commit_sha:
            return TaskLifecycleState.COMMITTED, issues
        if task_checked and review_passed:
            return TaskLifecycleState.CLOSED, issues
        if review_passed:
            return TaskLifecycleState.REVIEWED, issues
        if validation_passed:
            return TaskLifecycleState.VALIDATED, issues
        if implementation_passed:
            return TaskLifecycleState.IMPLEMENTED, issues
        if self._is_ready(task_snapshot, snapshot, baseline_path):
            return TaskLifecycleState.READY, issues
        return TaskLifecycleState.PENDING, issues

    def _is_ready(self, task_snapshot: TaskSnapshot, snapshot: GitStatus, baseline_path: Path | None) -> bool:
        if task_snapshot.checkbox.upper() == "X":
            return False
        if snapshot.branch in {"master", "main"}:
            return False
        if baseline_path is not None and not baseline_path.is_file():
            return False
        return True

    def _has_task_changes(self, snapshot: GitStatus, allowlist: Sequence[str]) -> bool:
        observed = {
            *snapshot.tracked,
            *snapshot.staged,
            *snapshot.untracked,
            *snapshot.deleted,
            *(old for old, _ in snapshot.renamed),
            *(new for _, new in snapshot.renamed),
        }
        return any(_path_allowed(path, allowlist) for path in observed)

    def _receipt_stage_passed(self, receipt: TaskReceiptRecord | None, stage: str) -> bool:
        if receipt is None:
            return False
        if receipt.state.value == stage.upper():
            return True
        return any(stage.lower() == item.name.lower() and item.status.upper() == "PASS" for item in receipt.stages)

    def _build_state_record(
        self,
        *,
        task_id: str,
        run_id: str,
        state: TaskLifecycleState,
        snapshot: GitStatus,
        task_snapshot: TaskSnapshot,
        baseline_path: Path | None,
        reason: str,
    ) -> TaskStateRecord:
        baseline_payload = _load_json_object(baseline_path) if baseline_path is not None and baseline_path.is_file() else {}
        return TaskStateRecord(
            schema_version=1,
            run_id=run_id,
            task_id=task_id,
            state=state,
            updated_at=_timestamp(),
            branch=snapshot.branch,
            head_sha=snapshot.head_sha,
            baseline_path=str(baseline_path) if baseline_path is not None else "",
            baseline_branch=str(baseline_payload.get("branch") or ""),
            baseline_head_sha=str(baseline_payload.get("head_sha") or ""),
            tasks_path=task_snapshot.tasks_path,
            feature_dir=str(Path(task_snapshot.tasks_path).parent),
            allowlist=task_snapshot.allowlist,
            validation_commands=task_snapshot.validation_commands,
            task_line=task_snapshot.task_line,
            reason=reason,
        )

    def _compatible_state(
        self,
        stored: TaskLifecycleState,
        inferred: TaskLifecycleState,
        receipt_record: TaskReceiptRecord | None,
    ) -> bool:
        if stored == inferred:
            return True
        if stored in TERMINAL_STATES or inferred in TERMINAL_STATES:
            return stored == inferred
        try:
            return ORDERED_STATES.index(stored) <= ORDERED_STATES.index(inferred)
        except ValueError:
            return False

    def _can_advance(self, current: TaskLifecycleState, target: TaskLifecycleState) -> bool:
        if current == target:
            return True
        if current in TERMINAL_STATES:
            return False
        if target in TERMINAL_STATES:
            return True
        try:
            return ORDERED_STATES.index(current) <= ORDERED_STATES.index(target)
        except ValueError:
            return False


def _path_allowed(path: str, allowlist: Sequence[str]) -> bool:
    normalized_path = path.replace("\\", "/").strip()
    if not normalized_path:
        return False
    for item in allowlist:
        normalized_item = str(item).replace("\\", "/").strip().rstrip("/")
        if not normalized_item:
            continue
        if normalized_path == normalized_item or normalized_path.startswith(f"{normalized_item}/"):
            return True
    return False


def _load_task_snapshot(task_id: str, tasks_path: Path) -> TaskSnapshot:
    for found_task_id, start_line, lines in task_consistency._iter_task_blocks(tasks_path):
        if found_task_id != task_id:
            continue
        header = lines[0][1]
        checkbox = header[2:3] if header.startswith("- [") else " "
        if header.startswith("- [X]"):
            checkbox = "X"
        elif header.startswith("- [x]"):
            checkbox = "x"
        elif header.startswith("- [ ]"):
            checkbox = " "
        title = header[header.index(task_id) + len(task_id) :].strip()
        epic = task_consistency._field_value(lines, "Epic:")
        milestone = task_consistency._field_value(lines, "Milestone:")
        implementation = task_consistency._field_value(lines, "Implementation files:")
        test_files = task_consistency._field_value(lines, "Test files:")
        validation_commands = task_consistency._field_value(lines, "Validation commands:")
        if epic is None or milestone is None or implementation is None or test_files is None or validation_commands is None:
            raise TaskStateMachineError(f"{tasks_path.name}:{start_line}: task {task_id} is missing required fields")
        return TaskSnapshot(
            task_id=task_id,
            task_line=start_line,
            checkbox=checkbox,
            title=title,
            epic_id=epic[1].strip(),
            milestone_id=milestone[1].strip(),
            implementation_files=tuple(_split_list(implementation[1])),
            test_files=tuple(_split_list(test_files[1])),
            validation_commands=tuple(_split_validation_commands(validation_commands[1])),
            tasks_path=tasks_path.as_posix(),
        )
    raise TaskStateMachineError(f"task does not exist in tasks.md: {task_id}")


def _split_list(value: str) -> list[str]:
    normalized = value.strip().strip("`")
    if not normalized or normalized.lower() in {"none", "n/a", "na", "[]"}:
        return []
    return [item.strip().strip("`") for item in normalized.split(",") if item.strip()]


def _split_validation_commands(value: str) -> list[str]:
    normalized = value.strip()
    if not normalized or normalized.lower() in {"none", "n/a", "na", "[]"}:
        return []
    commands: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in normalized:
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
            current.append(char)
            continue
        if char == ";":
            command = "".join(current).strip().strip("`").strip()
            if command:
                commands.append(command)
            current = []
            continue
        current.append(char)
    if quote is not None:
        raise TaskStateMachineError(f"validation commands contain unterminated quote: {value!r}")
    command = "".join(current).strip().strip("`").strip()
    if command:
        commands.append(command)
    return commands


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TaskStateMachineError(f"{path.name}: JSON payload must be an object")
    return payload


def _replace_receipt(receipt: TaskReceiptRecord, *, commit_sha: str) -> TaskReceiptRecord:
    return TaskReceiptRecord(
        schema_version=receipt.schema_version,
        run_id=receipt.run_id,
        task_id=receipt.task_id,
        updated_at=receipt.updated_at,
        state=receipt.state,
        agent_outcome=receipt.agent_outcome,
        summary=receipt.summary,
        files_touched=receipt.files_touched,
        notes=receipt.notes,
        commit_sha=commit_sha,
        stages=receipt.stages,
        review_verdict=receipt.review_verdict,
        safe_to_close=receipt.safe_to_close,
        closure_checkbox_before=receipt.closure_checkbox_before,
        closure_checkbox_after=receipt.closure_checkbox_after,
        closure_task_line=receipt.closure_task_line,
    )


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


def _timestamp() -> str:
    from time import gmtime, strftime

    return strftime("%Y-%m-%dT%H:%M:%SZ", gmtime())


__all__ = [
    "ORDERED_STATES",
    "ROOT",
    "TASK_RECEIPT_DIR",
    "TASK_STATE_DIR",
    "TaskLifecycleState",
    "TaskReceiptRecord",
    "TaskReceiptStage",
    "TaskReconcileResult",
    "TaskSnapshot",
    "TaskStateMachine",
    "TaskStateMachineError",
    "TaskStateRecord",
    "load_task_receipt",
    "load_task_state",
    "save_task_receipt",
    "save_task_state",
    "task_receipt_path",
    "task_state_path",
]

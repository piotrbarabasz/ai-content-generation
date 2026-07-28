"""Deterministic pipeline for a single local autopilot task."""

from __future__ import annotations

import json
import shlex
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from app.tooling import task_consistency

from . import process_runner, repository as repository_module
from .codex_adapter import CodexAdapter
from .config import AutopilotConfig, DEFAULT_AUTOPILOT_CONFIG_PATH, load_autopilot_config
from .models import AutopilotRun, CommandResult, RunStatus, TaskResult
from .task_state_machine import (
    TASK_RECEIPT_DIR,
    TASK_STATE_DIR,
    TaskLifecycleState,
    TaskReceiptRecord,
    TaskReceiptStage,
    TaskStateMachine,
    TaskStateMachineError,
    load_task_receipt,
    save_task_receipt,
    save_task_state,
    task_receipt_path,
    task_state_path,
)
from .state_store import save_run_state
from .workstreams import get_epic

ROOT = Path(__file__).resolve().parents[4]
TASK_ID_PATTERN = re.compile(r"^T\d{3}[A-Z]?$")
NO_DEPENDENCY_VALUES = {"none", "n/a", "na", "[]"}
FORBIDDEN_SHELL_OPERATORS = ("&&", "||", "|", ">", "<", "`", "\n", "\r")


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    task_line: int
    task_title: str
    checkbox: str
    feature_dir: Path
    epic_id: str
    milestone_id: str
    implementation_files: tuple[str, ...]
    test_files: tuple[str, ...]
    allowlist: tuple[str, ...]
    validation_commands: tuple[str, ...]
    tasks_path: Path
    tasks_text: str


@dataclass(frozen=True)
class TaskPipelineResult:
    status: RunStatus
    run: AutopilotRun
    task_result: TaskResult
    attempts: int
    baseline_path: str
    allowlist: tuple[str, ...]
    validation_commands: tuple[str, ...]
    command_results: tuple[CommandResult, ...]
    reason: str | None = None


class TaskPipelineError(RuntimeError):
    pass


class TaskPipeline:
    def __init__(
        self,
        root: Path | str = ROOT,
        *,
        config: AutopilotConfig | None = None,
        repository: repository_module.Repository | None = None,
        codex_adapter: CodexAdapter | None = None,
        process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
        config_path: Path | str = DEFAULT_AUTOPILOT_CONFIG_PATH,
    ) -> None:
        self.root = Path(root)
        self._run = process_runner_fn
        self.config = config or load_autopilot_config(config_path)
        self.repository = repository or repository_module.Repository(self.root, process_runner_fn=process_runner_fn)
        self.codex = codex_adapter or CodexAdapter(self.root, process_runner_fn=process_runner_fn)
        self.state_machine = TaskStateMachine(self.root, repository=self.repository)

    def run_task(
        self,
        run: AutopilotRun,
        *,
        task_id: str,
        cancel_event: Any | None = None,
    ) -> TaskPipelineResult:
        command_results: list[CommandResult] = []
        try:
            self._require_not_cancelled(cancel_event)
            epic_id, branch_name = self._active_epic_and_branch(run)
            self._require_active_epic(epic_id, branch_name)
            python_executable = self._resolve_agent_python()
            epic_manifest = get_epic(epic_id, self.root / ".specify" / "workstreams")
            if not isinstance(epic_manifest, dict):
                raise TaskPipelineError(f"epic {epic_id} manifest is invalid")
            feature_dir = _resolve_path(self.root, _require_text(epic_manifest, "feature"))
            if not feature_dir.is_dir():
                raise TaskPipelineError(f"epic {epic_id} does not declare a feature directory")
            tasks_path = feature_dir / "tasks.md"
            task_context = self._load_task_context(task_id, tasks_path=tasks_path, feature_dir=feature_dir)
            if task_context.checkbox == "X":
                raise TaskPipelineError(f"{task_context.tasks_path.name}:{task_context.task_line}: task {task_id} must be unchecked before running")

            checklist_blocker = self._check_required_checklists(task_context.feature_dir)
            if checklist_blocker is not None:
                return self._blocked_result(
                    run,
                    task_id,
                    task_context=task_context,
                    command_results=command_results,
                    reason=str(checklist_blocker),
                    attempts=0,
                )

            baseline_file = self._default_baseline_path(task_id)
            reconcile = self.state_machine.reconcile(
                task_id=task_id,
                run_id=run.run_id,
                tasks_path=task_context.tasks_path,
                baseline_path=baseline_file if baseline_file.is_file() else None,
            )
            current_state = reconcile.inferred_state
            state_record = reconcile.task_state
            receipt = reconcile.receipt

            if current_state in {TaskLifecycleState.PENDING, TaskLifecycleState.READY}:
                self._require_clean_tree()
                preflight_payload, preflight_command = self._run_preflight(task_id, python_executable, cancel_event)
                command_results.append(preflight_command)
                if preflight_payload.get("status") != "PASS":
                    raise TaskPipelineError(f"preflight status is {preflight_payload.get('status')!r}, expected 'PASS'")
                if preflight_payload.get("task") != task_id:
                    raise TaskPipelineError(f"preflight selected {preflight_payload.get('task')!r}, expected {task_id!r}")
                if preflight_payload.get("epic") != epic_id:
                    raise TaskPipelineError(f"preflight epic is {preflight_payload.get('epic')!r}, expected {epic_id!r}")
                if preflight_payload.get("branch") != branch_name:
                    raise TaskPipelineError(f"preflight branch is {preflight_payload.get('branch')!r}, expected {branch_name!r}")
                baseline_file = _resolve_path(self.root, _require_text(preflight_payload, "baseline_path"))
                if not _path_exists(baseline_file):
                    raise TaskPipelineError(f"baseline does not exist: {baseline_file}")
                state_record = self.state_machine.transition(
                    task_id=task_id,
                    run_id=run.run_id,
                    state=TaskLifecycleState.READY,
                    tasks_path=task_context.tasks_path,
                    baseline_path=baseline_file,
                    reason="preflight completed",
                )
                current_state = TaskLifecycleState.READY
            elif state_record is not None and state_record.baseline_path:
                baseline_file = _resolve_path(self.root, state_record.baseline_path)

            if current_state in {TaskLifecycleState.PENDING, TaskLifecycleState.READY}:
                codex_result = self.codex.run_task(
                    task_id=task_id,
                    task_text=task_context.task_title,
                    agent_python=python_executable,
                    speckit_selector=task_id,
                    timeout_seconds=self.config.codex_timeout_seconds,
                    cancel_event=cancel_event,
                )
                attempts = 1
                command_results.extend(self._command_results_from_codex(codex_result))
                if codex_result.cancelled:
                    return self._cancelled_result(run, task_id, command_results, reason="cancelled", attempts=attempts)
                if codex_result.status == "BLOCKED":
                    receipt = self._write_receipt(
                        task_context=task_context,
                        run_id=run.run_id,
                        state=TaskLifecycleState.BLOCKED,
                        summary=codex_result.parse_error or self._blocked_reason_from_codex(codex_result) or "codex returned BLOCKED",
                        files_touched=(),
                        notes=(codex_result.parse_error or self._blocked_reason_from_codex(codex_result) or "codex returned BLOCKED",),
                        agent_outcome="blocked",
                    )
                    self.state_machine.transition(
                        task_id=task_id,
                        run_id=run.run_id,
                        state=TaskLifecycleState.BLOCKED,
                        tasks_path=task_context.tasks_path,
                        baseline_path=baseline_file,
                        reason=receipt.summary,
                        receipt=receipt,
                    )
                    return self._blocked_result(
                        run,
                        task_id,
                        task_context=task_context,
                        command_results=command_results,
                        reason=receipt.summary,
                        attempts=attempts,
                    )
                if codex_result.status != "PASS" or codex_result.result_json is None:
                    last_reason = codex_result.parse_error or f"codex exited with {codex_result.status}"
                    receipt = self._write_receipt(
                        task_context=task_context,
                        run_id=run.run_id,
                        state=TaskLifecycleState.FAILED,
                        summary=last_reason,
                        files_touched=(),
                        notes=(last_reason,),
                        agent_outcome="failed",
                    )
                    self.state_machine.transition(
                        task_id=task_id,
                        run_id=run.run_id,
                        state=TaskLifecycleState.FAILED,
                        tasks_path=task_context.tasks_path,
                        baseline_path=baseline_file,
                        reason=receipt.summary,
                        receipt=receipt,
                    )
                    return self._failed_result(
                        run,
                        task_id,
                        task_context=task_context,
                        command_results=command_results,
                        reason=last_reason,
                        attempts=attempts,
                    )

                touched_source = codex_result.result_json.get("files_touched")
                if not isinstance(touched_source, list):
                    touched_source = codex_result.result_json.get("files_changed")
                touched_files = tuple(str(item).replace("\\", "/") for item in (touched_source or ()))
                outcome = str(codex_result.result_json.get("agent_outcome") or codex_result.result_json.get("final_status") or "").strip().lower()
                if outcome == "completed":
                    outcome = "finished"
                if not touched_files:
                    receipt = self._write_receipt(
                        task_context=task_context,
                        run_id=run.run_id,
                        state=TaskLifecycleState.FAILED,
                        summary="codex did not touch any files",
                        files_touched=touched_files,
                        notes=("codex did not touch any files",),
                        agent_outcome=outcome or "failed",
                    )
                    self.state_machine.transition(
                        task_id=task_id,
                        run_id=run.run_id,
                        state=TaskLifecycleState.FAILED,
                        tasks_path=task_context.tasks_path,
                        baseline_path=baseline_file,
                        reason=receipt.summary,
                        receipt=receipt,
                    )
                    return self._failed_result(
                        run,
                        task_id,
                        task_context=task_context,
                        command_results=command_results,
                        reason=receipt.summary,
                        attempts=attempts,
                    )

                scope_check = self._check_scope_drift(
                    _load_json_mapping(baseline_file),
                    [*task_context.allowlist, task_context.tasks_path.relative_to(self.root).as_posix()],
                )
                if scope_check is not None:
                    receipt = self._write_receipt(
                        task_context=task_context,
                        run_id=run.run_id,
                        state=TaskLifecycleState.FAILED,
                        summary=str(scope_check),
                        files_touched=touched_files,
                        notes=(str(scope_check),),
                        agent_outcome=outcome or "failed",
                    )
                    self.state_machine.transition(
                        task_id=task_id,
                        run_id=run.run_id,
                        state=TaskLifecycleState.FAILED,
                        tasks_path=task_context.tasks_path,
                        baseline_path=baseline_file,
                        reason=receipt.summary,
                        receipt=receipt,
                    )
                    return self._failed_result(
                        run,
                        task_id,
                        task_context=task_context,
                        command_results=command_results,
                        reason=receipt.summary,
                        attempts=attempts,
                    )

                receipt = self._write_receipt(
                    task_context=task_context,
                    run_id=run.run_id,
                    state=TaskLifecycleState.IMPLEMENTED,
                    summary=(
                        str(codex_result.result_json.get("summary") or codex_result.result_json.get("reason") or "").strip()
                        if isinstance(codex_result.result_json.get("summary") or codex_result.result_json.get("reason") or "", str)
                        else ""
                    ),
                    files_touched=touched_files,
                    notes=tuple(
                        str(item)
                        for item in (
                            codex_result.result_json.get("notes")
                            or ([codex_result.result_json.get("reason")] if codex_result.result_json.get("reason") else [])
                            or ([codex_result.result_json.get("blocked_reason")] if codex_result.result_json.get("blocked_reason") else [])
                        )
                        if item is not None
                    ),
                    agent_outcome=outcome or "finished",
                )
                self.state_machine.transition(
                    task_id=task_id,
                    run_id=run.run_id,
                    state=TaskLifecycleState.IMPLEMENTED,
                    tasks_path=task_context.tasks_path,
                    baseline_path=baseline_file,
                    reason=receipt.summary,
                    receipt=receipt,
                )
                current_state = TaskLifecycleState.IMPLEMENTED
            else:
                attempts = 0

            if current_state in {TaskLifecycleState.IMPLEMENTED, TaskLifecycleState.VALIDATED, TaskLifecycleState.REVIEWED, TaskLifecycleState.CLOSED, TaskLifecycleState.COMMITTED}:
                validation_results = ()
                diff_result = None
                finalizer_result = None
                if current_state == TaskLifecycleState.IMPLEMENTED:
                    validation_results = self._run_validation_commands(task_context.validation_commands, python_executable, cancel_event)
                    command_results.extend(validation_results)
                    if any(result.status != "PASS" for result in validation_results):
                        receipt = self._write_receipt(
                            task_context=task_context,
                            run_id=run.run_id,
                            state=TaskLifecycleState.FAILED,
                            summary="validation failed",
                            files_touched=tuple(),
                            notes=("validation failed",),
                            agent_outcome="failed",
                            validation=[self._command_result_to_payload(result) for result in validation_results],
                        )
                        self.state_machine.transition(
                            task_id=task_id,
                            run_id=run.run_id,
                            state=TaskLifecycleState.FAILED,
                            tasks_path=task_context.tasks_path,
                            baseline_path=baseline_file,
                            reason=receipt.summary,
                            receipt=receipt,
                        )
                        return self._failed_result(
                            run,
                            task_id,
                            task_context=task_context,
                            command_results=command_results,
                            reason="validation failed",
                            attempts=attempts,
                        )
                    diff_result = self._diff_check()
                    command_results.append(diff_result)
                    if diff_result.status != "PASS":
                        repaired = self._repair_whitespace(task_context.allowlist)
                        if repaired:
                            command_results.extend(repaired)
                            diff_result = self._diff_check()
                            command_results.append(diff_result)
                        if diff_result.status != "PASS":
                            receipt = self._write_receipt(
                                task_context=task_context,
                                run_id=run.run_id,
                                state=TaskLifecycleState.FAILED,
                                summary="git diff --check failed",
                                files_touched=tuple(),
                                notes=("git diff --check failed",),
                                agent_outcome="failed",
                                validation=[self._command_result_to_payload(result) for result in validation_results],
                            )
                            self.state_machine.transition(
                                task_id=task_id,
                                run_id=run.run_id,
                                state=TaskLifecycleState.FAILED,
                                tasks_path=task_context.tasks_path,
                                baseline_path=baseline_file,
                                reason=receipt.summary,
                                receipt=receipt,
                            )
                            return self._failed_result(
                                run,
                                task_id,
                                task_context=task_context,
                                command_results=command_results,
                                reason="git diff --check failed",
                                attempts=attempts,
                            )
                    finalizer_result = self._run_task_finalizer(task_id, python_executable)
                    command_results.append(finalizer_result)
                    if finalizer_result.status != "PASS":
                        receipt = self._write_receipt(
                            task_context=task_context,
                            run_id=run.run_id,
                            state=TaskLifecycleState.FAILED,
                            summary="task finalizer failed",
                            files_touched=tuple(),
                            notes=("task finalizer failed",),
                            agent_outcome="failed",
                            validation=[self._command_result_to_payload(result) for result in validation_results],
                        )
                        self.state_machine.transition(
                            task_id=task_id,
                            run_id=run.run_id,
                            state=TaskLifecycleState.FAILED,
                            tasks_path=task_context.tasks_path,
                            baseline_path=baseline_file,
                            reason=receipt.summary,
                            receipt=receipt,
                        )
                        return self._failed_result(
                            run,
                            task_id,
                            task_context=task_context,
                            command_results=command_results,
                            reason="task finalizer failed",
                            attempts=attempts,
                        )
                    receipt = self._write_receipt(
                        task_context=task_context,
                        run_id=run.run_id,
                        state=TaskLifecycleState.VALIDATED,
                        summary="validation and finalizer passed",
                        files_touched=tuple(),
                        notes=("git diff --check passed", "agent_task_finalize PASS"),
                        agent_outcome="finished",
                        validation=[self._command_result_to_payload(result) for result in validation_results],
                        review_verdict="PASS",
                        safe_to_close=True,
                    )
                    self.state_machine.transition(
                        task_id=task_id,
                        run_id=run.run_id,
                        state=TaskLifecycleState.VALIDATED,
                        tasks_path=task_context.tasks_path,
                        baseline_path=baseline_file,
                        reason=receipt.summary,
                        receipt=receipt,
                    )
                    current_state = TaskLifecycleState.VALIDATED

                if current_state in {TaskLifecycleState.VALIDATED, TaskLifecycleState.IMPLEMENTED}:
                    receipt = self._write_receipt(
                        task_context=task_context,
                        run_id=run.run_id,
                        state=TaskLifecycleState.REVIEWED,
                        summary="review passed",
                        files_touched=tuple(receipt.files_touched if receipt is not None else ()),
                        notes=("review verdict PASS", "safe_to_close yes"),
                        agent_outcome="finished",
                        review_verdict="PASS",
                        safe_to_close=True,
                        stages=(TaskReceiptStage(name="review", status="PASS", updated_at=_timestamp(), details={"scope_drift": []}),),
                    )
                    self.state_machine.transition(
                        task_id=task_id,
                        run_id=run.run_id,
                        state=TaskLifecycleState.REVIEWED,
                        tasks_path=task_context.tasks_path,
                        baseline_path=baseline_file,
                        reason=receipt.summary,
                        receipt=receipt,
                    )
                    current_state = TaskLifecycleState.REVIEWED

            if current_state in {TaskLifecycleState.REVIEWED, TaskLifecycleState.CLOSED, TaskLifecycleState.COMMITTED}:
                current_text = task_context.tasks_path.read_text(encoding="utf-8")
                if current_state == TaskLifecycleState.REVIEWED:
                    updated_text, checkbox_before, checkbox_after = self._close_task_checkbox(task_context, current_text)
                    if updated_text != current_text:
                        task_context.tasks_path.write_text(updated_text, encoding="utf-8")
                    receipt = self._write_receipt(
                        task_context=task_context,
                        run_id=run.run_id,
                        state=TaskLifecycleState.CLOSED,
                        summary="task checkbox closed",
                        files_touched=tuple(receipt.files_touched if receipt is not None else ()),
                        notes=("tasks.md checkbox closed",),
                        agent_outcome="finished",
                        closure_checkbox_before=checkbox_before,
                        closure_checkbox_after=checkbox_after,
                        closure_task_line=task_context.task_line,
                    )
                    self.state_machine.transition(
                        task_id=task_id,
                        run_id=run.run_id,
                        state=TaskLifecycleState.CLOSED,
                        tasks_path=task_context.tasks_path,
                        baseline_path=baseline_file,
                        reason=receipt.summary,
                        receipt=receipt,
                    )
                    current_state = TaskLifecycleState.CLOSED

                if current_state == TaskLifecycleState.CLOSED:
                    commit_paths = [
                        *task_context.allowlist,
                        task_context.tasks_path.relative_to(self.root).as_posix(),
                    ]
                    normalization_results = self._repair_whitespace(commit_paths)
                    command_results.extend(normalization_results)
                    self.repository.stage_allowlist(commit_paths)
                    cached_check = self.repository.diff_check(cached=True)
                    command_results.append(self._command_result_from_process(cached_check))
                    if cached_check.status != "PASS":
                        return self._failed_result(
                            run,
                            task_id,
                            task_context=task_context,
                            command_results=command_results,
                            reason="git diff --cached --check failed",
                            attempts=attempts,
                        )
                    pre_commit = self._run_hook("pre-commit", cancel_event=cancel_event)
                    if pre_commit.status != "PASS":
                        return self._failed_result(
                            run,
                            task_id,
                            task_context=task_context,
                            command_results=command_results,
                            reason="pre-commit hook failed",
                            attempts=attempts,
                        )
                    commit_message = f"feat({task_id}): {task_context.task_title}"
                    commit_result = self.repository.commit(commit_message)
                    command_results.append(self._command_result_from_process(commit_result))
                    if commit_result.status != "PASS":
                        return self._failed_result(
                            run,
                            task_id,
                            task_context=task_context,
                            command_results=command_results,
                            reason="git commit failed",
                            attempts=attempts,
                        )
                    commit_sha = self.repository.head_sha()
                    post_commit = self._run_hook("post-commit", cancel_event=cancel_event)
                    if post_commit.status != "PASS":
                        return self._failed_result(
                            run,
                            task_id,
                            task_context=task_context,
                            command_results=command_results,
                            reason="post-commit hook failed",
                            attempts=attempts,
                        )
                    receipt = load_task_receipt(task_id, root=self.root)
                    if receipt is None or receipt.commit_sha != commit_sha or receipt.state != TaskLifecycleState.COMMITTED:
                        receipt = self._write_receipt(
                            task_context=task_context,
                            run_id=run.run_id,
                            state=TaskLifecycleState.COMMITTED,
                            summary="task committed",
                            files_touched=tuple(receipt.files_touched if receipt is not None else ()),
                            notes=("commit recorded",),
                            agent_outcome="finished",
                            commit_sha=commit_sha,
                        )
                        self.state_machine.transition(
                            task_id=task_id,
                            run_id=run.run_id,
                            state=TaskLifecycleState.COMMITTED,
                            tasks_path=task_context.tasks_path,
                            baseline_path=baseline_file,
                            reason=receipt.summary,
                            receipt=receipt,
                            commit_sha=commit_sha,
                        )
                    self.repository.require_clean_tree()
                    task_result = TaskResult(
                        task_id=task_id,
                        status=RunStatus.COMPLETED,
                        command_results=tuple(command_results),
                        commit_sha=commit_sha,
                        title=task_context.task_title,
                    )
                    updated_run = self._update_run(
                        run,
                        status=RunStatus.COMPLETED,
                        epic_id=epic_id,
                        branch_name=branch_name,
                        current_task_id=task_id,
                        task_result=task_result,
                        last_error=None,
                    )
                    save_run_state(updated_run, root=self.root)
                    return TaskPipelineResult(
                        status=RunStatus.COMPLETED,
                        run=updated_run,
                        task_result=task_result,
                        attempts=attempts,
                        baseline_path=str(baseline_file),
                        allowlist=task_context.allowlist,
                        validation_commands=task_context.validation_commands,
                        command_results=tuple(command_results),
                        reason=None,
                    )

            if current_state == TaskLifecycleState.COMMITTED:
                commit_sha = receipt.commit_sha if receipt is not None and receipt.commit_sha else self.repository.head_sha()
                task_result = TaskResult(
                    task_id=task_id,
                    status=RunStatus.COMPLETED,
                    command_results=tuple(command_results),
                    commit_sha=commit_sha,
                    title=task_context.task_title,
                )
                updated_run = self._update_run(
                    run,
                    status=RunStatus.COMPLETED,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    current_task_id=task_id,
                    task_result=task_result,
                    last_error=None,
                )
                save_run_state(updated_run, root=self.root)
                return TaskPipelineResult(
                    status=RunStatus.COMPLETED,
                    run=updated_run,
                    task_result=task_result,
                    attempts=attempts,
                    baseline_path=str(baseline_file),
                    allowlist=task_context.allowlist,
                    validation_commands=task_context.validation_commands,
                    command_results=tuple(command_results),
                    reason=None,
                )

            raise TaskPipelineError(f"unhandled task state: {current_state.value}")
        except (KeyboardInterrupt, TaskPipelineError, RuntimeError, ValueError, FileNotFoundError, OSError) as exc:
            if isinstance(exc, KeyboardInterrupt):
                return self._cancelled_result(run, task_id, command_results, reason="cancelled", attempts=0)
            return self._failed_result(run, task_id, command_results, reason=str(exc), attempts=0)

    def _require_not_cancelled(self, cancel_event: Any | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise KeyboardInterrupt()

    def _can_retry(self, attempts: int) -> bool:
        return attempts <= self.config.max_repair_cycles

    def _require_clean_tree(self) -> None:
        self.repository.require_clean_tree()

    def _active_epic_and_branch(self, run: AutopilotRun) -> tuple[str, str]:
        epic_id = run.epic_id or (run.request.scope_id if run.request.scope_type.value == "epic" else None)
        branch_name = run.branch_name
        if not epic_id:
            raise TaskPipelineError("run does not declare an active epic")
        if not branch_name:
            raise TaskPipelineError("run does not declare a branch")
        return epic_id, branch_name

    def _require_active_epic(self, epic_id: str, branch_name: str) -> None:
        epic = get_epic(epic_id, self.root / ".specify" / "workstreams")
        if str(epic.get("status") or "") != "active":
            raise TaskPipelineError(f"epic {epic_id} is not active")
        manifest_branch = str(epic.get("branch") or "")
        if manifest_branch and manifest_branch != branch_name:
            raise TaskPipelineError(f"active branch {branch_name!r} does not match epic branch {manifest_branch!r}")
        current_branch = self.repository.status().branch
        if current_branch != branch_name:
            raise TaskPipelineError(f"current branch {current_branch!r} does not match run branch {branch_name!r}")

    def _resolve_agent_python(self) -> str:
        result = self._run(
            ["git", "config", "--local", "--get", "agent.python"],
            cwd=self.root,
            timeout_seconds=20,
            heartbeat_seconds=0,
        )
        if result.status != "PASS" or not result.stdout_lines:
            raise TaskPipelineError("agent.python is not configured")
        python_executable = result.stdout_lines[0].strip()
        if not python_executable:
            raise TaskPipelineError("agent.python is empty")
        return python_executable

    def _run_preflight(self, task_id: str, python_executable: str, cancel_event: Any | None) -> tuple[dict[str, Any], CommandResult]:
        result = self._run(
            [
                python_executable,
                "-m",
                "backend.app.tooling.agent_task_preflight",
                "--selector",
                task_id,
                "--json",
            ],
            cwd=self.root,
            timeout_seconds=self.config.command_timeout_seconds,
            cancel_event=cancel_event,
            heartbeat_seconds=0,
        )
        command_result = self._command_result_from_process(result)
        if result.status != "PASS":
            raise TaskPipelineError(f"preflight failed: {result.status}")
        payload = _parse_preflight_json_document(_combine_lines(result.stdout_lines))
        return payload, command_result

    def _load_task_context(self, task_id: str, *, tasks_path: Path, feature_dir: Path) -> TaskContext:
        for found_task_id, start_line, lines in task_consistency._iter_task_blocks(tasks_path):
            if found_task_id != task_id:
                continue
            header = lines[0][1]
            match = task_consistency.TASK_HEADER_PATTERN.match(header)
            if not match:
                raise TaskPipelineError(f"{tasks_path.name}:{start_line}: malformed task header")
            checkbox = match.group("checkbox")
            title = header[header.index(task_id) + len(task_id) :].strip()
            epic = task_consistency._field_value(lines, "Epic:")
            milestone = task_consistency._field_value(lines, "Milestone:")
            implementation = task_consistency._field_value(lines, "Implementation files:")
            test_files = task_consistency._field_value(lines, "Test files:")
            validation_commands = task_consistency._field_value(lines, "Validation commands:")
            if epic is None or milestone is None or implementation is None or test_files is None or validation_commands is None:
                raise TaskPipelineError(f"{tasks_path.name}:{start_line}: task {task_id} is missing required fields")
            implementation_files = tuple(_split_comma_list(implementation[1]))
            test_file_list = tuple(_split_comma_list(test_files[1]))
            commands = tuple(_split_validation_commands(validation_commands[1]))
            return TaskContext(
                task_id=task_id,
                task_line=start_line,
                task_title=title,
                checkbox=checkbox,
                feature_dir=feature_dir,
                epic_id=epic[1].strip(),
                milestone_id=milestone[1].strip(),
                implementation_files=implementation_files,
                test_files=test_file_list,
                allowlist=tuple([*implementation_files, *test_file_list]),
                validation_commands=commands,
                tasks_path=tasks_path,
                tasks_text=tasks_path.read_text(encoding="utf-8"),
            )
        raise TaskPipelineError(f"task does not exist in tasks.md: {task_id}")

    def _check_required_checklists(self, feature_dir: Path) -> TaskPipelineError | None:
        checklist_dir = feature_dir / "checklists"
        if not checklist_dir.is_dir():
            return None
        violations: list[str] = []
        for checklist_path in sorted(checklist_dir.glob("*.md")):
            try:
                text = checklist_path.read_text(encoding="utf-8")
            except OSError as exc:
                violations.append(f"{checklist_path.relative_to(self.root)}:1: failed to read checklist: {exc}")
                continue
            if "optional: true" in text.lower():
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if re.search(r"^\s*-\s*\[\s\]\s*", line):
                    violations.append(f"{checklist_path.relative_to(self.root)}:{line_number}: {line}")
        if violations:
            return TaskPipelineError("required checklist items are incomplete:\n" + "\n".join(violations))
        return None

    def _check_scope_drift(self, baseline: dict[str, Any], allowlist: Sequence[str]) -> TaskPipelineError | None:
        current = self.repository.status()
        current_paths = sorted(set(current.tracked + current.staged + current.untracked + current.deleted + tuple(old for old, _ in current.renamed) + tuple(new for _, new in current.renamed)))
        unexpected = [path for path in current_paths if not _path_allowed(path, allowlist)]
        if unexpected:
            return TaskPipelineError(f"unexpected paths outside allowlist: {', '.join(unexpected)}")
        baseline_head = str(baseline.get("head_sha") or "")
        if baseline_head and current.head_sha and baseline_head != current.head_sha:
            return TaskPipelineError(f"head SHA changed from baseline {baseline_head!r} to {current.head_sha!r}")
        return None

    def _run_validation_commands(
        self,
        commands: Sequence[str],
        python_executable: str,
        cancel_event: Any | None,
    ) -> tuple[CommandResult, ...]:
        results: list[CommandResult] = []
        for command in commands:
            argv = _safe_validation_command_argv(command)
            if _is_diff_check_command(argv):
                continue
            argv = _normalize_python_launcher(argv, python_executable)
            result = self._run(
                argv,
                cwd=self.root,
                timeout_seconds=self.config.command_timeout_seconds,
                cancel_event=cancel_event,
                heartbeat_seconds=0,
            )
            command_result = self._command_result_from_process(result)
            results.append(command_result)
            if command_result.status != "PASS":
                break
        return tuple(results)

    def _default_baseline_path(self, task_id: str) -> Path:
        return self.root / ".specify" / "runtime" / "task-runs" / task_id / "baseline.json"

    def _run_task_finalizer(self, task_id: str, python_executable: str) -> CommandResult:
        result = self._run(
            [
                python_executable,
                "-m",
                "backend.app.tooling.agent_task_finalize",
                "--task",
                task_id,
                "--json",
            ],
            cwd=self.root,
            timeout_seconds=self.config.command_timeout_seconds,
            heartbeat_seconds=0,
        )
        return self._command_result_from_process(result)

    def _run_hook(self, mode: str, *, cancel_event: Any | None = None) -> CommandResult:
        python_executable = self._resolve_agent_python()
        result = self._run(
            [
                python_executable,
                "-m",
                "backend.app.tooling.git_hook_runner",
                mode,
                "--json",
                "--no-heartbeat",
            ],
            cwd=self.root,
            timeout_seconds=self.config.command_timeout_seconds,
            cancel_event=cancel_event,
            heartbeat_seconds=0,
        )
        return self._command_result_from_process(result)

    def _diff_check(self) -> CommandResult:
        result = self.repository.diff_check(cached=False)
        return self._command_result_from_process(result)

    def _repair_whitespace(self, allowlist: Sequence[str]) -> tuple[CommandResult, ...]:
        changed = self.repository.normalize_allowlist_eof([path for path in allowlist])
        if not changed:
            return ()
        return (
            self._command_result_from_process(
                process_runner.ProcessResult(
                    command=("normalize_allowlist_eof",),
                    status="PASS",
                    exit_code=0,
                    duration_ms=0,
                    timed_out=False,
                    cancelled=False,
                    stdout_lines=tuple(changed),
                    stderr_lines=(),
                    output_truncated=False,
                    process_tree_killed=False,
                    pid=None,
                )
            ),
        )

    def _verify_task_completion_change(self, task_context: TaskContext, *, before_text: str, after_text: str) -> str | None:
        before_lines = before_text.splitlines()
        after_lines = after_text.splitlines()
        if before_lines == after_lines:
            return "tasks.md was not updated by the successful speckit-loop"
        if len(before_lines) != len(after_lines):
            return "tasks.md changed more than one line"
        diff_indexes = [index for index, (before_line, after_line) in enumerate(zip(before_lines, after_lines), start=1) if before_line != after_line]
        if len(diff_indexes) != 1:
            return "tasks.md changed more than one task"
        line_number = diff_indexes[0]
        before_line = before_lines[line_number - 1]
        after_line = after_lines[line_number - 1]
        expected_before = f"- [ ] {task_context.task_id}"
        expected_after = f"- [X] {task_context.task_id}"
        if not before_line.startswith(expected_before):
            return f"{task_context.tasks_path.name}:{line_number}: only the selected task may be closed"
        if before_line.replace("- [ ]", "- [X]", 1) != after_line:
            return f"{task_context.tasks_path.name}:{line_number}: task text must remain identical apart from the checkbox"
        if not after_line.startswith(expected_after):
            return f"{task_context.tasks_path.name}:{line_number}: the selected task must be marked complete"
        return None

    def _blocked_reason_from_codex(self, result: Any) -> str | None:
        if isinstance(result.result_json, dict):
            reason = result.result_json.get("reason")
            if isinstance(reason, str) and reason.strip():
                return reason.strip()
        if getattr(result, "parse_error", None):
            return str(result.parse_error)
        return None

    def _update_run(
        self,
        run: AutopilotRun,
        *,
        status: RunStatus,
        epic_id: str | None,
        branch_name: str | None,
        current_task_id: str | None,
        task_result: TaskResult,
        last_error: str | None,
    ) -> AutopilotRun:
        task_results = tuple([*run.task_results, task_result])
        return replace(
            run,
            status=status,
            updated_at=_timestamp(),
            epic_id=epic_id,
            branch_name=branch_name,
            current_task_id=current_task_id,
            task_results=task_results,
            last_error=last_error,
        )

    def _failed_result(
        self,
        run: AutopilotRun,
        task_id: str,
        command_results: Sequence[CommandResult],
        *,
        reason: str,
        attempts: int,
        task_context: TaskContext | None = None,
    ) -> TaskPipelineResult:
        task_result = TaskResult(
            task_id=task_id,
            status=RunStatus.FAILED,
            command_results=tuple(command_results),
            title=task_context.task_title if task_context is not None else None,
        )
        updated_run = self._update_run(
            run,
            status=RunStatus.FAILED,
            epic_id=run.epic_id,
            branch_name=run.branch_name,
            current_task_id=task_id,
            task_result=task_result,
            last_error=reason,
        )
        save_run_state(updated_run, root=self.root)
        return TaskPipelineResult(
            status=RunStatus.FAILED,
            run=updated_run,
            task_result=task_result,
            attempts=attempts,
            baseline_path="",
            allowlist=(),
            validation_commands=(),
            command_results=tuple(command_results),
            reason=reason,
        )

    def _blocked_result(
        self,
        run: AutopilotRun,
        task_id: str,
        command_results: Sequence[CommandResult],
        *,
        reason: str,
        attempts: int,
        task_context: TaskContext | None = None,
    ) -> TaskPipelineResult:
        task_result = TaskResult(
            task_id=task_id,
            status=RunStatus.BLOCKED,
            command_results=tuple(command_results),
            title=task_context.task_title if task_context is not None else None,
        )
        updated_run = self._update_run(
            run,
            status=RunStatus.BLOCKED,
            epic_id=run.epic_id,
            branch_name=run.branch_name,
            current_task_id=task_id,
            task_result=task_result,
            last_error=reason,
        )
        save_run_state(updated_run, root=self.root)
        return TaskPipelineResult(
            status=RunStatus.BLOCKED,
            run=updated_run,
            task_result=task_result,
            attempts=attempts,
            baseline_path="",
            allowlist=(),
            validation_commands=(),
            command_results=tuple(command_results),
            reason=reason,
        )

    def _write_receipt(
        self,
        *,
        task_context: TaskContext,
        run_id: str,
        state: TaskLifecycleState,
        summary: str,
        files_touched: Sequence[str],
        notes: Sequence[str],
        agent_outcome: str,
        commit_sha: str = "",
        validation: Sequence[dict[str, Any]] | None = None,
        review_verdict: str = "",
        safe_to_close: bool = False,
        closure_checkbox_before: str = "",
        closure_checkbox_after: str = "",
        closure_task_line: int = 0,
        stages: Sequence[TaskReceiptStage] = (),
    ) -> TaskReceiptRecord:
        record = TaskReceiptRecord(
            schema_version=1,
            run_id=run_id,
            task_id=task_context.task_id,
            updated_at=_timestamp(),
            state=state,
            agent_outcome=agent_outcome,
            summary=summary,
            files_touched=tuple(str(item).replace("\\", "/") for item in files_touched),
            notes=tuple(str(item) for item in notes),
            validation=tuple(dict(item) for item in (validation or ()) if isinstance(item, dict)),
            commit_sha=commit_sha,
            stages=tuple(stages),
            review_verdict=review_verdict,
            safe_to_close=safe_to_close,
            closure_checkbox_before=closure_checkbox_before,
            closure_checkbox_after=closure_checkbox_after,
            closure_task_line=closure_task_line,
        )
        save_task_receipt(record, root=self.root)
        return record

    def _close_task_checkbox(self, task_context: TaskContext, text: str) -> tuple[str, str, str]:
        lines = text.splitlines()
        before_line = ""
        after_line = ""
        updated_lines: list[str] = []
        replaced = False
        for line_number, line in enumerate(lines, start=1):
            if line_number == task_context.task_line:
                before_line = line
                if not line.startswith(f"- [ ] {task_context.task_id}") and not line.startswith(f"- [x] {task_context.task_id}") and not line.startswith(f"- [X] {task_context.task_id}"):
                    raise TaskPipelineError(f"{task_context.tasks_path.name}:{line_number}: selected task mismatch")
                after_line = line.replace("- [ ]", "- [X]", 1)
                updated_lines.append(after_line)
                replaced = True
            else:
                updated_lines.append(line)
        if not replaced:
            raise TaskPipelineError(f"{task_context.tasks_path.name}: could not locate task line {task_context.task_line}")
        verification = self._verify_task_completion_change(task_context, before_text=text, after_text="\n".join(updated_lines))
        if verification is not None:
            raise TaskPipelineError(verification)
        updated_text = "\n".join(updated_lines)
        if text.endswith("\n"):
            updated_text += "\n"
        return updated_text, before_line, after_line

    def _cancelled_result(
        self,
        run: AutopilotRun,
        task_id: str,
        command_results: Sequence[CommandResult],
        *,
        reason: str,
        attempts: int,
    ) -> TaskPipelineResult:
        task_result = TaskResult(
            task_id=task_id,
            status=RunStatus.CANCELLED,
            command_results=tuple(command_results),
        )
        updated_run = self._update_run(
            run,
            status=RunStatus.CANCELLED,
            epic_id=run.epic_id,
            branch_name=run.branch_name,
            current_task_id=task_id,
            task_result=task_result,
            last_error=reason,
        )
        save_run_state(updated_run, root=self.root)
        return TaskPipelineResult(
            status=RunStatus.CANCELLED,
            run=updated_run,
            task_result=task_result,
            attempts=attempts,
            baseline_path="",
            allowlist=(),
            validation_commands=(),
            command_results=tuple(command_results),
            reason=reason,
        )

    def _command_result_from_process(self, result: process_runner.ProcessResult) -> CommandResult:
        return CommandResult(
            command=tuple(result.command),
            status=result.status,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            timed_out=result.timed_out,
            stdout_lines=tuple(result.stdout_lines),
            stderr_lines=tuple(result.stderr_lines),
            output_truncated=result.output_truncated,
        )

    def _command_result_to_payload(self, result: CommandResult) -> dict[str, Any]:
        return {
            "command": list(result.command),
            "status": result.status,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "timed_out": result.timed_out,
            "stdout_lines": list(result.stdout_lines),
            "stderr_lines": list(result.stderr_lines),
            "output_truncated": result.output_truncated,
        }

    def _command_results_from_codex(self, result: Any) -> tuple[CommandResult, ...]:
        command_result = CommandResult(
            command=tuple(result.command),
            status=result.status,
            exit_code=result.exit_code,
            duration_ms=0,
            timed_out=result.timed_out,
            stdout_lines=tuple(result.stdout_lines),
            stderr_lines=tuple(result.stderr_lines),
            output_truncated=result.output_truncated,
        )
        return (command_result,)

    def _command_result_from_json(self, value: Any) -> CommandResult:
        if isinstance(value, dict):
            return CommandResult(
                command=tuple(str(part) for part in value.get("command", ("preflight",))),
                status=str(value.get("status", "PASS")),
                exit_code=value.get("exit_code"),
                duration_ms=int(value.get("duration_ms", 0)),
                timed_out=bool(value.get("timed_out", False)),
                stdout_lines=tuple(str(item) for item in value.get("stdout_lines", ()) or ()),
                stderr_lines=tuple(str(item) for item in value.get("stderr_lines", ()) or ()),
                output_truncated=bool(value.get("output_truncated", False)),
            )
        return CommandResult(
            command=("preflight",),
            status="PASS",
            exit_code=0,
            duration_ms=0,
            timed_out=False,
            stdout_lines=(),
            stderr_lines=(),
            output_truncated=False,
        )


def run_task_pipeline(
    run: AutopilotRun,
    *,
    task_id: str,
    root: Path | str = ROOT,
    config: AutopilotConfig | None = None,
    process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
    cancel_event: Any | None = None,
) -> TaskPipelineResult:
    return TaskPipeline(root, config=config, process_runner_fn=process_runner_fn).run_task(run, task_id=task_id, cancel_event=cancel_event)


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _combine_lines(lines: Sequence[str]) -> str:
    return "\n".join(lines)


def _parse_preflight_json_document(text: str) -> dict[str, Any]:
    normalized = text.strip()
    if not normalized:
        raise TaskPipelineError("preflight did not return JSON")
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise TaskPipelineError(f"preflight output is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TaskPipelineError("preflight JSON must be an object")
    return payload


def _load_json_mapping(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TaskPipelineError(f"{path.name}: baseline must be a JSON object")
    return payload


def _path_exists(path: Path) -> bool:
    return path.exists()


def _resolve_path(root: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return root / candidate


def _safe_validation_command_argv(command: str) -> list[str]:
    normalized = command.strip().strip("`").strip()
    if not normalized:
        raise TaskPipelineError("validation command is empty")
    if any(operator in normalized for operator in FORBIDDEN_SHELL_OPERATORS):
        raise TaskPipelineError(f"validation command contains forbidden shell operators: {command!r}")
    try:
        argv = shlex.split(normalized, posix=True)
    except ValueError as exc:
        raise TaskPipelineError(f"validation command is not parseable: {command!r}") from exc
    if not argv:
        raise TaskPipelineError("validation command is empty")
    return argv


def _normalize_python_launcher(argv: Sequence[str], python_executable: str) -> list[str]:
    if not argv:
        return []
    launcher = Path(argv[0]).name.lower()
    if launcher in {"python", "python.exe", "python3", "python3.exe"}:
        return [python_executable, *argv[1:]]
    if launcher == "py" and len(argv) >= 2 and argv[1] in {"-3", "-3.11"}:
        return [python_executable, *argv[2:]]
    return list(argv)


def _is_diff_check_command(argv: Sequence[str]) -> bool:
    parts = list(argv)
    return parts[:3] == ["git", "diff", "--check"] or parts[:4] == ["git", "--no-pager", "diff", "--check"]


def _split_comma_list(value: str) -> list[str]:
    normalized = value.strip().strip("`")
    if not normalized or normalized.lower() in NO_DEPENDENCY_VALUES:
        return []
    return [item.strip().strip("`") for item in normalized.split(",") if item.strip()]


def _split_validation_commands(value: str) -> list[str]:
    normalized = value.strip()
    if not normalized or normalized.lower() in NO_DEPENDENCY_VALUES:
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
        raise TaskPipelineError(f"validation commands contain unterminated quote: {value!r}")
    command = "".join(current).strip().strip("`").strip()
    if command:
        commands.append(command)
    return commands


def _path_allowed(path: str, allowlist: Sequence[str]) -> bool:
    normalized_path = path.replace("\\", "/").strip()
    if not normalized_path:
        return False
    for item in allowlist:
        normalized_item = item.replace("\\", "/").strip().rstrip("/")
        if not normalized_item:
            continue
        if normalized_path == normalized_item or normalized_path.startswith(f"{normalized_item}/"):
            return True
    return False


def _require_text(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TaskPipelineError(f"{key} is missing from preflight output")
    return value.strip()


__all__ = [
    "TaskContext",
    "TaskPipeline",
    "TaskPipelineError",
    "TaskPipelineResult",
    "run_task_pipeline",
]

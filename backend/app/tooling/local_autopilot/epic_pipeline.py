"""Deterministic pipeline for an entire local epic."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from app.tooling import epic_review_receipt
from app.tooling import task_consistency
from app.tooling import workstream_validation

from . import process_runner, repository as repository_module, validation_receipt
from .codex_adapter import CodexAdapter
from .config import AutopilotConfig, DEFAULT_AUTOPILOT_CONFIG_PATH, load_autopilot_config
from .recovery import archive_completed_epic_runtime, archive_task_attempt
from .github_adapter import GitHubAdapter, GitHubAuthResult
from .models import AutopilotRun, CommandResult, PullRequestInfo, RunMode, RunStatus, TaskResult
from .state_store import save_run_state
from .task_pipeline import TaskPipeline, TaskPipelineResult
from .workstreams import (
    activate_epic_with_human_authorization,
    all_epic_tasks_complete,
    get_epic,
    next_dependency_ready_task,
    validate_dependencies,
)
from .task_state_machine import _load_task_snapshot, _path_allowed, load_task_receipt, load_task_state, save_task_state
from .task_state_machine import TaskLifecycleState

ROOT = Path(__file__).resolve().parents[4]
ACTIVE_EPIC_FILE = ROOT / ".specify" / "runtime" / "active-epic"
WORKSTREAMS_DIR = ROOT / ".specify" / "workstreams"
TASKS_FILE = ROOT / "specs" / "001-ai-content-studio" / "tasks.md"
EPIC_ID_PATTERN = re.compile(r"^E\d{3}$")
TASK_ID_SUBJECT_PATTERN = re.compile(r"^(feat|fix|test|chore|refactor)\((T\d{3}[A-Z]?)\):")
EPIC_MAINTENANCE_SUBJECT_PATTERN = re.compile(r"(activate epic|close epic|epic closure)", re.IGNORECASE)


def build_push_failure_reason(result: process_runner.ProcessResult, *, timeout_seconds: int | None = None) -> str:
    detail = _push_failure_detail(result)
    parts = ["push failed:"]
    if result.status == "TIMEOUT" or result.timed_out:
        parts.append("status=TIMEOUT")
        if timeout_seconds is not None:
            parts.append(f"timeout={timeout_seconds}s")
    elif result.exit_code is not None:
        parts.append(f"exit_code={result.exit_code}")
    if detail:
        parts.append(detail)
    command = " ".join(str(part) for part in result.command)
    if result.status == "TIMEOUT" or result.timed_out:
        parts.append(f"command={command}")
    elif not detail:
        parts.append(f"command={command}")
    return " ".join(parts)


def _push_failure_detail(result: process_runner.ProcessResult) -> str | None:
    candidates = [line.strip() for line in (*result.stderr_lines, *result.stdout_lines) if line.strip()]
    if not candidates:
        return None
    for needle in ("timed out after", "timed out", "remote rejected", "rejected", "denied", "failed"):
        for line in candidates:
            if needle in line.lower():
                return process_runner.redact_sensitive_text(line)
    return process_runner.redact_sensitive_text(candidates[0])


def _process_result_detail(result: process_runner.ProcessResult) -> str | None:
    for line in (*result.stderr_lines, *result.stdout_lines):
        cleaned = process_runner.redact_sensitive_text(line.strip())
        if cleaned:
            return cleaned
    return None


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
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
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


@dataclass(frozen=True)
class EpicPipelineResult:
    status: RunStatus
    run: AutopilotRun
    epic_id: str
    branch_name: str
    task_ids: tuple[str, ...]
    task_results: tuple[TaskResult, ...]
    command_results: tuple[CommandResult, ...]
    review_receipt_path: str | None = None
    pull_request: PullRequestInfo | None = None
    implementation_pull_request: PullRequestInfo | None = None
    closure_pull_request: PullRequestInfo | None = None
    activation_commit_sha: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class TaskEvidenceResolution:
    task_id: str
    commit_sha: str
    source: str
    evidence_paths: tuple[str, ...]
    legacy_bundle: bool


class EpicPipelineError(RuntimeError):
    pass


class EpicPipeline:
    def __init__(
        self,
        root: Path | str = ROOT,
        *,
        config: AutopilotConfig | None = None,
        repository: repository_module.Repository | None = None,
        task_pipeline_factory: Callable[[Path, AutopilotConfig, Callable[..., process_runner.ProcessResult]], TaskPipeline] | None = None,
        github_adapter: GitHubAdapter | None = None,
        process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
        review_receipt_writer: Callable[..., Path] = epic_review_receipt.write_review_receipt,
        review_receipt_validator: Callable[..., list[str]] = epic_review_receipt.validate_review_receipt_file,
        config_path: Path | str = DEFAULT_AUTOPILOT_CONFIG_PATH,
        active_epic_file: Path | None = None,
        workstreams_dir: Path | None = None,
        tasks_file: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self._run = process_runner_fn
        self.config = config or load_autopilot_config(config_path)
        self.repository = repository or repository_module.Repository(self.root, process_runner_fn=process_runner_fn)
        self.task_pipeline_factory = task_pipeline_factory or self._default_task_pipeline_factory
        self.github = github_adapter or GitHubAdapter(self.root, process_runner_fn=process_runner_fn)
        self.review_receipt_writer = review_receipt_writer
        self.review_receipt_validator = review_receipt_validator
        self.active_epic_file = active_epic_file or (self.root / ".specify" / "runtime" / "active-epic")
        self.workstreams_dir = workstreams_dir or (self.root / ".specify" / "workstreams")
        self.tasks_file = tasks_file or (self.root / "specs" / "001-ai-content-studio" / "tasks.md")

    def run_epic(
        self,
        run: AutopilotRun,
        *,
        human_authorized: bool | None = None,
        cancel_event: Any | None = None,
    ) -> EpicPipelineResult:
        task_results: list[TaskResult] = []
        command_results: list[CommandResult] = []
        task_ids: list[str] = []
        current_run = run
        try:
            self._require_not_cancelled(cancel_event)
            current_status = self.repository.status()
            completed_tasks_this_run = sum(1 for task_result in current_run.task_results if task_result.status == RunStatus.COMPLETED)

            epic_id = self._epic_id_for(run)
            epic_manifest = get_epic(epic_id, self.workstreams_dir)
            branch_name = str(epic_manifest.get("branch") or "")
            if not branch_name:
                raise EpicPipelineError(f"{epic_id} manifest is missing branch")
            base_branch = str(epic_manifest.get("base_branch") or "").strip()
            if not base_branch:
                raise EpicPipelineError(f"{epic_id} manifest is missing base_branch")
            dependency_errors = validate_dependencies(epic_id, self.workstreams_dir)
            if dependency_errors:
                raise EpicPipelineError("; ".join(dependency_errors))

            dirty_worktree = not current_status.clean
            if dirty_worktree:
                return self._finalize_blocked(
                    current_run,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=tuple(command_results),
                    reason="active epic must recover or commit the current task before syncing with base",
                    activation_commit_sha=None,
                )

            initial_status = str(epic_manifest.get("status") or "")
            if initial_status == "planned":
                if human_authorized is None:
                    human_authorized = bool(run.request.human_authorized)
                self.repository.create_branch(branch_name, base_branch=base_branch)
            elif initial_status == "active":
                if not dirty_worktree:
                    self.repository.create_branch(branch_name, base_branch=base_branch)
            elif initial_status != "completed":
                raise EpicPipelineError(f"{epic_id} manifest is missing status or has unsupported status {initial_status!r}")

            epic_manifest = get_epic(epic_id, self.workstreams_dir)
            branch_status = str(epic_manifest.get("status") or "")
            if branch_status == "completed":
                if run.request.run_mode == RunMode.STOP_BEFORE_PUSH:
                    finalized_run = self._finalize_run(
                        current_run,
                        status=RunStatus.COMPLETED,
                        epic_id=epic_id,
                        branch_name=branch_name,
                        task_results=task_results,
                        command_results=command_results,
                        pull_request=None,
                    )
                    self._clear_active_epic_marker_if_matches(epic_id)
                    save_run_state(finalized_run, root=self.root)
                    return EpicPipelineResult(
                        status=RunStatus.COMPLETED,
                        run=finalized_run,
                        epic_id=epic_id,
                        branch_name=branch_name,
                        task_ids=self._epic_task_ids(epic_manifest),
                        task_results=tuple(task_results),
                        command_results=tuple(command_results),
                        activation_commit_sha=None,
                    )
                return self._finalize_completed_epic(
                    current_run,
                    epic_id=epic_id,
                    base_branch=base_branch,
                    branch_name=branch_name,
                    epic_manifest=epic_manifest,
                    task_results=task_results,
                    command_results=command_results,
                    activation_commit_sha=None,
                    preflight_auth=None,
                )

            if branch_status == "planned":
                if human_authorized is None:
                    human_authorized = bool(run.request.human_authorized)
                if not human_authorized:
                    raise EpicPipelineError("human authorization is required to activate a planned epic")
                activate_epic_with_human_authorization(
                    epic_id,
                    human_authorized=True,
                    directory=self.workstreams_dir,
                )
                self._stage_and_commit_activation(epic_manifest_path=self._epic_manifest_path(epic_id), epic_id=epic_id)
                activation_commit_sha = self.repository.head_sha()
            else:
                activation_commit_sha = None

            self._write_active_epic(epic_id)

            self.repository.require_clean_tree()
            sync_source_head = self.repository.head_sha()
            self.repository.merge_base_into_active_branch(
                branch_name,
                base_branch,
                timeout_seconds=self.config.command_timeout_seconds,
            )
            sync_target_head = self.repository.head_sha()
            if sync_source_head != sync_target_head:
                self._refresh_task_baselines_after_head_change(
                    epic_id,
                    epic_manifest,
                    old_head_sha=sync_source_head,
                    new_head_sha=sync_target_head,
                )

            current_run = replace(
                current_run,
                status=RunStatus.PREFLIGHT,
                updated_at=_timestamp(),
                epic_id=epic_id,
                branch_name=branch_name,
            )
            save_run_state(current_run, root=self.root)

            preflight_auth = self._preflight_environment(
                current_run,
                epic_manifest,
                run_mode=run.request.run_mode,
                cancel_event=cancel_event,
            )

            current_run = replace(current_run, status=RunStatus.ACTIVATING, updated_at=_timestamp())
            save_run_state(current_run, root=self.root)

            if run.request.run_mode == RunMode.FULL:
                lifecycle_result = self._reconcile_active_epic_state(
                    current_run,
                    epic_id=epic_id,
                    epic_manifest=epic_manifest,
                    base_branch=base_branch,
                    branch_name=branch_name,
                    task_results=task_results,
                    command_results=command_results,
                    activation_commit_sha=activation_commit_sha,
                    preflight_auth=preflight_auth,
                    cancel_event=cancel_event,
                )
                if lifecycle_result is not None:
                    return lifecycle_result

            task_pipeline = self.task_pipeline_factory(self.root, self.config, self._run)
            while True:
                next_task_id = next_dependency_ready_task(epic_id, tasks_file=self.tasks_file, directory=self.workstreams_dir)
                if next_task_id is None:
                    break
                task_ids.append(next_task_id)
                status = RunStatus.TASK_RUNNING
                task_result_bundle = task_pipeline.run_task(current_run, task_id=next_task_id, cancel_event=cancel_event)
                task_results.append(task_result_bundle.task_result)
                command_results.extend(task_result_bundle.command_results)
                current_run = task_result_bundle.run
                save_run_state(current_run, root=self.root)
                if task_result_bundle.status == RunStatus.PAUSED:
                    return self._finalize_paused(
                        current_run,
                        epic_id=epic_id,
                        branch_name=branch_name,
                        task_ids=tuple(task_ids),
                        task_results=tuple(task_results),
                        command_results=tuple(command_results),
                        reason=task_result_bundle.reason or "task pipeline paused",
                        activation_commit_sha=activation_commit_sha,
                    )
                if task_result_bundle.status == RunStatus.BLOCKED:
                    return self._finalize_blocked(
                        current_run,
                        epic_id=epic_id,
                        branch_name=branch_name,
                        task_ids=tuple(task_ids),
                        task_results=tuple(task_results),
                        command_results=tuple(command_results),
                        reason=task_result_bundle.reason or "task pipeline blocked",
                        activation_commit_sha=activation_commit_sha,
                    )
                if task_result_bundle.status != RunStatus.COMPLETED:
                    return self._finalize_failure(
                        current_run,
                        epic_id=epic_id,
                        branch_name=branch_name,
                        task_ids=tuple(task_ids),
                        task_results=tuple(task_results),
                        command_results=tuple(command_results),
                        reason=task_result_bundle.reason or "task pipeline failed",
                        activation_commit_sha=activation_commit_sha,
                    )
                if task_result_bundle.status == RunStatus.COMPLETED:
                    completed_tasks_this_run += 1
                    if completed_tasks_this_run >= self.config.max_tasks_per_run:
                        return self._finalize_paused(
                            current_run,
                            epic_id=epic_id,
                            branch_name=branch_name,
                            task_ids=tuple(task_ids),
                            task_results=tuple(task_results),
                            command_results=tuple(command_results),
                            reason=f"max_tasks_per_run reached: {self.config.max_tasks_per_run}",
                            activation_commit_sha=activation_commit_sha,
                        )

            if not all_epic_tasks_complete(epic_id, tasks_file=self.tasks_file, directory=self.workstreams_dir):
                return self._finalize_failure(
                    current_run,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=tuple(command_results),
                    reason="epic tasks are not all complete",
                    activation_commit_sha=activation_commit_sha,
                )

            evidence_errors = self._verify_task_evidence(epic_id, epic_manifest, task_results)
            if evidence_errors:
                return self._finalize_failure(
                    current_run,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=tuple(command_results),
                    reason="; ".join(evidence_errors),
                    activation_commit_sha=activation_commit_sha,
                )

            expected_required_commands = self._required_check_commands(epic_manifest)
            required_check_results = self._run_required_checks(epic_manifest, command_results, cancel_event=cancel_event)
            if len(required_check_results) != len(expected_required_commands):
                return self._finalize_failure(
                    current_run,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=tuple(command_results),
                    reason=(
                        "required checks results count does not match the epic manifest: "
                        f"declared={len(expected_required_commands)} actual={len(required_check_results)}"
                    ),
                    activation_commit_sha=activation_commit_sha,
                )
            command_results.extend(required_check_results)
            if any(result.status != "PASS" for result in required_check_results):
                return self._finalize_failure(
                    current_run,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=tuple(command_results),
                    reason="required checks failed",
                    activation_commit_sha=activation_commit_sha,
                )

            review_receipt_path = self._write_review_receipt(epic_manifest, required_check_results)
            receipt_errors = self._validate_review_receipt(epic_id, epic_manifest, review_receipt_path)
            if receipt_errors:
                return self._finalize_failure(
                    current_run,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=tuple(command_results),
                    reason="; ".join(receipt_errors),
                    activation_commit_sha=activation_commit_sha,
                )

            self._write_validation_receipt(epic_manifest, required_check_results)

            if run.request.run_mode == RunMode.STOP_BEFORE_PUSH:
                finalized_run = self._finalize_run(
                    current_run,
                    status=RunStatus.COMPLETED,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    task_results=task_results,
                    command_results=command_results,
                    pull_request=None,
                )
                save_run_state(finalized_run, root=self.root)
                return EpicPipelineResult(
                    status=RunStatus.COMPLETED,
                    run=finalized_run,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=tuple(command_results),
                    review_receipt_path=str(review_receipt_path),
                    activation_commit_sha=activation_commit_sha,
                )

            if not self.config.auto_push:
                return self._finalize_paused(
                    current_run,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=tuple(command_results),
                    reason="epic validated; automatic push disabled",
                    activation_commit_sha=activation_commit_sha,
                    pull_request=None,
                    implementation_pull_request=current_run.implementation_pull_request,
                    closure_pull_request=current_run.closure_pull_request,
                )

            if preflight_auth is not None:
                command_results.extend(self._auth_to_command_result(preflight_auth))

            current_run = replace(current_run, status=RunStatus.PUSHING, updated_at=_timestamp())
            save_run_state(current_run, root=self.root)

            push_result = self.repository.push(branch_name, timeout_seconds=self.config.push_timeout_seconds)
            self._write_push_result(current_run, push_result)
            command_results.append(self._command_result_from_process(push_result))
            if push_result.status != "PASS":
                return self._finalize_failure(
                    current_run,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=tuple(command_results),
                    reason=build_push_failure_reason(push_result, timeout_seconds=self.config.push_timeout_seconds),
                    activation_commit_sha=activation_commit_sha,
                )

            if not self.config.create_draft_pr:
                return self._finalize_paused(
                    current_run,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=command_results,
                    reason="branch pushed; automatic PR creation disabled",
                    activation_commit_sha=activation_commit_sha,
                    pull_request=None,
                    implementation_pull_request=current_run.implementation_pull_request,
                    closure_pull_request=current_run.closure_pull_request,
                )

            current_run = replace(current_run, status=RunStatus.PR_CREATING, updated_at=_timestamp())
            save_run_state(current_run, root=self.root)

            pull_request = self.github.find_pr(
                self._base_branch_for(run),
                branch_name,
                timeout_seconds=self.config.command_timeout_seconds,
            )
            if pull_request is None:
                pull_request = self.github.create_draft_pr(
                    self._base_branch_for(run),
                    branch_name,
                    self._pr_title(epic_id, epic_manifest),
                    self._pr_body(epic_id, epic_manifest, task_ids, review_receipt_path),
                    timeout_seconds=self.config.command_timeout_seconds,
                )

            finalized_run = self._finalize_run(
                current_run,
                status=RunStatus.WAITING_FOR_MERGE,
                epic_id=epic_id,
                branch_name=branch_name,
                task_results=task_results,
                command_results=command_results,
                pull_request=pull_request,
            )
            save_run_state(finalized_run, root=self.root)
            return EpicPipelineResult(
                status=RunStatus.WAITING_FOR_MERGE,
                run=finalized_run,
                epic_id=epic_id,
                branch_name=branch_name,
                task_ids=tuple(task_ids),
                task_results=tuple(task_results),
                command_results=tuple(command_results),
                review_receipt_path=str(review_receipt_path),
                pull_request=pull_request,
                activation_commit_sha=activation_commit_sha,
            )
        except (KeyboardInterrupt, EpicPipelineError, RuntimeError, ValueError, FileNotFoundError, OSError) as exc:
            if isinstance(exc, KeyboardInterrupt):
                finalized_run = self._finalize_run(
                    current_run,
                    status=RunStatus.CANCELLED,
                    epic_id=current_run.epic_id,
                    branch_name=current_run.branch_name,
                    task_results=task_results,
                    command_results=command_results,
                    pull_request=current_run.pull_request,
                    last_error="cancelled",
                )
                save_run_state(finalized_run, root=self.root)
                return EpicPipelineResult(
                    status=RunStatus.CANCELLED,
                    run=finalized_run,
                    epic_id=current_run.epic_id or "",
                    branch_name=current_run.branch_name or "",
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=tuple(command_results),
                    reason="cancelled",
                )
            finalized_run = self._finalize_run(
                current_run,
                status=RunStatus.FAILED,
                epic_id=current_run.epic_id,
                branch_name=current_run.branch_name,
                task_results=task_results,
                command_results=command_results,
                pull_request=current_run.pull_request,
                last_error=str(exc),
            )
            save_run_state(finalized_run, root=self.root)
            return EpicPipelineResult(
                status=RunStatus.FAILED,
                run=finalized_run,
                epic_id=current_run.epic_id or "",
                branch_name=current_run.branch_name or "",
                task_ids=tuple(task_ids),
                task_results=tuple(task_results),
                command_results=tuple(command_results),
                reason=str(exc),
            )

    def _default_task_pipeline_factory(
        self,
        root: Path,
        config: AutopilotConfig,
        process_runner_fn: Callable[..., process_runner.ProcessResult],
    ) -> TaskPipeline:
        return TaskPipeline(root, config=config, process_runner_fn=process_runner_fn)

    def _epic_id_for(self, run: AutopilotRun) -> str:
        epic_id = run.epic_id or (run.request.scope_id if run.request.scope_type.value == "epic" else "")
        if not epic_id or not EPIC_ID_PATTERN.fullmatch(epic_id):
            raise EpicPipelineError("run does not declare a valid epic id")
        return epic_id

    def _base_branch_for(self, run: AutopilotRun) -> str:
        epic_id = self._epic_id_for(run)
        epic = get_epic(epic_id, self.workstreams_dir)
        base_branch = str(epic.get("base_branch") or "").strip()
        if not base_branch:
            raise EpicPipelineError(f"{epic_id} manifest is missing base_branch")
        return base_branch

    def _epic_manifest_path(self, epic_id: str) -> Path:
        for path in sorted(self.workstreams_dir.glob("*.yml")):
            manifest = workstream_validation._load_yaml_manifest(path)
            if manifest.get("id") == epic_id:
                return path
        raise FileNotFoundError(f"epic manifest does not exist: {epic_id}")

    def _write_active_epic(self, epic_id: str) -> None:
        self.active_epic_file.parent.mkdir(parents=True, exist_ok=True)
        self.active_epic_file.write_text(f"{epic_id}\n", encoding="utf-8")

    def _stage_and_commit_activation(self, *, epic_manifest_path: Path, epic_id: str) -> None:
        relative_path = epic_manifest_path.relative_to(self.root).as_posix()
        self.repository.stage_allowlist([relative_path])
        result = self.repository.commit(f"feat({epic_id}): activate epic")
        if result.status != "PASS":
            raise EpicPipelineError("activation commit failed")

    def _required_check_commands(self, epic_manifest: dict[str, Any]) -> list[str]:
        return [command for command in (epic_manifest.get("required_checks") or []) if isinstance(command, str) and command.strip()]

    def _run_required_checks(
        self,
        epic_manifest: dict[str, Any],
        command_results: Sequence[CommandResult],
        *,
        cancel_event: Any | None,
    ) -> tuple[CommandResult, ...]:
        results: list[CommandResult] = []
        required_commands = self._required_check_commands(epic_manifest)
        if not required_commands:
            raise EpicPipelineError("epic manifest does not declare required checks")
        python_executable = self._resolve_agent_python()
        for command in required_commands:
            argv = self._safe_command(command, python_executable)
            if self._is_diff_check_command(argv):
                result = self.repository.diff_check(cached=False)
            else:
                result = self._run(
                    argv,
                    cwd=self.root,
                    timeout_seconds=self.config.command_timeout_seconds,
                    cancel_event=cancel_event,
                    heartbeat_seconds=0,
                )
            results.append(self._command_result_from_process(result))
            if result.status != "PASS":
                break
        return tuple(results)

    def _verify_task_evidence(
        self,
        epic_id: str,
        epic_manifest: dict[str, Any],
        task_results: Sequence[TaskResult],
    ) -> list[str]:
        errors: list[str] = []
        try:
            self.repository.require_clean_tree()
        except Exception as exc:
            errors.append(f"{epic_id}: {exc}")
        current_head = self.repository.head_sha()
        commit_history = self.repository.list_commit_history(ref="HEAD")
        history_by_sha = {sha: subject for sha, subject in commit_history}
        commit_files_cache: dict[str, tuple[str, ...]] = {}
        try:
            activation_commit_sha = self._epic_activation_commit_sha(epic_id, history_by_sha)
        except EpicPipelineError as exc:
            errors.append(str(exc))
            activation_commit_sha = None
        manifest_task_ids = tuple(
            str(task_id).strip()
            for task_id in (epic_manifest.get("tasks") or [])
            if isinstance(task_id, str) and str(task_id).strip()
        )
        tasks_md_task_ids = self._epic_tasks_from_tasks_file(epic_id)
        manifest_set = set(manifest_task_ids)
        tasks_md_set = set(tasks_md_task_ids)
        if manifest_set != tasks_md_set:
            missing = sorted(manifest_set - tasks_md_set)
            extra = sorted(tasks_md_set - manifest_set)
            details: list[str] = []
            if missing:
                details.append(f"missing={missing}")
            if extra:
                details.append(f"extra={extra}")
            errors.append(f"{epic_id}: tasks.md task IDs do not match the manifest ({'; '.join(details)})")
        task_results_by_id = {str(result.task_id): result for result in task_results if str(result.task_id).strip()}
        resolved_evidence: dict[str, list[TaskEvidenceResolution]] = {}
        for task_id in manifest_task_ids:
            try:
                snapshot = _load_task_snapshot(task_id, self.tasks_file)
            except Exception as exc:
                errors.append(f"{task_id}: {exc}")
                continue
            if snapshot.epic_id != epic_id:
                errors.append(f"{task_id}: tasks.md belongs to epic {snapshot.epic_id!r}, expected {epic_id!r}")
                continue
            if snapshot.checkbox.upper() != "X":
                errors.append(f"{task_id}: tasks.md checkbox is [{snapshot.checkbox}]")
                continue
            state_record = load_task_state(task_id, root=self.root)
            receipt_record = load_task_receipt(task_id, root=self.root)
            sources_checked: list[str] = []
            try:
                resolution = self._resolve_task_evidence(
                    task_id,
                    snapshot,
                    state_record=state_record,
                    receipt_record=receipt_record,
                    history_by_sha=history_by_sha,
                    commit_files_cache=commit_files_cache,
                    sources_checked=sources_checked,
                    epic_id=epic_id,
                    activation_commit_sha=activation_commit_sha,
                )
                if resolution is None:
                    errors.append(f"{task_id}: no task evidence found [sources={', '.join(sources_checked) or 'none'}]")
                    continue
                if not self.repository.is_ancestor(resolution.commit_sha, current_head):
                    errors.append(
                        f"{task_id}: commit {resolution.commit_sha} is not an ancestor of HEAD {current_head} "
                        f"[source={resolution.source}]"
                    )
                    continue
                existing_resolutions = resolved_evidence.get(resolution.commit_sha, [])
                if existing_resolutions:
                    if not resolution.legacy_bundle or any(not existing.legacy_bundle for existing in existing_resolutions):
                        errors.append(
                            f"{task_id}: commit SHA {resolution.commit_sha} is shared with {existing_resolutions[0].task_id} "
                            f"[source={resolution.source}]"
                        )
                        continue
                    resolution_paths = set(resolution.evidence_paths)
                    overlap = next(
                        (
                            existing
                            for existing in existing_resolutions
                            if set(existing.evidence_paths) & resolution_paths
                        ),
                        None,
                    )
                    if overlap is not None:
                        errors.append(
                            f"{task_id}: legacy evidence paths overlap with {overlap.task_id} "
                            f"[source={resolution.source}]"
                        )
                        continue
                resolved_evidence.setdefault(resolution.commit_sha, []).append(resolution)
            except EpicPipelineError as exc:
                errors.append(str(exc))
        return errors

    def _resolve_task_evidence(
        self,
        task_id: str,
        snapshot: TaskSnapshot,
        *,
        state_record,
        receipt_record,
        history_by_sha: dict[str, str],
        commit_files_cache: dict[str, tuple[str, ...]],
        sources_checked: list[str],
        epic_id: str,
        activation_commit_sha: str | None,
    ) -> TaskEvidenceResolution | None:
        persisted = self._resolve_persisted_task_evidence(
            task_id,
            snapshot,
            state_record=state_record,
            receipt_record=receipt_record,
            history_by_sha=history_by_sha,
            commit_files_cache=commit_files_cache,
            sources_checked=sources_checked,
        )
        if persisted is not None:
            return persisted

        task_id_subject_sha = self._resolve_task_id_subject_evidence(
            task_id,
            snapshot=snapshot,
            commit_files_cache=commit_files_cache,
            history_by_sha=history_by_sha,
            sources_checked=sources_checked,
        )
        if task_id_subject_sha is not None:
            return task_id_subject_sha

        legacy_sha = self._resolve_legacy_task_evidence(
            task_id,
            snapshot,
            epic_id=epic_id,
            activation_commit_sha=activation_commit_sha,
            history_by_sha=history_by_sha,
            commit_files_cache=commit_files_cache,
            sources_checked=sources_checked,
        )
        return legacy_sha

    def _resolve_persisted_task_evidence(
        self,
        task_id: str,
        snapshot: TaskSnapshot,
        *,
        state_record,
        receipt_record,
        history_by_sha: dict[str, str],
        commit_files_cache: dict[str, tuple[str, ...]],
        sources_checked: list[str],
    ) -> TaskEvidenceResolution | None:
        if state_record is None and receipt_record is None:
            return None
        sources_checked.append("persisted_state_receipt")
        if state_record is None or receipt_record is None:
            raise EpicPipelineError(f"{task_id}: committed task state and receipt must both exist")
        if state_record.state != TaskLifecycleState.COMMITTED or receipt_record.state != TaskLifecycleState.COMMITTED:
            raise EpicPipelineError(f"{task_id}: committed task state and receipt must both be COMMITTED")
        if not state_record.head_sha or not receipt_record.commit_sha:
            raise EpicPipelineError(f"{task_id}: committed task state and receipt must both record a SHA")
        if state_record.head_sha != receipt_record.commit_sha:
            raise EpicPipelineError(
                f"{task_id}: state.head_sha and receipt.commit_sha differ "
                f"({state_record.head_sha} != {receipt_record.commit_sha})"
            )
        candidate_sha = state_record.head_sha
        if candidate_sha not in history_by_sha:
            raise EpicPipelineError(f"{task_id}: persisted SHA {candidate_sha} is missing from HEAD history")
        evidence_paths = self._commit_evidence_paths(snapshot, candidate_sha, commit_files_cache)
        return TaskEvidenceResolution(
            task_id=task_id,
            commit_sha=candidate_sha,
            source="persisted_state_receipt",
            evidence_paths=evidence_paths,
            legacy_bundle=False,
        )

    def _resolve_task_id_subject_evidence(
        self,
        task_id: str,
        *,
        snapshot: TaskSnapshot,
        commit_files_cache: dict[str, tuple[str, ...]],
        history_by_sha: dict[str, str],
        sources_checked: list[str],
    ) -> TaskEvidenceResolution | None:
        sources_checked.append("task_id_subject")
        pattern = re.compile(rf"^(feat|fix|test|chore|refactor)\({re.escape(task_id)}\):")
        candidates = [sha for sha, subject in history_by_sha.items() if pattern.match(subject.strip())]
        if len(candidates) == 1:
            sha = candidates[0]
            subject = history_by_sha.get(sha, "")
            if EPIC_MAINTENANCE_SUBJECT_PATTERN.search(subject):
                raise EpicPipelineError(f"{task_id}: task-id subject evidence points to an epic maintenance commit [sources=task_id_subject]")
            evidence_paths = self._commit_evidence_paths(snapshot, sha, commit_files_cache)
            return TaskEvidenceResolution(
                task_id=task_id,
                commit_sha=sha,
                source="task_id_subject",
                evidence_paths=evidence_paths,
                legacy_bundle=False,
            )
        if len(candidates) > 1:
            raise EpicPipelineError(f"{task_id}: multiple task-id subject commits found [sources=task_id_subject]")
        return None

    def _resolve_legacy_task_evidence(
        self,
        task_id: str,
        snapshot: TaskSnapshot,
        *,
        epic_id: str,
        activation_commit_sha: str | None,
        history_by_sha: dict[str, str],
        commit_files_cache: dict[str, tuple[str, ...]],
        sources_checked: list[str],
    ) -> TaskEvidenceResolution | None:
        sources_checked.append("legacy_test_file_addition")
        candidate_shas = self._legacy_addition_candidates(snapshot, history_by_sha, commit_files_cache)
        if len(candidate_shas) == 1:
            candidate_sha = next(iter(candidate_shas))
            subject = history_by_sha.get(candidate_sha, "")
            if TASK_ID_SUBJECT_PATTERN.search(subject):
                raise EpicPipelineError(
                    f"{task_id}: legacy candidate {candidate_sha} has task-id subject evidence "
                    f"[sources=legacy_test_file_addition]"
                )
            if EPIC_MAINTENANCE_SUBJECT_PATTERN.search(subject):
                raise EpicPipelineError(
                    f"{task_id}: legacy candidate {candidate_sha} is an epic maintenance commit "
                    f"[sources=legacy_test_file_addition]"
                )
            if activation_commit_sha is None or not self.repository.is_ancestor(candidate_sha, activation_commit_sha) or candidate_sha == activation_commit_sha:
                raise EpicPipelineError(
                    f"{task_id}: legacy candidate {candidate_sha} is not older than the {epic_id} activation commit "
                    f"[sources=legacy_test_file_addition]"
                )
            evidence_paths = self._commit_evidence_paths(snapshot, candidate_sha, commit_files_cache)
            if not evidence_paths:
                raise EpicPipelineError(
                    f"{task_id}: legacy candidate {candidate_sha} is not linked to task-specific paths "
                    f"[sources=legacy_test_file_addition]"
                )
            return TaskEvidenceResolution(
                task_id=task_id,
                commit_sha=candidate_sha,
                source="legacy_test_file_addition",
                evidence_paths=evidence_paths,
                legacy_bundle=True,
            )
        if len(candidate_shas) > 1:
            raise EpicPipelineError(f"{task_id}: ambiguous legacy task evidence found [sources=legacy_test_file_addition]")
        return None

    def _legacy_addition_candidates(
        self,
        snapshot: TaskSnapshot,
        history_by_sha: dict[str, str],
        commit_files_cache: dict[str, tuple[str, ...]],
    ) -> set[str]:
        candidate_sets: list[set[str]] = []
        declared_files = [*snapshot.test_files, *snapshot.implementation_files]
        existing_test_files = [path for path in snapshot.test_files if (self.root / path).is_file()]
        existing_implementation_files = [path for path in snapshot.implementation_files if (self.root / path).is_file()]
        search_files = existing_test_files or existing_implementation_files
        for path in search_files:
            shas = set(self.repository.find_commits_adding_path(path, ref="HEAD"))
            if shas:
                candidate_sets.append(shas)
        if not candidate_sets:
            return set()
        candidates = set.intersection(*candidate_sets) if candidate_sets else set()
        valid: set[str] = set()
        for sha in candidates:
            files = self._commit_files(sha, commit_files_cache)
            if any(_path_allowed(file, declared_files) for file in files):
                subject = history_by_sha.get(sha, "")
                if EPIC_MAINTENANCE_SUBJECT_PATTERN.search(subject):
                    continue
                valid.add(sha)
        return valid

    def _commit_evidence_paths(
        self,
        snapshot: TaskSnapshot,
        commit_sha: str,
        commit_files_cache: dict[str, tuple[str, ...]],
    ) -> tuple[str, ...]:
        commit_files = self._commit_files(commit_sha, commit_files_cache)
        declared_files = [*snapshot.implementation_files, *snapshot.test_files]
        return tuple(path for path in commit_files if _path_allowed(path, declared_files))

    def _commit_files(self, commit_sha: str, cache: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
        if commit_sha not in cache:
            cache[commit_sha] = self.repository.list_commit_files(commit_sha)
        return cache[commit_sha]

    def _commit_links_task(
        self,
        task_id: str,
        snapshot: TaskSnapshot,
        commit_sha: str,
        history_by_sha: dict[str, str],
        commit_files_cache: dict[str, tuple[str, ...]],
    ) -> bool:
        subject = history_by_sha.get(commit_sha, "")
        if EPIC_MAINTENANCE_SUBJECT_PATTERN.search(subject):
            return False
        if f"({task_id})" in subject:
            return True
        return bool(self._commit_evidence_paths(snapshot, commit_sha, commit_files_cache))

    def _epic_activation_commit_sha(self, epic_id: str, history_by_sha: dict[str, str]) -> str | None:
        pattern = re.compile(rf"^(feat|fix|test|chore|refactor)\({re.escape(epic_id)}\): activate epic$")
        candidates = [sha for sha, subject in history_by_sha.items() if pattern.match(subject.strip())]
        if not candidates:
            return None
        if len(candidates) > 1:
            raise EpicPipelineError(f"{epic_id}: multiple activation commits found")
        return candidates[0]

    def _epic_tasks_from_tasks_file(self, epic_id: str) -> tuple[str, ...]:
        task_ids: list[str] = []
        for task_id, _start_line, lines in task_consistency._iter_task_blocks(self.tasks_file):
            epic = task_consistency._field_value(lines, "Epic:")
            if epic is None or epic[1].strip() != epic_id:
                continue
            task_ids.append(task_id)
        return tuple(task_ids)

    def _write_review_receipt(
        self,
        epic_manifest: dict[str, Any],
        required_check_results: Sequence[CommandResult],
    ) -> Path:
        expected_commands = self._required_check_commands(epic_manifest)
        if len(required_check_results) != len(expected_commands):
            raise EpicPipelineError(
                "required checks results count does not match the epic manifest: "
                f"declared={len(expected_commands)} actual={len(required_check_results)}"
            )
        payload = [
            {
                "command": expected_command,
                "executed_command": " ".join(result.command),
                "exit_code": result.exit_code if result.exit_code is not None else 0,
            }
            for expected_command, result in zip(expected_commands, required_check_results)
        ]
        return self.review_receipt_writer(
            epic_id=str(epic_manifest.get("id") or ""),
            milestone_id=str(epic_manifest.get("milestone") or ""),
            branch=str(epic_manifest.get("branch") or ""),
            base_branch=str(epic_manifest.get("base_branch") or ""),
            verdict="PASS",
            safe_to_create_pr=True,
            required_checks=payload,
            head_sha=self.repository.head_sha(),
            base_sha=self._run_git_rev_parse(str(epic_manifest.get("base_branch") or "")),
        )

    def _write_validation_receipt(
        self,
        epic_manifest: dict[str, Any],
        required_check_results: Sequence[CommandResult],
    ) -> Path:
        self.repository.require_clean_tree()
        current_status = self.repository.status()
        python_executable = self._resolve_agent_python()
        expected_commands = self._required_check_commands(epic_manifest)
        checks: list[dict[str, Any]] = []
        for expected_command, result in zip(expected_commands, required_check_results):
            checks.append(
                {
                    "name": self._validation_check_name(expected_command, result.command),
                    "command": list(result.command),
                    "status": result.status,
                    "exit_code": 0 if result.exit_code is None else result.exit_code,
                    "duration_ms": result.duration_ms,
                }
            )
        path = validation_receipt.write_validation_receipt(
            head_sha=current_status.head_sha,
            branch=current_status.branch,
            python_executable=python_executable,
            python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            status="PASS",
            checks=checks,
            root=self.root,
        )
        errors = validation_receipt.validate_receipt_for_head(
            path,
            current_head_sha=current_status.head_sha,
            current_branch=current_status.branch,
            repo_clean=current_status.clean,
            current_python_version=(sys.version_info.major, sys.version_info.minor),
        )
        if errors:
            raise EpicPipelineError("; ".join(errors))
        return path

    def _refresh_task_baselines_after_head_change(
        self,
        epic_id: str,
        epic_manifest: dict[str, Any],
        *,
        old_head_sha: str,
        new_head_sha: str,
    ) -> tuple[str, ...]:
        if not old_head_sha or old_head_sha == new_head_sha:
            return ()
        refreshed: list[str] = []
        reason = f"epic branch head changed from {old_head_sha} to {new_head_sha}"
        for task_id in self._epic_task_ids(epic_manifest):
            try:
                snapshot = _load_task_snapshot(task_id, self.tasks_file)
            except Exception:
                continue
            state_record = load_task_state(task_id, root=self.root)
            if state_record is None:
                continue
            receipt_record = load_task_receipt(task_id, root=self.root)
            if (
                snapshot.checkbox.upper() == "X"
                and state_record.state == TaskLifecycleState.COMMITTED
                and receipt_record is not None
                and receipt_record.state == TaskLifecycleState.COMMITTED
            ):
                continue
            archive_task_attempt(task_id, self.root, reason=reason, restore_baseline=False)
            refreshed_state = replace(
                state_record,
                state=TaskLifecycleState.PENDING,
                updated_at=_timestamp(),
                branch=self.repository.status().branch,
                head_sha=new_head_sha,
                baseline_path="",
                baseline_branch="",
                baseline_head_sha="",
                reason=reason,
            )
            save_task_state(refreshed_state, root=self.root)
            refreshed.append(task_id)
        return tuple(refreshed)

    def _validate_review_receipt(self, epic_id: str, epic_manifest: dict[str, Any], receipt_path: Path) -> list[str]:
        return self.review_receipt_validator(
            receipt_path,
            epic_id=epic_id,
            milestone_id=str(epic_manifest.get("milestone") or ""),
            branch=str(epic_manifest.get("branch") or ""),
            base_branch=str(epic_manifest.get("base_branch") or ""),
            head_sha=self.repository.head_sha(),
            base_sha=self._run_git_rev_parse(str(epic_manifest.get("base_branch") or "")),
            expected_required_commands=[command for command in (epic_manifest.get("required_checks") or []) if isinstance(command, str) and command.strip()],
        )

    def _validation_check_name(self, expected_command: str, argv: Sequence[str]) -> str:
        if self._is_pytest_full_command(argv):
            return "pytest_full"
        if self._is_diff_check_command(argv):
            return "git_diff_check"
        normalized = re.sub(r"[^A-Za-z0-9]+", "_", expected_command.strip()).strip("_")
        return normalized or "required_check"

    def _is_pytest_full_command(self, argv: Sequence[str]) -> bool:
        parts = list(argv)
        if len(parts) < 3:
            return False
        return Path(parts[0]).name.lower() in {"python", "python.exe", "python3", "python3.exe"} and parts[1:3] == ["-m", "pytest"]

    def _preflight_environment(
        self,
        run: AutopilotRun,
        epic_manifest: dict[str, Any],
        *,
        run_mode: RunMode,
        cancel_event: Any | None,
    ) -> GitHubAuthResult | None:
        self._require_not_cancelled(cancel_event)
        status = self.repository.status()
        if not status.head_sha:
            raise EpicPipelineError("repository has no HEAD")
        if not status.branch:
            raise EpicPipelineError("repository branch is not available")
        if run.branch_name and status.branch != run.branch_name:
            raise EpicPipelineError(f"repository branch {status.branch!r} does not match {run.branch_name!r}")
        if not epic_manifest.get("branch"):
            raise EpicPipelineError("epic manifest is missing branch")

        auth: GitHubAuthResult | None = None
        if run_mode is RunMode.FULL:
            auth = self.github.validate_auth(timeout_seconds=self.config.command_timeout_seconds)
            if not auth.available or not auth.authenticated:
                raise EpicPipelineError(auth.reason or "gh authentication failed")
            self.repository.validate_remote(
                "origin",
                timeout_seconds=self.config.command_timeout_seconds,
                probe_timeout_seconds=min(self.config.command_timeout_seconds, 20),
            )

        python_executable = self._resolve_agent_python()
        python_probe = self._run(
            [python_executable, "--version"],
            cwd=self.root,
            timeout_seconds=self.config.command_timeout_seconds,
            heartbeat_seconds=0,
        )
        if python_probe.status != "PASS":
            raise EpicPipelineError(_process_result_detail(python_probe) or "agent.python cannot be executed")

        codex = CodexAdapter(self.root, process_runner_fn=self._run).detect_availability(timeout_seconds=self.config.command_timeout_seconds)
        if not codex.has_cli:
            raise EpicPipelineError(codex.reason or "codex CLI is missing")
        if not codex.supports_non_interactive:
            raise EpicPipelineError(codex.reason or "codex exec is unavailable")
        return auth

    def _finalize_run(
        self,
        run: AutopilotRun,
        *,
        status: RunStatus,
        epic_id: str | None,
        branch_name: str | None,
        task_results: Sequence[TaskResult],
        command_results: Sequence[CommandResult],
        pull_request: PullRequestInfo | None,
        last_error: str | None = None,
    ) -> AutopilotRun:
        return replace(
            run,
            status=status,
            updated_at=_timestamp(),
            epic_id=epic_id,
            branch_name=branch_name,
            current_task_id=task_results[-1].task_id if task_results else run.current_task_id,
            task_results=tuple(task_results),
            command_results=tuple(command_results),
            pull_request=pull_request,
            last_error=last_error,
        )

    def _finalize_failure(
        self,
        run: AutopilotRun,
        *,
        epic_id: str | None,
        branch_name: str | None,
        task_ids: Sequence[str],
        task_results: Sequence[TaskResult],
        command_results: Sequence[CommandResult],
        reason: str,
        activation_commit_sha: str | None,
    ) -> EpicPipelineResult:
        finalized_run = self._finalize_run(
            run,
            status=RunStatus.FAILED,
            epic_id=epic_id,
            branch_name=branch_name,
            task_results=task_results,
            command_results=command_results,
            pull_request=run.pull_request,
            last_error=reason,
        )
        save_run_state(finalized_run, root=self.root)
        return EpicPipelineResult(
            status=RunStatus.FAILED,
            run=finalized_run,
            epic_id=epic_id or "",
            branch_name=branch_name or "",
            task_ids=tuple(task_ids),
            task_results=tuple(task_results),
            command_results=tuple(command_results),
            activation_commit_sha=activation_commit_sha,
            reason=reason,
        )

    def _finalize_blocked(
        self,
        run: AutopilotRun,
        *,
        epic_id: str | None,
        branch_name: str | None,
        task_ids: Sequence[str],
        task_results: Sequence[TaskResult],
        command_results: Sequence[CommandResult],
        reason: str,
        activation_commit_sha: str | None,
    ) -> EpicPipelineResult:
        finalized_run = self._finalize_run(
            run,
            status=RunStatus.BLOCKED,
            epic_id=epic_id,
            branch_name=branch_name,
            task_results=task_results,
            command_results=command_results,
            pull_request=run.pull_request,
            last_error=reason,
        )
        save_run_state(finalized_run, root=self.root)
        return EpicPipelineResult(
            status=RunStatus.BLOCKED,
            run=finalized_run,
            epic_id=epic_id or "",
            branch_name=branch_name or "",
            task_ids=tuple(task_ids),
            task_results=tuple(task_results),
            command_results=tuple(command_results),
            activation_commit_sha=activation_commit_sha,
            reason=reason,
        )

    def _finalize_paused(
        self,
        run: AutopilotRun,
        *,
        epic_id: str | None,
        branch_name: str | None,
        task_ids: Sequence[str],
        task_results: Sequence[TaskResult],
        command_results: Sequence[CommandResult],
        reason: str,
        activation_commit_sha: str | None,
        pull_request: PullRequestInfo | None = None,
        implementation_pull_request: PullRequestInfo | None = None,
        closure_pull_request: PullRequestInfo | None = None,
    ) -> EpicPipelineResult:
        finalized_run = self._finalize_run(
            run,
            status=RunStatus.PAUSED,
            epic_id=epic_id,
            branch_name=branch_name,
            task_results=task_results,
            command_results=command_results,
            pull_request=pull_request or run.pull_request,
            last_error=reason,
        )
        finalized_run = replace(
            finalized_run,
            implementation_pull_request=implementation_pull_request or run.implementation_pull_request,
            closure_pull_request=closure_pull_request or run.closure_pull_request,
        )
        save_run_state(finalized_run, root=self.root)
        return EpicPipelineResult(
            status=RunStatus.PAUSED,
            run=finalized_run,
            epic_id=epic_id or "",
            branch_name=branch_name or "",
            task_ids=tuple(task_ids),
            task_results=tuple(task_results),
            command_results=tuple(command_results),
            activation_commit_sha=activation_commit_sha,
            reason=reason,
            pull_request=finalized_run.pull_request,
            implementation_pull_request=finalized_run.implementation_pull_request,
            closure_pull_request=finalized_run.closure_pull_request,
        )

    def _auth_to_command_result(self, auth: GitHubAuthResult) -> tuple[CommandResult, ...]:
        return (
            CommandResult(
                command=auth.command,
                status="PASS" if auth.available and auth.authenticated else "FAIL",
                exit_code=auth.exit_code,
                duration_ms=0,
                timed_out=False,
                stdout_lines=auth.stdout_lines,
                stderr_lines=auth.stderr_lines,
                output_truncated=False,
            ),
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

    def _resolve_agent_python(self) -> str:
        result = self._run(
            ["git", "config", "--local", "--get", "agent.python"],
            cwd=self.root,
            timeout_seconds=self.config.command_timeout_seconds,
            heartbeat_seconds=0,
        )
        if result.status != "PASS" or not result.stdout_lines:
            raise EpicPipelineError("agent.python is not configured")
        python_executable = result.stdout_lines[0].strip()
        if not python_executable:
            raise EpicPipelineError("agent.python is empty")
        return python_executable

    def _write_push_result(self, run: AutopilotRun, result: process_runner.ProcessResult) -> Path:
        path = self._push_result_path(run.run_id)
        payload = {
            "command": [str(part) for part in result.command],
            "status": result.status,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_ms": result.duration_ms,
            "stdout_lines": [process_runner.redact_sensitive_text(line) for line in result.stdout_lines],
            "stderr_lines": [process_runner.redact_sensitive_text(line) for line in result.stderr_lines],
            "head_sha": self.repository.head_sha(),
            "branch": run.branch_name,
            "timestamp": _timestamp(),
        }
        _write_atomic_json(path, payload)
        return path

    def _push_result_path(self, run_id: str) -> Path:
        return self.root / ".specify" / "runtime" / "runs" / run_id / "push-result.json"

    def _run_git_rev_parse(self, ref: str) -> str:
        result = self._run(["git", "rev-parse", ref], cwd=self.root, timeout_seconds=20, heartbeat_seconds=0)
        if result.status != "PASS" or not result.stdout_lines:
            raise EpicPipelineError(f"cannot resolve Git SHA for {ref!r}")
        return result.stdout_lines[0].strip()

    def _safe_command(self, command: str, python_executable: str) -> list[str]:
        normalized = command.strip().strip("`").strip()
        if not normalized:
            raise EpicPipelineError("required command is empty")
        argv = re.split(r"\s+", normalized)
        if argv and Path(argv[0]).name.lower() in {"python", "python.exe", "python3", "python3.exe"}:
            return [python_executable, *argv[1:]]
        return argv

    def _is_diff_check_command(self, argv: Sequence[str]) -> bool:
        parts = list(argv)
        return parts[:3] == ["git", "diff", "--check"] or parts[:4] == ["git", "--no-pager", "diff", "--check"]

    def _pr_title(self, epic_id: str, epic_manifest: dict[str, Any]) -> str:
        title = str(epic_manifest.get("title") or epic_id).strip()
        return f"{epic_id}: {title}"

    def _pr_body(
        self,
        epic_id: str,
        epic_manifest: dict[str, Any],
        task_ids: Sequence[str],
        review_receipt_path: Path,
    ) -> str:
        lines = [
            f"Epic: {epic_id}",
            f"Milestone: {epic_manifest.get('milestone')}",
            f"Tasks: {', '.join(task_ids) if task_ids else 'none'}",
            f"Review receipt: {review_receipt_path.as_posix()}",
            "Draft PR created by local autopilot.",
            "No merge or deployment is performed automatically.",
        ]
        return "\n".join(lines)

    def _epic_task_ids(self, epic_manifest: dict[str, Any]) -> tuple[str, ...]:
        return tuple(
            str(task_id).strip()
            for task_id in (epic_manifest.get("tasks") or [])
            if isinstance(task_id, str) and str(task_id).strip()
        )

    def _closure_branch_name(self, epic_id: str) -> str:
        return f"chore/close-{epic_id}"

    def _closure_pr_title(self, epic_id: str) -> str:
        return f"chore({epic_id}): mark epic completed"

    def _closure_pr_body(
        self,
        epic_id: str,
        epic_manifest: dict[str, Any],
        implementation_pr: PullRequestInfo | None,
        merge_sha: str,
        closure_branch_name: str,
    ) -> str:
        implementation_line = "Implementation PR: unavailable"
        if implementation_pr is not None:
            implementation_line = f"Implementation PR: #{implementation_pr.number} {implementation_pr.url}"
        lines = [
            f"Epic: {epic_id}",
            f"Milestone: {epic_manifest.get('milestone')}",
            implementation_line,
            f"Implementation merge SHA: {merge_sha}",
            f"Closure branch: {closure_branch_name}",
            "This PR marks the epic completed after the implementation PR merged.",
            "Manual merge is required.",
        ]
        return "\n".join(lines)

    def _rewrite_manifest_status(self, path: Path, new_status: str) -> None:
        lines = path.read_text(encoding="utf-8").splitlines()
        updated_lines: list[str] = []
        replaced = False
        for line in lines:
            if not replaced and line.startswith("status: "):
                updated_lines.append(f"status: {new_status}")
                replaced = True
            else:
                updated_lines.append(line)
        if not replaced:
            raise EpicPipelineError(f"{path.name}: missing status field")
        path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")

    def _clear_active_epic_marker_if_matches(self, epic_id: str) -> None:
        if not self.active_epic_file.is_file():
            return
        try:
            current = self.active_epic_file.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if current == epic_id:
            try:
                self.active_epic_file.unlink()
            except FileNotFoundError:
                pass

    def _validate_epic_closure_prereqs(
        self,
        epic_id: str,
        epic_manifest: dict[str, Any],
        master_head_sha: str,
    ) -> list[str]:
        errors: list[str] = []
        self._require_not_cancelled(None)
        try:
            self.repository.require_clean_tree()
        except Exception as exc:
            errors.append(str(exc))
        if not all_epic_tasks_complete(epic_id, tasks_file=self.tasks_file, directory=self.workstreams_dir):
            errors.append("epic tasks are not all complete")
        task_ids = self._epic_task_ids(epic_manifest)
        if not task_ids:
            errors.append("epic manifest does not declare tasks")
            return errors
        for task_id in task_ids:
            try:
                snapshot = _load_task_snapshot(task_id, self.tasks_file)
            except Exception as exc:
                errors.append(f"{task_id}: {exc}")
                continue
            if snapshot.checkbox.upper() != "X":
                errors.append(f"{task_id}: tasks.md checkbox is [{snapshot.checkbox}]")
            receipt = load_task_receipt(task_id, root=self.root)
            if receipt is None or not receipt.commit_sha:
                errors.append(f"{task_id}: missing task receipt commit SHA")
                continue
            if not self.repository.is_ancestor(receipt.commit_sha, master_head_sha):
                errors.append(
                    f"{task_id}: commit {receipt.commit_sha} is not an ancestor of origin/master {master_head_sha}"
                )
        return errors

    def _reconcile_active_epic_state(
        self,
        current_run: AutopilotRun,
        *,
        epic_id: str,
        epic_manifest: dict[str, Any],
        base_branch: str,
        branch_name: str,
        task_results: Sequence[TaskResult],
        command_results: Sequence[CommandResult],
        activation_commit_sha: str | None,
        preflight_auth: GitHubAuthResult | None,
        cancel_event: Any | None,
    ) -> EpicPipelineResult | None:
        implementation_pr = self.github.find_pr(
            base_branch,
            branch_name,
            timeout_seconds=self.config.command_timeout_seconds,
        )
        task_id_tuple = self._epic_task_ids(epic_manifest)
        if implementation_pr is None:
            return None
        if not implementation_pr.merged:
            waiting_run = replace(
                current_run,
                status=RunStatus.WAITING_FOR_MERGE,
                updated_at=_timestamp(),
                epic_id=epic_id,
                branch_name=branch_name,
                pull_request=implementation_pr,
                implementation_pull_request=implementation_pr,
                closure_pull_request=None,
            )
            save_run_state(waiting_run, root=self.root)
            return EpicPipelineResult(
                status=RunStatus.WAITING_FOR_MERGE,
                run=waiting_run,
                epic_id=epic_id,
                branch_name=branch_name,
                task_ids=task_id_tuple,
                task_results=tuple(task_results) or tuple(current_run.task_results),
                command_results=tuple(command_results) or tuple(current_run.command_results),
                pull_request=implementation_pr,
                implementation_pull_request=implementation_pr,
                activation_commit_sha=activation_commit_sha,
            )

        if self.config.closure_mode != "pull_request":
            raise EpicPipelineError(f"unsupported closure_mode {self.config.closure_mode!r}")
        master_head_sha = self.repository.head_sha()
        closure_errors = self._validate_epic_closure_prereqs(epic_id, epic_manifest, master_head_sha)
        if closure_errors:
            return self._finalize_failure(
                current_run,
                epic_id=epic_id,
                branch_name=branch_name,
                task_ids=task_id_tuple,
                task_results=task_results,
                command_results=command_results,
                reason="; ".join(closure_errors),
                activation_commit_sha=activation_commit_sha,
            )
        return self._create_epic_closure(
            current_run,
            epic_id=epic_id,
            epic_manifest=epic_manifest,
            base_branch=base_branch,
            implementation_pr=implementation_pr,
            task_ids=task_id_tuple,
            task_results=task_results,
            command_results=command_results,
            cancel_event=cancel_event,
            master_head_sha=master_head_sha,
            activation_commit_sha=activation_commit_sha,
        )

    def _finalize_completed_epic(
        self,
        current_run: AutopilotRun,
        *,
        epic_id: str,
        base_branch: str,
        branch_name: str,
        epic_manifest: dict[str, Any],
        task_results: Sequence[TaskResult],
        command_results: Sequence[CommandResult],
        activation_commit_sha: str | None,
        preflight_auth: GitHubAuthResult | None,
    ) -> EpicPipelineResult:
        closure_branch = self._closure_branch_name(epic_id)
        closure_pr = self.github.find_pr(
            base_branch,
            closure_branch,
            timeout_seconds=self.config.command_timeout_seconds,
        )
        task_id_tuple = self._epic_task_ids(epic_manifest)
        if closure_pr is not None and not closure_pr.merged:
            waiting_run = replace(
                current_run,
                status=RunStatus.WAITING_FOR_MERGE,
                updated_at=_timestamp(),
                epic_id=epic_id,
                branch_name=closure_branch,
                pull_request=closure_pr,
                closure_pull_request=closure_pr,
            )
            save_run_state(waiting_run, root=self.root)
            return EpicPipelineResult(
                status=RunStatus.WAITING_FOR_MERGE,
                run=waiting_run,
                epic_id=epic_id,
                branch_name=closure_branch,
                task_ids=task_id_tuple,
                task_results=tuple(task_results) or tuple(current_run.task_results),
                command_results=tuple(command_results) or tuple(current_run.command_results),
                pull_request=closure_pr,
                closure_pull_request=closure_pr,
                activation_commit_sha=activation_commit_sha,
            )

        if closure_pr is not None and closure_pr.merged:
            current_master_sha = self.repository.head_sha()
            closure_errors = self._validate_epic_closure_prereqs(epic_id, epic_manifest, current_master_sha)
            if closure_errors:
                return self._finalize_failure(
                    current_run,
                    epic_id=epic_id,
                    branch_name=closure_branch,
                    task_ids=task_id_tuple,
                    task_results=task_results,
                    command_results=command_results,
                    reason="; ".join(closure_errors),
                    activation_commit_sha=activation_commit_sha,
                )
            archive_completed_epic_runtime(epic_id, self.root, reason="epic completed")
            self._clear_active_epic_marker_if_matches(epic_id)
            completed_run = replace(
                current_run,
                status=RunStatus.COMPLETED,
                updated_at=_timestamp(),
                epic_id=epic_id,
                branch_name=closure_branch,
                pull_request=closure_pr,
                closure_pull_request=closure_pr,
            )
            save_run_state(completed_run, root=self.root)
            return EpicPipelineResult(
                status=RunStatus.COMPLETED,
                run=completed_run,
                epic_id=epic_id,
                branch_name=closure_branch,
                task_ids=task_id_tuple,
                task_results=tuple(task_results) or tuple(current_run.task_results),
                command_results=tuple(command_results) or tuple(current_run.command_results),
                pull_request=closure_pr,
                closure_pull_request=closure_pr,
                activation_commit_sha=activation_commit_sha,
            )

        self._clear_active_epic_marker_if_matches(epic_id)
        archive_completed_epic_runtime(epic_id, self.root, reason="epic completed")
        completed_run = replace(
            current_run,
            status=RunStatus.COMPLETED,
            updated_at=_timestamp(),
            epic_id=epic_id,
            branch_name=branch_name,
            last_error=None,
        )
        save_run_state(completed_run, root=self.root)
        return EpicPipelineResult(
            status=RunStatus.COMPLETED,
            run=completed_run,
            epic_id=epic_id,
            branch_name=branch_name,
            task_ids=task_id_tuple,
            task_results=tuple(task_results) or tuple(current_run.task_results),
            command_results=tuple(command_results) or tuple(current_run.command_results),
            activation_commit_sha=activation_commit_sha,
        )

    def _create_epic_closure(
        self,
        current_run: AutopilotRun,
        *,
        epic_id: str,
        epic_manifest: dict[str, Any],
        base_branch: str,
        implementation_pr: PullRequestInfo,
        task_ids: Sequence[str],
        task_results: Sequence[TaskResult],
        command_results: Sequence[CommandResult],
        cancel_event: Any | None,
        master_head_sha: str,
        activation_commit_sha: str | None,
    ) -> EpicPipelineResult:
        closure_branch = self._closure_branch_name(epic_id)
        manifest_path = self._epic_manifest_path(epic_id)
        current_run = replace(
            current_run,
            status=RunStatus.CLOSING,
            updated_at=_timestamp(),
            epic_id=epic_id,
            branch_name=closure_branch,
            pull_request=implementation_pr,
            implementation_pull_request=implementation_pr,
            closure_pull_request=None,
        )
        save_run_state(current_run, root=self.root)

        self.repository.create_branch(closure_branch, base_branch=base_branch)
        self._rewrite_manifest_status(manifest_path, "completed")
        relative_path = manifest_path.relative_to(self.root).as_posix()
        self.repository.stage_allowlist([relative_path])
        cached_diff = self.repository.diff_check(cached=True)
        command_results = [*command_results, self._command_result_from_process(cached_diff)]
        if cached_diff.status != "PASS":
            return self._finalize_failure(
                current_run,
                epic_id=epic_id,
                branch_name=closure_branch,
                task_ids=task_ids,
                task_results=task_results,
                command_results=command_results,
                reason="cached diff check failed",
                activation_commit_sha=activation_commit_sha,
            )

        python_executable = self._resolve_agent_python()
        pre_commit = self._run(
            [
                python_executable,
                "-m",
                "backend.app.tooling.git_hook_runner",
                "pre-commit",
                "--json",
                "--no-heartbeat",
            ],
            cwd=self.root,
            timeout_seconds=self.config.command_timeout_seconds,
            heartbeat_seconds=0,
            cancel_event=cancel_event,
        )
        command_results.append(self._command_result_from_process(pre_commit))
        if pre_commit.status != "PASS":
            return self._finalize_failure(
                current_run,
                epic_id=epic_id,
                branch_name=closure_branch,
                task_ids=task_ids,
                task_results=task_results,
                command_results=command_results,
                reason=_process_result_detail(pre_commit) or "pre-commit hook failed",
                activation_commit_sha=activation_commit_sha,
            )

        commit_result = self.repository.commit(self._closure_commit_message(epic_id))
        command_results.append(self._command_result_from_process(commit_result))
        if commit_result.status != "PASS":
            return self._finalize_failure(
                current_run,
                epic_id=epic_id,
                branch_name=closure_branch,
                task_ids=task_ids,
                task_results=task_results,
                command_results=command_results,
                reason=_process_result_detail(commit_result) or "epic closure commit failed",
                activation_commit_sha=activation_commit_sha,
            )

        push_result = self.repository.push(closure_branch, timeout_seconds=self.config.push_timeout_seconds)
        self._write_push_result(current_run, push_result)
        command_results.append(self._command_result_from_process(push_result))
        if push_result.status != "PASS":
            return self._finalize_failure(
                current_run,
                epic_id=epic_id,
                branch_name=closure_branch,
                task_ids=task_ids,
                task_results=task_results,
                command_results=command_results,
                reason=build_push_failure_reason(push_result, timeout_seconds=self.config.push_timeout_seconds),
                activation_commit_sha=activation_commit_sha,
            )

        closure_pr = self.github.create_pr(
            base_branch,
            closure_branch,
            self._closure_pr_title(epic_id),
            self._closure_pr_body(
                epic_id,
                epic_manifest,
                implementation_pr,
                master_head_sha,
                closure_branch,
            ),
            draft=False,
            timeout_seconds=self.config.command_timeout_seconds,
        )
        finalized_run = self._finalize_run(
            replace(
                current_run,
                pull_request=closure_pr,
                closure_pull_request=closure_pr,
            ),
            status=RunStatus.WAITING_FOR_MERGE,
            epic_id=epic_id,
            branch_name=closure_branch,
            task_results=task_results,
            command_results=command_results,
            pull_request=closure_pr,
        )
        save_run_state(finalized_run, root=self.root)
        return EpicPipelineResult(
            status=RunStatus.WAITING_FOR_MERGE,
            run=finalized_run,
            epic_id=epic_id,
            branch_name=closure_branch,
            task_ids=tuple(task_ids),
            task_results=tuple(task_results),
            command_results=tuple(command_results),
            pull_request=closure_pr,
            implementation_pull_request=implementation_pr,
            closure_pull_request=closure_pr,
            activation_commit_sha=activation_commit_sha,
        )

    def _closure_commit_message(self, epic_id: str) -> str:
        return f"chore({epic_id}): mark epic completed"

    def _require_not_cancelled(self, cancel_event: Any | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise KeyboardInterrupt()

    def retry_push(
        self,
        run: AutopilotRun,
        *,
        cancel_event: Any | None = None,
    ) -> EpicPipelineResult:
        if run.request.run_mode is not RunMode.FULL:
            raise EpicPipelineError("retry push is only available for full runs")
        if run.status is not RunStatus.FAILED:
            raise EpicPipelineError("retry push requires a failed run")
        if not run.epic_id or not run.branch_name:
            raise EpicPipelineError("retry push requires epic and branch metadata")
        if not run.task_results:
            raise EpicPipelineError("retry push requires completed task results")

        epic_id = self._epic_id_for(run)
        epic_manifest = get_epic(epic_id, self.workstreams_dir)
        if not all_epic_tasks_complete(epic_id, tasks_file=self.tasks_file, directory=self.workstreams_dir):
            raise EpicPipelineError("epic tasks are not all complete")

        current_status = self.repository.require_clean_tree()
        if current_status.branch != run.branch_name:
            raise EpicPipelineError(f"repository branch {current_status.branch!r} does not match {run.branch_name!r}")

        current_head = self.repository.head_sha()
        expected_head = next((task.commit_sha for task in reversed(run.task_results) if task.commit_sha), None)
        if not expected_head:
            raise EpicPipelineError("retry push requires a validated HEAD")
        if current_head != expected_head:
            raise EpicPipelineError(f"HEAD changed from {expected_head} to {current_head}")

        receipt_path = validation_receipt.validation_receipt_path(current_head, self.root)
        receipt_errors = validation_receipt.validate_receipt_for_head(
            receipt_path,
            current_head_sha=current_head,
            current_branch=current_status.branch,
            repo_clean=current_status.clean,
            current_python_version=(sys.version_info.major, sys.version_info.minor),
        )
        if receipt_errors:
            raise EpicPipelineError("; ".join(receipt_errors))

        self.repository.validate_remote(
            "origin",
            timeout_seconds=self.config.command_timeout_seconds,
            probe_timeout_seconds=min(self.config.command_timeout_seconds, 20),
        )
        auth = self.github.validate_auth(timeout_seconds=self.config.command_timeout_seconds)
        if not auth.available or not auth.authenticated:
            raise EpicPipelineError(auth.reason or "gh authentication failed")

        task_ids = [task.task_id for task in run.task_results]
        task_results = list(run.task_results)
        command_results = list(run.command_results)
        current_run = replace(
            run,
            status=RunStatus.PUSHING,
            updated_at=_timestamp(),
            epic_id=epic_id,
            branch_name=run.branch_name,
        )
        save_run_state(current_run, root=self.root)

        push_result = self.repository.push(run.branch_name, timeout_seconds=self.config.push_timeout_seconds)
        self._write_push_result(current_run, push_result)
        command_results.append(self._command_result_from_process(push_result))
        if push_result.status != "PASS":
            return self._finalize_failure(
                current_run,
                epic_id=epic_id,
                branch_name=run.branch_name,
                task_ids=tuple(task_ids),
                task_results=task_results,
                command_results=command_results,
                reason=build_push_failure_reason(push_result, timeout_seconds=self.config.push_timeout_seconds),
                activation_commit_sha=None,
            )

        current_run = replace(current_run, status=RunStatus.PR_CREATING, updated_at=_timestamp())
        save_run_state(current_run, root=self.root)

        pull_request = self.github.find_pr(
            self._base_branch_for(run),
            run.branch_name,
            timeout_seconds=self.config.command_timeout_seconds,
        )
        if pull_request is None:
            pull_request = self.github.create_draft_pr(
                self._base_branch_for(run),
                run.branch_name,
                self._pr_title(epic_id, epic_manifest),
                self._pr_body(epic_id, epic_manifest, task_ids, receipt_path),
                timeout_seconds=self.config.command_timeout_seconds,
            )

        finalized_run = self._finalize_run(
            current_run,
            status=RunStatus.WAITING_FOR_MERGE,
            epic_id=epic_id,
            branch_name=run.branch_name,
            task_results=task_results,
            command_results=command_results,
            pull_request=pull_request,
        )
        save_run_state(finalized_run, root=self.root)
        return EpicPipelineResult(
            status=RunStatus.WAITING_FOR_MERGE,
            run=finalized_run,
            epic_id=epic_id,
            branch_name=run.branch_name,
            task_ids=tuple(task_ids),
            task_results=tuple(task_results),
            command_results=tuple(command_results),
            review_receipt_path=str(receipt_path),
            pull_request=pull_request,
        )


def run_epic_pipeline(
    run: AutopilotRun,
    *,
    root: Path | str = ROOT,
    config: AutopilotConfig | None = None,
    repository: repository_module.Repository | None = None,
    github_adapter: GitHubAdapter | None = None,
    task_pipeline_factory: Callable[[Path, AutopilotConfig, Callable[..., process_runner.ProcessResult]], TaskPipeline] | None = None,
    process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
    review_receipt_writer: Callable[..., Path] = epic_review_receipt.write_review_receipt,
    review_receipt_validator: Callable[..., list[str]] = epic_review_receipt.validate_review_receipt_file,
    cancel_event: Any | None = None,
    human_authorized: bool | None = None,
) -> EpicPipelineResult:
    pipeline = EpicPipeline(
        root,
        config=config,
        repository=repository,
        task_pipeline_factory=task_pipeline_factory,
        github_adapter=github_adapter,
        process_runner_fn=process_runner_fn,
        review_receipt_writer=review_receipt_writer,
        review_receipt_validator=review_receipt_validator,
    )
    return pipeline.run_epic(run, human_authorized=human_authorized, cancel_event=cancel_event)


def retry_push_pipeline(
    run: AutopilotRun,
    *,
    root: Path | str = ROOT,
    config: AutopilotConfig | None = None,
    repository: repository_module.Repository | None = None,
    github_adapter: GitHubAdapter | None = None,
    process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
    cancel_event: Any | None = None,
) -> EpicPipelineResult:
    pipeline = EpicPipeline(
        root,
        config=config,
        repository=repository,
        github_adapter=github_adapter,
        process_runner_fn=process_runner_fn,
    )
    return pipeline.retry_push(run, cancel_event=cancel_event)


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = [
    "ACTIVE_EPIC_FILE",
    "EpicPipeline",
    "EpicPipelineError",
    "EpicPipelineResult",
    "TASKS_FILE",
    "WORKSTREAMS_DIR",
    "run_epic_pipeline",
    "retry_push_pipeline",
    "build_push_failure_reason",
]

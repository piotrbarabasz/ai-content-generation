"""Deterministic pipeline for an entire local epic."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from app.tooling import epic_review_receipt
from app.tooling import workstream_validation

from . import process_runner, repository as repository_module
from .codex_adapter import CodexAdapter
from .config import AutopilotConfig, DEFAULT_AUTOPILOT_CONFIG_PATH, load_autopilot_config
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

ROOT = Path(__file__).resolve().parents[4]
ACTIVE_EPIC_FILE = ROOT / ".specify" / "runtime" / "active-epic"
WORKSTREAMS_DIR = ROOT / ".specify" / "workstreams"
TASKS_FILE = ROOT / "specs" / "001-ai-content-studio" / "tasks.md"
EPIC_ID_PATTERN = re.compile(r"^E\d{3}$")


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
    activation_commit_sha: str | None = None
    reason: str | None = None


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
        status = RunStatus.PREFLIGHT
        try:
            self._require_not_cancelled(cancel_event)
            self.repository.require_clean_tree()
            self.repository.switch_to_master_and_pull(self._base_branch_for(run), "origin")

            epic_id = self._epic_id_for(run)
            epic_manifest = get_epic(epic_id, self.workstreams_dir)
            branch_name = str(epic_manifest.get("branch") or "")
            if not branch_name:
                raise EpicPipelineError(f"{epic_id} manifest is missing branch")
            dependency_errors = validate_dependencies(epic_id, self.workstreams_dir)
            if dependency_errors:
                raise EpicPipelineError("; ".join(dependency_errors))

            if str(epic_manifest.get("status") or "") == "planned":
                if human_authorized is None:
                    human_authorized = bool(run.request.human_authorized)
                if not human_authorized:
                    raise EpicPipelineError("human authorization is required to activate a planned epic")
                self.repository.create_branch(branch_name, base_branch=self._base_branch_for(run))
                updated_manifest = activate_epic_with_human_authorization(
                    epic_id,
                    human_authorized=True,
                    directory=self.workstreams_dir,
                )
                self._stage_and_commit_activation(epic_manifest_path=self._epic_manifest_path(epic_id), epic_id=epic_id)
                self._write_active_epic(epic_id)
                activation_commit_sha = self.repository.head_sha()
            else:
                self.repository.create_branch(branch_name, base_branch=self._base_branch_for(run))
                self._write_active_epic(epic_id)
                activation_commit_sha = None

            current_run = replace(
                run,
                status=RunStatus.ACTIVATING,
                updated_at=_timestamp(),
                epic_id=epic_id,
                branch_name=branch_name,
            )
            save_run_state(current_run, root=self.root)

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

            evidence_errors = self._verify_task_evidence(task_results)
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

            required_check_results = self._run_required_checks(epic_manifest, command_results, cancel_event=cancel_event)
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

            auth = self.github.validate_auth(timeout_seconds=self.config.command_timeout_seconds)
            command_results.extend(self._auth_to_command_result(auth))
            if not auth.available or not auth.authenticated:
                return self._finalize_failure(
                    current_run,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=tuple(command_results),
                    reason=auth.reason or "gh authentication failed",
                    activation_commit_sha=activation_commit_sha,
                )

            push_result = self.repository.push(branch_name)
            command_results.append(self._command_result_from_process(push_result))
            if push_result.status != "PASS":
                return self._finalize_failure(
                    current_run,
                    epic_id=epic_id,
                    branch_name=branch_name,
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=tuple(command_results),
                    reason="push failed",
                    activation_commit_sha=activation_commit_sha,
                )

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
                    run,
                    status=RunStatus.CANCELLED,
                    epic_id=run.epic_id,
                    branch_name=run.branch_name,
                    task_results=task_results,
                    command_results=command_results,
                    pull_request=run.pull_request,
                    last_error="cancelled",
                )
                save_run_state(finalized_run, root=self.root)
                return EpicPipelineResult(
                    status=RunStatus.CANCELLED,
                    run=finalized_run,
                    epic_id=run.epic_id or "",
                    branch_name=run.branch_name or "",
                    task_ids=tuple(task_ids),
                    task_results=tuple(task_results),
                    command_results=tuple(command_results),
                    reason="cancelled",
                )
            finalized_run = self._finalize_run(
                run,
                status=RunStatus.FAILED,
                epic_id=run.epic_id,
                branch_name=run.branch_name,
                task_results=task_results,
                command_results=command_results,
                pull_request=run.pull_request,
                last_error=str(exc),
            )
            save_run_state(finalized_run, root=self.root)
            return EpicPipelineResult(
                status=RunStatus.FAILED,
                run=finalized_run,
                epic_id=run.epic_id or "",
                branch_name=run.branch_name or "",
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

    def _run_required_checks(
        self,
        epic_manifest: dict[str, Any],
        command_results: Sequence[CommandResult],
        *,
        cancel_event: Any | None,
    ) -> tuple[CommandResult, ...]:
        results: list[CommandResult] = []
        required_commands = [command for command in (epic_manifest.get("required_checks") or []) if isinstance(command, str) and command.strip()]
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

    def _verify_task_evidence(self, task_results: Sequence[TaskResult]) -> list[str]:
        errors: list[str] = []
        if not task_results:
            return ["no task commits were recorded"]
        try:
            self.repository.require_clean_tree()
        except Exception as exc:
            errors.append(str(exc))
        commit_shas = [str(result.commit_sha or "").strip() for result in task_results]
        if any(not sha for sha in commit_shas):
            errors.append("one or more task commits are missing commit SHAs")
        if len(commit_shas) != len(set(commit_shas)):
            errors.append("task commit SHAs must be unique")
        current_head = self.repository.head_sha()
        if commit_shas and commit_shas[-1] != current_head:
            errors.append("current HEAD does not match the final task commit")
        return errors

    def _write_review_receipt(
        self,
        epic_manifest: dict[str, Any],
        required_check_results: Sequence[CommandResult],
    ) -> Path:
        payload = [
            {"command": " ".join(result.command), "exit_code": result.exit_code or 0}
            for result in required_check_results
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

    def _require_not_cancelled(self, cancel_event: Any | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise KeyboardInterrupt()


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
]

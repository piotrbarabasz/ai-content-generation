"""Deterministic pipeline for milestone-level local autopilot runs."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from app.tooling import epic_close_evidence
from app.tooling import workstream_validation

from . import process_runner, repository as repository_module
from .config import AutopilotConfig, DEFAULT_AUTOPILOT_CONFIG_PATH, load_autopilot_config
from .epic_pipeline import EpicPipeline, EpicPipelineResult
from .github_adapter import GitHubAdapter, GitHubAuthResult
from .models import AutopilotRequest, AutopilotRun, PullRequestInfo, RunMode, RunStatus
from .state_store import save_run_state
from .workstreams import all_epic_tasks_complete, get_epic, get_milestone, list_epics, next_ready_epic_for_milestone

ROOT = Path(__file__).resolve().parents[4]
ACTIVE_EPIC_FILE = ROOT / ".specify" / "runtime" / "active-epic"
WORKSTREAMS_DIR = ROOT / ".specify" / "workstreams"
TASKS_FILE = ROOT / "specs" / "001-ai-content-studio" / "tasks.md"
MILESTONE_ID_PATTERN = re.compile(r"^M\d{3}$")
EPIC_ID_PATTERN = re.compile(r"^E\d{3}$")
BOOKKEEPING_BRANCH_PREFIX = "bookkeeping"
ACTIVE_EPIC_BRANCH_PATTERN = re.compile(r"^epic/E\d{3}$")
EPIC_CLOSURE_BRANCH_PATTERN = re.compile(r"^bookkeeping/(M\d{3})/(E\d{3})$")
MILESTONE_CLOSURE_BRANCH_PATTERN = re.compile(r"^bookkeeping/(M\d{3})/close$")


@dataclass(frozen=True)
class MilestonePipelineResult:
    status: RunStatus
    run: AutopilotRun
    milestone_id: str
    epic_id: str | None = None
    branch_name: str | None = None
    pull_request: PullRequestInfo | None = None
    epic_result: EpicPipelineResult | None = None
    reason: str | None = None


class MilestonePipelineError(RuntimeError):
    pass


class MilestonePipeline:
    def __init__(
        self,
        root: Path | str = ROOT,
        *,
        config: AutopilotConfig | None = None,
        repository: repository_module.Repository | None = None,
        epic_pipeline_factory: Callable[[Path, AutopilotConfig, repository_module.Repository, GitHubAdapter, Callable[..., process_runner.ProcessResult]], EpicPipeline] | None = None,
        github_adapter: GitHubAdapter | None = None,
        process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
        config_path: Path | str = DEFAULT_AUTOPILOT_CONFIG_PATH,
        active_epic_file: Path | None = None,
        workstreams_dir: Path | None = None,
        tasks_file: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self._run = process_runner_fn
        self.config = config or load_autopilot_config(config_path)
        self.repository = repository or repository_module.Repository(self.root, process_runner_fn=process_runner_fn)
        self.github = github_adapter or GitHubAdapter(self.root, process_runner_fn=process_runner_fn)
        self.epic_pipeline_factory = epic_pipeline_factory or self._default_epic_pipeline_factory
        self.active_epic_file = active_epic_file or (self.root / ".specify" / "runtime" / "active-epic")
        self.workstreams_dir = workstreams_dir or (self.root / ".specify" / "workstreams")
        self.tasks_file = tasks_file or (self.root / "specs" / "001-ai-content-studio" / "tasks.md")

    def run_milestone(
        self,
        run: AutopilotRun,
        *,
        human_authorized: bool | None = None,
        cancel_event: Any | None = None,
    ) -> MilestonePipelineResult:
        try:
            self._require_not_cancelled(cancel_event)
            self.repository.require_clean_tree()
            self.repository.switch_to_master_and_pull("master", "origin")

            milestone_id = self._milestone_id_for(run)
            milestone = get_milestone(milestone_id, self.workstreams_dir)
            epic_id = run.epic_id if run.epic_id and EPIC_ID_PATTERN.fullmatch(run.epic_id) else None

            if run.status == RunStatus.WAITING_FOR_MERGE and run.pull_request is not None and epic_id is not None:
                return self._advance_waiting_run(
                    run,
                    milestone_id=milestone_id,
                    milestone=milestone,
                    cancel_event=cancel_event,
                )

            ready_epic_id = next_ready_epic_for_milestone(milestone_id, tasks_file=self.tasks_file, directory=self.workstreams_dir)
            if ready_epic_id is not None:
                return self._start_epic(
                    run,
                    milestone_id=milestone_id,
                    epic_id=ready_epic_id,
                    milestone=milestone,
                    human_authorized=human_authorized,
                    cancel_event=cancel_event,
                )

            if self._milestone_can_close(milestone_id):
                return self._create_milestone_closure(
                    run,
                    milestone_id=milestone_id,
                    milestone=milestone,
                    cancel_event=cancel_event,
                )

            raise MilestonePipelineError(f"no ready epic exists for milestone {milestone_id}")
        except (KeyboardInterrupt, MilestonePipelineError, RuntimeError, ValueError, FileNotFoundError, OSError) as exc:
            if isinstance(exc, KeyboardInterrupt):
                finalized = replace(
                    run,
                    status=RunStatus.CANCELLED,
                    updated_at=_timestamp(),
                    last_error="cancelled",
                )
                save_run_state(finalized, root=self.root)
                return MilestonePipelineResult(
                    status=RunStatus.CANCELLED,
                    run=finalized,
                    milestone_id=run.milestone_id or run.request.scope_id,
                    epic_id=run.epic_id,
                    branch_name=run.branch_name,
                    pull_request=run.pull_request,
                    reason="cancelled",
                )
            finalized = replace(
                run,
                status=RunStatus.FAILED,
                updated_at=_timestamp(),
                last_error=str(exc),
            )
            save_run_state(finalized, root=self.root)
            return MilestonePipelineResult(
                status=RunStatus.FAILED,
                run=finalized,
                milestone_id=run.milestone_id or run.request.scope_id,
                epic_id=run.epic_id,
                branch_name=run.branch_name,
                pull_request=run.pull_request,
                reason=str(exc),
            )

    def _default_epic_pipeline_factory(
        self,
        root: Path,
        config: AutopilotConfig,
        repository: repository_module.Repository,
        github_adapter: GitHubAdapter,
        process_runner_fn: Callable[..., process_runner.ProcessResult],
    ) -> EpicPipeline:
        return EpicPipeline(
            root,
            config=config,
            repository=repository,
            github_adapter=github_adapter,
            process_runner_fn=process_runner_fn,
            active_epic_file=self.active_epic_file,
            workstreams_dir=self.workstreams_dir,
            tasks_file=self.tasks_file,
        )

    def _start_epic(
        self,
        run: AutopilotRun,
        *,
        milestone_id: str,
        epic_id: str,
        milestone: dict[str, Any],
        human_authorized: bool | None,
        cancel_event: Any | None,
    ) -> MilestonePipelineResult:
        epic_run = self._prepare_epic_run(run, milestone_id=milestone_id, epic_id=epic_id)
        epic_pipeline = self.epic_pipeline_factory(self.root, self.config, self.repository, self.github, self._run)
        epic_result = epic_pipeline.run_epic(epic_run, human_authorized=human_authorized, cancel_event=cancel_event)
        if epic_result.status == RunStatus.FAILED:
            save_run_state(epic_result.run, root=self.root)
            return MilestonePipelineResult(
                status=RunStatus.FAILED,
                run=epic_result.run,
                milestone_id=milestone_id,
                epic_id=epic_id,
                branch_name=epic_result.branch_name,
                pull_request=epic_result.pull_request,
                epic_result=epic_result,
                reason=epic_result.reason,
            )
        if epic_result.status in {RunStatus.CANCELLED, RunStatus.COMPLETED}:
            save_run_state(epic_result.run, root=self.root)
            return MilestonePipelineResult(
                status=epic_result.status,
                run=epic_result.run,
                milestone_id=milestone_id,
                epic_id=epic_id,
                branch_name=epic_result.branch_name,
                pull_request=epic_result.pull_request,
                epic_result=epic_result,
                reason=epic_result.reason,
            )
        save_run_state(epic_result.run, root=self.root)
        return MilestonePipelineResult(
            status=RunStatus.WAITING_FOR_MERGE,
            run=epic_result.run,
            milestone_id=milestone_id,
            epic_id=epic_id,
            branch_name=epic_result.branch_name,
            pull_request=epic_result.pull_request,
            epic_result=epic_result,
        )

    def _advance_waiting_run(
        self,
        run: AutopilotRun,
        *,
        milestone_id: str,
        milestone: dict[str, Any],
        cancel_event: Any | None,
    ) -> MilestonePipelineResult:
        if run.pull_request is None:
            raise MilestonePipelineError("waiting run does not include a pull request")
        pr_metadata = self._load_pr_metadata(run.pull_request.number, cancel_event=cancel_event)
        state = str(pr_metadata.get("state") or "").lower()
        merged = pr_metadata.get("mergedAt") is not None or str(pr_metadata.get("state") or "").lower() == "merged"
        if _is_epic_branch(run.branch_name):
            merge_evidence = self._evaluate_merge_evidence(run, milestone, pr_metadata)
            if not merged:
                if state == "closed":
                    reason = "; ".join(merge_evidence.reasons) or "closed without merge"
                    finalized = replace(run, status=RunStatus.FAILED, updated_at=_timestamp(), last_error=reason)
                    save_run_state(finalized, root=self.root)
                    return MilestonePipelineResult(
                        status=RunStatus.FAILED,
                        run=finalized,
                        milestone_id=milestone_id,
                        epic_id=run.epic_id,
                        branch_name=run.branch_name,
                        pull_request=run.pull_request,
                        reason=reason,
                    )
                waiting = replace(run, status=RunStatus.WAITING_FOR_MERGE, updated_at=_timestamp())
                save_run_state(waiting, root=self.root)
                return MilestonePipelineResult(
                    status=RunStatus.WAITING_FOR_MERGE,
                    run=waiting,
                    milestone_id=milestone_id,
                    epic_id=run.epic_id,
                    branch_name=run.branch_name,
                    pull_request=run.pull_request,
                )
            if not merge_evidence.valid:
                reason = "; ".join(merge_evidence.reasons) or "pull request merge evidence is invalid"
                finalized = replace(run, status=RunStatus.FAILED, updated_at=_timestamp(), last_error=reason)
                save_run_state(finalized, root=self.root)
                return MilestonePipelineResult(
                    status=RunStatus.FAILED,
                    run=finalized,
                    milestone_id=milestone_id,
                    epic_id=run.epic_id,
                    branch_name=run.branch_name,
                    pull_request=run.pull_request,
                    reason=reason,
                )

            epic_id = run.epic_id or _extract_epic_id_from_branch(run.branch_name)
            if epic_id is None:
                raise MilestonePipelineError("cannot infer epic id from waiting branch")
            closure = self._create_epic_closure(run, milestone_id=milestone_id, epic_id=epic_id, cancel_event=cancel_event)
            return closure

        if _is_bookkeeping_epic_branch(run.branch_name):
            if not merged:
                if state == "closed":
                    reason = "closed bookkeeping PR is not merge evidence"
                    finalized = replace(run, status=RunStatus.FAILED, updated_at=_timestamp(), last_error=reason)
                    save_run_state(finalized, root=self.root)
                    return MilestonePipelineResult(
                        status=RunStatus.FAILED,
                        run=finalized,
                        milestone_id=milestone_id,
                        epic_id=run.epic_id,
                        branch_name=run.branch_name,
                        pull_request=run.pull_request,
                        reason=reason,
                    )
                waiting = replace(run, status=RunStatus.WAITING_FOR_MERGE, updated_at=_timestamp())
                save_run_state(waiting, root=self.root)
                return MilestonePipelineResult(
                    status=RunStatus.WAITING_FOR_MERGE,
                    run=waiting,
                    milestone_id=milestone_id,
                    epic_id=run.epic_id,
                    branch_name=run.branch_name,
                    pull_request=run.pull_request,
                )
            next_epic_id = next_ready_epic_for_milestone(milestone_id, tasks_file=self.tasks_file, directory=self.workstreams_dir)
            if next_epic_id is not None:
                return self._start_epic(
                    run,
                    milestone_id=milestone_id,
                    epic_id=next_epic_id,
                    milestone=milestone,
                    human_authorized=None,
                    cancel_event=cancel_event,
                )
            if self._milestone_can_close(milestone_id):
                return self._create_milestone_closure(
                    run,
                    milestone_id=milestone_id,
                    milestone=milestone,
                    cancel_event=cancel_event,
                )
            return self._complete_milestone(run, milestone_id=milestone_id, milestone=milestone, cancel_event=cancel_event)

        if _is_milestone_closure_branch(run.branch_name):
            if not merged:
                if state == "closed":
                    reason = "closed milestone closure PR is not merge evidence"
                    finalized = replace(run, status=RunStatus.FAILED, updated_at=_timestamp(), last_error=reason)
                    save_run_state(finalized, root=self.root)
                    return MilestonePipelineResult(
                        status=RunStatus.FAILED,
                        run=finalized,
                        milestone_id=milestone_id,
                        branch_name=run.branch_name,
                        pull_request=run.pull_request,
                        reason=reason,
                    )
                waiting = replace(run, status=RunStatus.WAITING_FOR_MERGE, updated_at=_timestamp())
                save_run_state(waiting, root=self.root)
                return MilestonePipelineResult(
                    status=RunStatus.WAITING_FOR_MERGE,
                    run=waiting,
                    milestone_id=milestone_id,
                    branch_name=run.branch_name,
                    pull_request=run.pull_request,
                )
            return self._complete_milestone(run, milestone_id=milestone_id, milestone=milestone, cancel_event=cancel_event)

        raise MilestonePipelineError(f"unsupported waiting branch {run.branch_name!r}")

    def _create_epic_closure(
        self,
        run: AutopilotRun,
        *,
        milestone_id: str,
        epic_id: str,
        cancel_event: Any | None,
    ) -> MilestonePipelineResult:
        epic_manifest = get_epic(epic_id, self.workstreams_dir)
        epic_path = self._epic_manifest_path(epic_id)
        if str(epic_manifest.get("status") or "") == "completed":
            return MilestonePipelineResult(
                status=RunStatus.COMPLETED,
                run=run,
                milestone_id=milestone_id,
                epic_id=epic_id,
                branch_name=run.branch_name,
                pull_request=run.pull_request,
            )

        self.repository.require_clean_tree()
        self.repository.switch_to_master_and_pull("master", "origin")
        branch_name = self._epic_closure_branch_name(milestone_id, epic_id)
        self.repository.create_branch(branch_name, base_branch="master")
        self._rewrite_manifest_status(epic_path, "completed")
        relative_path = epic_path.relative_to(self.root).as_posix()
        self.repository.stage_allowlist([relative_path])
        commit_result = self.repository.commit(f"chore({epic_id}): close epic")
        if commit_result.status != "PASS":
            raise MilestonePipelineError("epic closure commit failed")
        self._require_not_cancelled(cancel_event)
        push_result = self.repository.push(branch_name)
        if push_result.status != "PASS":
            raise MilestonePipelineError("epic closure push failed")
        auth = self.github.validate_auth(timeout_seconds=self.config.command_timeout_seconds)
        self._auth_guard(auth)
        pr = self.github.create_draft_pr(
            "master",
            branch_name,
            f"{epic_id}: close epic",
            self._closure_pr_body(
                kind="epic",
                milestone_id=milestone_id,
                epic_id=epic_id,
                branch_name=branch_name,
                status="completed",
            ),
            timeout_seconds=self.config.command_timeout_seconds,
        )
        finalized = replace(
            run,
            status=RunStatus.WAITING_FOR_MERGE,
            updated_at=_timestamp(),
            milestone_id=milestone_id,
            epic_id=epic_id,
            branch_name=branch_name,
            pull_request=pr,
        )
        save_run_state(finalized, root=self.root)
        return MilestonePipelineResult(
            status=RunStatus.WAITING_FOR_MERGE,
            run=finalized,
            milestone_id=milestone_id,
            epic_id=epic_id,
            branch_name=branch_name,
            pull_request=pr,
        )

    def _create_milestone_closure(
        self,
        run: AutopilotRun,
        *,
        milestone_id: str,
        milestone: dict[str, Any],
        cancel_event: Any | None,
    ) -> MilestonePipelineResult:
        milestone_path = self._milestone_manifest_path(milestone_id)
        self.repository.require_clean_tree()
        self.repository.switch_to_master_and_pull("master", "origin")
        branch_name = self._milestone_closure_branch_name(milestone_id)
        self.repository.create_branch(branch_name, base_branch="master")
        self._rewrite_manifest_status(milestone_path, "completed")
        relative_path = milestone_path.relative_to(self.root).as_posix()
        self.repository.stage_allowlist([relative_path])
        commit_result = self.repository.commit(f"chore({milestone_id}): close milestone")
        if commit_result.status != "PASS":
            raise MilestonePipelineError("milestone closure commit failed")
        self._require_not_cancelled(cancel_event)
        auth = self.github.validate_auth(timeout_seconds=self.config.command_timeout_seconds)
        self._auth_guard(auth)
        push_result = self.repository.push(branch_name)
        if push_result.status != "PASS":
            raise MilestonePipelineError("milestone closure push failed")
        pr = self.github.create_draft_pr(
            "master",
            branch_name,
            f"{milestone_id}: close milestone",
            self._closure_pr_body(
                kind="milestone",
                milestone_id=milestone_id,
                epic_id=None,
                branch_name=branch_name,
                status="completed",
            ),
            timeout_seconds=self.config.command_timeout_seconds,
        )
        finalized = replace(
            run,
            status=RunStatus.WAITING_FOR_MERGE,
            updated_at=_timestamp(),
            milestone_id=milestone_id,
            branch_name=branch_name,
            pull_request=pr,
        )
        save_run_state(finalized, root=self.root)
        return MilestonePipelineResult(
            status=RunStatus.WAITING_FOR_MERGE,
            run=finalized,
            milestone_id=milestone_id,
            branch_name=branch_name,
            pull_request=pr,
        )

    def _complete_milestone(
        self,
        run: AutopilotRun,
        *,
        milestone_id: str,
        milestone: dict[str, Any],
        cancel_event: Any | None,
    ) -> MilestonePipelineResult:
        if not self._milestone_can_close(milestone_id):
            raise MilestonePipelineError(f"milestone {milestone_id} cannot be completed yet")
        finalized = replace(
            run,
            status=RunStatus.COMPLETED,
            updated_at=_timestamp(),
            last_error=None,
        )
        save_run_state(finalized, root=self.root)
        return MilestonePipelineResult(
            status=RunStatus.COMPLETED,
            run=finalized,
            milestone_id=milestone_id,
            branch_name=run.branch_name,
            pull_request=run.pull_request,
        )

    def _prepare_epic_run(self, run: AutopilotRun, *, milestone_id: str, epic_id: str) -> AutopilotRun:
        request = replace(run.request, run_mode=RunMode.FULL)
        return replace(
            run,
            request=request,
            status=RunStatus.PREFLIGHT,
            milestone_id=milestone_id,
            epic_id=epic_id,
            branch_name=None,
            current_task_id=None,
            pull_request=None,
            last_error=None,
        )

    def _milestone_id_for(self, run: AutopilotRun) -> str:
        milestone_id = run.milestone_id or (run.request.scope_id if run.request.scope_type.value == "milestone" else "")
        if not milestone_id or not MILESTONE_ID_PATTERN.fullmatch(milestone_id):
            raise MilestonePipelineError("run does not declare a valid milestone id")
        return milestone_id

    def _milestone_manifest_path(self, milestone_id: str) -> Path:
        for path in sorted(self.workstreams_dir.glob("*.yml")):
            manifest = workstream_validation._load_yaml_manifest(path)
            if manifest.get("id") == milestone_id:
                return path
        raise FileNotFoundError(f"milestone manifest does not exist: {milestone_id}")

    def _epic_manifest_path(self, epic_id: str) -> Path:
        for path in sorted(self.workstreams_dir.glob("*.yml")):
            manifest = workstream_validation._load_yaml_manifest(path)
            if manifest.get("id") == epic_id:
                return path
        raise FileNotFoundError(f"epic manifest does not exist: {epic_id}")

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
            raise MilestonePipelineError(f"{path.name}: missing status field")
        path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")

    def _evaluate_merge_evidence(self, run: AutopilotRun, milestone: dict[str, Any], pr_metadata: dict[str, Any]) -> epic_close_evidence.MergeEvidenceResult:
        epic_id = run.epic_id or ""
        epic_manifest = get_epic(epic_id, self.workstreams_dir) if epic_id else {}
        epic_branch = str(epic_manifest.get("branch") or run.branch_name or "")
        base_branch = str(epic_manifest.get("base_branch") or "master")
        epic_head_sha = pr_metadata.get("headRefOid") or pr_metadata.get("head_sha")
        base_sha = pr_metadata.get("baseRefOid") or pr_metadata.get("base_sha")
        return epic_close_evidence.evaluate_merge_evidence(
            epic_id=epic_id,
            epic_branch=epic_branch,
            base_branch=base_branch,
            epic_head_sha=str(epic_head_sha) if epic_head_sha else None,
            base_sha=str(base_sha) if base_sha else None,
            github_pr=pr_metadata,
            github_integration_available=True,
        )

    def _load_pr_metadata(self, number: int, *, cancel_event: Any | None) -> dict[str, Any]:
        result = self._run(
            [
                "gh",
                "pr",
                "view",
                str(number),
                "--json",
                "number,url,title,baseRefName,headRefName,isDraft,state,mergedAt,headRefOid,baseRefOid",
            ],
            cwd=self.root,
            timeout_seconds=self.config.command_timeout_seconds,
            cancel_event=cancel_event,
            heartbeat_seconds=0,
        )
        if result.status != "PASS":
            raise MilestonePipelineError("cannot read pull request metadata from gh")
        payload = _parse_json_object(_combine_lines([*result.stdout_lines, *result.stderr_lines]))
        if not isinstance(payload, dict):
            raise MilestonePipelineError("gh did not return pull request metadata")
        return payload

    def _auth_guard(self, auth: GitHubAuthResult) -> None:
        if not auth.available or not auth.authenticated:
            raise MilestonePipelineError(auth.reason or "gh authentication failed")

    def _closure_pr_body(
        self,
        *,
        kind: str,
        milestone_id: str,
        epic_id: str | None,
        branch_name: str,
        status: str,
    ) -> str:
        lines = [
            f"Kind: {kind}",
            f"Milestone: {milestone_id}",
            f"Epic: {epic_id or 'none'}",
            f"Branch: {branch_name}",
            f"Status: {status}",
            "Draft bookkeeping PR created by local autopilot.",
            "No merge or deployment is performed automatically.",
        ]
        return "\n".join(lines)

    def _milestone_can_close(self, milestone_id: str) -> bool:
        milestone = get_milestone(milestone_id, self.workstreams_dir)
        if not milestone.get("completion_criteria"):
            return False
        epics = [epic for epic in list_epics(self.workstreams_dir, milestone_id=milestone_id) if isinstance(epic.get("id"), str)]
        if not epics:
            return False
        if any(str(epic.get("status") or "") != "completed" for epic in epics):
            return False
        return all(self._epic_tasks_complete(str(epic["id"])) for epic in epics)

    def _epic_tasks_complete(self, epic_id: str) -> bool:
        return all_epic_tasks_complete(epic_id, tasks_file=self.tasks_file, directory=self.workstreams_dir)

    def _epic_closure_branch_name(self, milestone_id: str, epic_id: str) -> str:
        return f"{BOOKKEEPING_BRANCH_PREFIX}/{milestone_id}/{epic_id}"

    def _milestone_closure_branch_name(self, milestone_id: str) -> str:
        return f"{BOOKKEEPING_BRANCH_PREFIX}/{milestone_id}/close"

    def _require_not_cancelled(self, cancel_event: Any | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise KeyboardInterrupt()


def run_milestone_pipeline(
    run: AutopilotRun,
    *,
    root: Path | str = ROOT,
    config: AutopilotConfig | None = None,
    repository: repository_module.Repository | None = None,
    epic_pipeline_factory: Callable[[Path, AutopilotConfig, repository_module.Repository, GitHubAdapter, Callable[..., process_runner.ProcessResult]], EpicPipeline] | None = None,
    github_adapter: GitHubAdapter | None = None,
    process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
    cancel_event: Any | None = None,
    human_authorized: bool | None = None,
) -> MilestonePipelineResult:
    pipeline = MilestonePipeline(
        root,
        config=config,
        repository=repository,
        epic_pipeline_factory=epic_pipeline_factory,
        github_adapter=github_adapter,
        process_runner_fn=process_runner_fn,
    )
    return pipeline.run_milestone(run, human_authorized=human_authorized, cancel_event=cancel_event)


def _parse_json_object(text: str) -> Any:
    if not text.strip():
        return None
    return json.loads(text)


def _combine_lines(lines: Sequence[str]) -> str:
    return "\n".join(line for line in lines if line)


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _is_epic_branch(branch_name: str | None) -> bool:
    return bool(branch_name and ACTIVE_EPIC_BRANCH_PATTERN.fullmatch(branch_name))


def _is_bookkeeping_epic_branch(branch_name: str | None) -> bool:
    return bool(branch_name and EPIC_CLOSURE_BRANCH_PATTERN.fullmatch(branch_name))


def _is_milestone_closure_branch(branch_name: str | None) -> bool:
    return bool(branch_name and MILESTONE_CLOSURE_BRANCH_PATTERN.fullmatch(branch_name))


def _extract_epic_id_from_branch(branch_name: str | None) -> str | None:
    if not branch_name:
        return None
    match = EPIC_CLOSURE_BRANCH_PATTERN.fullmatch(branch_name)
    return match.group(2) if match else None


__all__ = [
    "ACTIVE_EPIC_FILE",
    "BOOKKEEPING_BRANCH_PREFIX",
    "MilestonePipeline",
    "MilestonePipelineError",
    "MilestonePipelineResult",
    "TASKS_FILE",
    "WORKSTREAMS_DIR",
    "run_milestone_pipeline",
]

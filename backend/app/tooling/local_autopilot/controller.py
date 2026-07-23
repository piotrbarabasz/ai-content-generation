"""Controller for the local autopilot desktop workflow."""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from . import process_runner
from .config import AutopilotConfig, DEFAULT_AUTOPILOT_CONFIG_PATH, load_autopilot_config
from .epic_pipeline import EpicPipelineResult, run_epic_pipeline
from .milestone_pipeline import MilestonePipelineResult, run_milestone_pipeline
from .models import AutopilotRequest, AutopilotRun, PullRequestInfo, RunMode, RunStatus, ScopeType
from .state_store import AUTOPILOT_STATE_DIR, load_run_state, run_state_path, save_run_state
from .workstreams import list_epics, list_milestones

ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class ScopeChoices:
    epic_ids: tuple[str, ...]
    milestone_ids: tuple[str, ...]


@dataclass(frozen=True)
class ControllerSnapshot:
    run_id: str | None
    repo_path: str
    scope_type: ScopeType | None
    scope_id: str | None
    run_mode: RunMode | None
    create_draft_pr: bool
    running: bool
    status: RunStatus
    branch_name: str | None = None
    epic_id: str | None = None
    milestone_id: str | None = None
    current_task_id: str | None = None
    progress: int = 0
    last_commit: str | None = None
    pull_request_number: int | None = None
    pull_request_url: str | None = None
    pull_request_title: str | None = None
    last_error: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class ControllerEvent:
    kind: str
    message: str = ""
    snapshot: ControllerSnapshot | None = None
    data: dict[str, Any] = field(default_factory=dict)


class AutopilotControllerError(RuntimeError):
    pass


class AutopilotController:
    def __init__(
        self,
        root: Path | str = ROOT,
        *,
        config: AutopilotConfig | None = None,
        config_path: Path | str = DEFAULT_AUTOPILOT_CONFIG_PATH,
        process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
        epic_pipeline_runner: Callable[..., EpicPipelineResult] = run_epic_pipeline,
        milestone_pipeline_runner: Callable[..., MilestonePipelineResult] = run_milestone_pipeline,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.runtime_dir = self.root / ".specify" / "runtime" / "autopilot"
        self.config = config or load_autopilot_config(config_path)
        self._run = process_runner_fn
        self._epic_pipeline_runner = epic_pipeline_runner
        self._milestone_pipeline_runner = milestone_pipeline_runner
        self._run_id_factory = run_id_factory or (lambda: uuid.uuid4().hex)
        self._events: queue.Queue[ControllerEvent] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._lock = threading.RLock()
        self._snapshot = ControllerSnapshot(
            run_id=None,
            repo_path=str(self.root),
            scope_type=None,
            scope_id=None,
            run_mode=None,
            create_draft_pr=True,
            running=False,
            status=RunStatus.IDLE,
        )
        self._current_run: AutopilotRun | None = None

    def available_scope_choices(self, repo_path: Path | str | None = None) -> ScopeChoices:
        root = self._resolve_repo_root(repo_path)
        epics = tuple(str(manifest.get("id") or "") for manifest in list_epics(root / ".specify" / "workstreams") if str(manifest.get("id") or "").strip())
        milestones = tuple(str(manifest.get("id") or "") for manifest in list_milestones(root / ".specify" / "workstreams") if str(manifest.get("id") or "").strip())
        return ScopeChoices(epic_ids=epics, milestone_ids=milestones)

    def start_run(
        self,
        *,
        repo_path: Path | str,
        scope_type: ScopeType,
        scope_id: str,
        run_mode: RunMode,
        create_draft_pr: bool,
    ) -> AutopilotRun:
        with self._lock:
            self._ensure_idle()
            repo_root = self._resolve_repo_root(repo_path)
            request = AutopilotRequest(
                scope_type=scope_type,
                scope_id=scope_id,
                run_mode=run_mode,
                repo_path=str(repo_root),
                created_by="ui",
                human_authorized=True,
            )
            run = AutopilotRun(
                run_id=self._run_id_factory(),
                request=request,
                status=RunStatus.PREFLIGHT,
                created_at=_timestamp(),
                updated_at=_timestamp(),
                epic_id=scope_id if scope_type is ScopeType.EPIC else None,
                milestone_id=scope_id if scope_type is ScopeType.MILESTONE else None,
            )
            save_run_state(run, root=self.root)
            self._current_run = run
            self._snapshot = self._snapshot_from_run(run, repo_path=str(repo_root), create_draft_pr=create_draft_pr, running=True, message="starting")
            self._cancel_event = threading.Event()
            self._start_worker(run, create_draft_pr=create_draft_pr)
            return run

    def resume_run(
        self,
        *,
        repo_path: Path | str,
        scope_type: ScopeType,
        scope_id: str,
        create_draft_pr: bool = True,
    ) -> AutopilotRun:
        with self._lock:
            self._ensure_idle()
            repo_root = self._resolve_repo_root(repo_path)
            if scope_type is not ScopeType.MILESTONE:
                raise AutopilotControllerError("resume is only supported for milestones")
            run = self._latest_run_for_scope(scope_type, scope_id)
            if run is None:
                raise AutopilotControllerError(f"no saved run exists for {scope_id}")
            if run.status is not RunStatus.WAITING_FOR_MERGE:
                raise AutopilotControllerError(f"saved run for {scope_id} is not waiting for merge")
            if Path(run.request.repo_path) != repo_root:
                raise AutopilotControllerError("saved run belongs to a different repository path")
            self._current_run = run
            self._snapshot = self._snapshot_from_run(run, repo_path=str(repo_root), create_draft_pr=create_draft_pr, running=True, message="resuming")
            self._cancel_event = threading.Event()
            self._start_worker(run, create_draft_pr=create_draft_pr)
            return run

    def stop(self) -> bool:
        with self._lock:
            if self._cancel_event is None:
                return False
            self._cancel_event.set()
            self._emit(
                "log",
                "Cancellation requested.",
                snapshot=self._snapshot,
            )
            return True

    def is_running(self) -> bool:
        with self._lock:
            return self._worker is not None and self._worker.is_alive()

    def snapshot(self) -> ControllerSnapshot:
        with self._lock:
            return self._snapshot

    def current_run(self) -> AutopilotRun | None:
        with self._lock:
            return self._current_run

    def poll_events(self) -> list[ControllerEvent]:
        events: list[ControllerEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

    def open_logs_path(self) -> Path:
        return self.runtime_dir

    def latest_pr_url(self) -> str | None:
        with self._lock:
            if self._snapshot.pull_request_url:
                return self._snapshot.pull_request_url
            run = self._current_run
            if run is None or run.pull_request is None:
                return None
            return run.pull_request.url

    def latest_state_path(self) -> Path | None:
        with self._lock:
            run = self._current_run
            if run is None:
                return None
            return run_state_path(run.run_id, root=self.root)

    def _start_worker(self, run: AutopilotRun, *, create_draft_pr: bool) -> None:
        worker = threading.Thread(
            target=self._worker_main,
            args=(run, create_draft_pr),
            name=f"local-autopilot-{run.run_id}",
            daemon=True,
        )
        self._worker = worker
        worker.start()

    def _worker_main(self, run: AutopilotRun, create_draft_pr: bool) -> None:
        cancel_event = self._cancel_event
        assert cancel_event is not None
        try:
            self._emit("log", f"Run {run.run_id} started.", snapshot=self._snapshot_from_run(run, repo_path=run.request.repo_path, create_draft_pr=create_draft_pr, running=True, message="started"))
            pipeline_runner = self._logging_process_runner(cancel_event)
            if run.request.scope_type is ScopeType.EPIC:
                result = self._epic_pipeline_runner(
                    run,
                    root=self.root,
                    config=self.config,
                    process_runner_fn=pipeline_runner,
                    cancel_event=cancel_event,
                    human_authorized=run.request.human_authorized,
                )
            else:
                result = self._milestone_pipeline_runner(
                    run,
                    root=self.root,
                    config=self.config,
                    process_runner_fn=pipeline_runner,
                    cancel_event=cancel_event,
                    human_authorized=run.request.human_authorized,
                )
            self._finish(result.run, message=result.reason or result.status.value, create_draft_pr=create_draft_pr)
            self._emit(
                "finished",
                result.reason or result.status.value,
                snapshot=self._snapshot,
                data={
                    "status": result.status.value,
                    "epic_id": getattr(result, "epic_id", None),
                    "milestone_id": getattr(result, "milestone_id", None),
                    "branch_name": getattr(result, "branch_name", None),
                    "pull_request": _pull_request_payload(getattr(result, "pull_request", None)),
                },
            )
        except Exception as exc:  # pragma: no cover - defensive fallback for worker crashes
            self._mark_failed(str(exc), create_draft_pr=create_draft_pr)
            self._emit("failed", str(exc), snapshot=self._snapshot)
        finally:
            with self._lock:
                self._worker = None
                self._cancel_event = None

    def _finish(self, run: AutopilotRun, *, message: str, create_draft_pr: bool) -> None:
        with self._lock:
            self._current_run = run
            self._snapshot = self._snapshot_from_run(
                run,
                repo_path=run.request.repo_path,
                create_draft_pr=create_draft_pr,
                running=False,
                message=message,
            )

    def _mark_failed(self, message: str, *, create_draft_pr: bool) -> None:
        with self._lock:
            run = self._current_run
            if run is not None:
                run = replace(run, status=RunStatus.FAILED, updated_at=_timestamp(), last_error=message)
                save_run_state(run, root=self.root)
                self._current_run = run
                self._snapshot = self._snapshot_from_run(
                    run,
                    repo_path=run.request.repo_path,
                    create_draft_pr=create_draft_pr,
                    running=False,
                    message=message,
                )
            else:
                self._snapshot = replace(self._snapshot, running=False, status=RunStatus.FAILED, last_error=message, message=message)

    def _emit(self, kind: str, message: str, *, snapshot: ControllerSnapshot | None = None, data: dict[str, Any] | None = None) -> None:
        self._events.put(
            ControllerEvent(
                kind=kind,
                message=message,
                snapshot=snapshot,
                data=dict(data or {}),
            )
        )

    def _logging_process_runner(self, cancel_event: threading.Event) -> Callable[..., process_runner.ProcessResult]:
        def _runner(argv: list[str], **kwargs: Any) -> process_runner.ProcessResult:
            self._emit("log", f"$ {' '.join(argv)}")
            result = self._run(argv, cancel_event=cancel_event, **kwargs)
            for line in result.stdout_lines:
                self._emit("log", line)
            for line in result.stderr_lines:
                self._emit("log", line)
            self._emit("log", f"command status: {result.status}")
            return result

        return _runner

    def _snapshot_from_run(
        self,
        run: AutopilotRun,
        *,
        repo_path: str,
        create_draft_pr: bool,
        running: bool,
        message: str | None,
    ) -> ControllerSnapshot:
        last_commit = _last_commit_from_run(run)
        pr = run.pull_request
        progress = _progress_for_status(run.status)
        return ControllerSnapshot(
            run_id=run.run_id,
            repo_path=repo_path,
            scope_type=run.request.scope_type,
            scope_id=run.request.scope_id,
            run_mode=run.request.run_mode,
            create_draft_pr=create_draft_pr,
            running=running,
            status=run.status,
            branch_name=run.branch_name,
            epic_id=run.epic_id,
            milestone_id=run.milestone_id,
            current_task_id=run.current_task_id,
            progress=progress,
            last_commit=last_commit,
            pull_request_number=pr.number if pr is not None else None,
            pull_request_url=pr.url if pr is not None else None,
            pull_request_title=pr.title if pr is not None else None,
            last_error=run.last_error,
            message=message,
        )

    def _resolve_repo_root(self, repo_path: Path | str | None) -> Path:
        candidate = Path(repo_path) if repo_path is not None else self.root
        return candidate.expanduser().resolve(strict=False)

    def _ensure_idle(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            raise AutopilotControllerError("autopilot is already running")

    def _latest_run_for_scope(self, scope_type: ScopeType, scope_id: str) -> AutopilotRun | None:
        if not self.runtime_dir.is_dir():
            return None
        matches: list[AutopilotRun] = []
        for path in sorted(self.runtime_dir.glob("*.json")):
            try:
                run = load_run_state(path.stem, root=self.root)
            except Exception:
                continue
            if run.request.scope_type is not scope_type:
                continue
            if run.request.scope_id != scope_id:
                continue
            matches.append(run)
        if not matches:
            return None
        matches.sort(key=lambda item: item.updated_at)
        return matches[-1]


def _progress_for_status(status: RunStatus) -> int:
    progress_map = {
        RunStatus.IDLE: 0,
        RunStatus.PREFLIGHT: 5,
        RunStatus.ACTIVATING: 10,
        RunStatus.BRANCHING: 20,
        RunStatus.TASK_RUNNING: 35,
        RunStatus.TASK_VALIDATING: 50,
        RunStatus.TASK_COMMITTING: 60,
        RunStatus.EPIC_REVIEW: 75,
        RunStatus.PUSHING: 85,
        RunStatus.PR_CREATING: 90,
        RunStatus.WAITING_FOR_MERGE: 95,
        RunStatus.CLOSING: 98,
        RunStatus.COMPLETED: 100,
        RunStatus.FAILED: 100,
        RunStatus.CANCELLED: 100,
    }
    return progress_map.get(status, 0)


def _last_commit_from_run(run: AutopilotRun) -> str | None:
    if run.task_results:
        commit_sha = run.task_results[-1].commit_sha
        if commit_sha:
            return commit_sha
    return None


def _pull_request_payload(pr: PullRequestInfo | None) -> dict[str, Any] | None:
    if pr is None:
        return None
    return {
        "number": pr.number,
        "url": pr.url,
        "title": pr.title,
        "base_branch": pr.base_branch,
        "head_branch": pr.head_branch,
        "draft": pr.draft,
        "merged": pr.merged,
    }


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def run_local_autopilot_controller(
    *,
    root: Path | str = ROOT,
    config: AutopilotConfig | None = None,
    process_runner_fn: Callable[..., process_runner.ProcessResult] = process_runner.run_process,
) -> AutopilotController:
    return AutopilotController(root, config=config, process_runner_fn=process_runner_fn)


__all__ = [
    "AutopilotController",
    "AutopilotControllerError",
    "ControllerEvent",
    "ControllerSnapshot",
    "ROOT",
    "ScopeChoices",
    "run_local_autopilot_controller",
]

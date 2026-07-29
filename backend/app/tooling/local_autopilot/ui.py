"""Tkinter user interface for the local autopilot."""

from __future__ import annotations

import os
import webbrowser
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .controller import AutopilotController, AutopilotControllerError, ControllerEvent, ControllerSnapshot, ScopeChoices
from .epic_pipeline import retry_push_pipeline
from .recovery import assess_task_recovery
from .github_adapter import GitHubAdapter
from .models import RunMode, RunStatus, ScopeType
from . import process_runner
from .scope_proposal import build_suggested_metadata_change, load_scope_expansion_proposal, reject_scope_expansion_proposal
from .repository import Repository
from .task_pipeline import TaskPipeline


@dataclass(frozen=True)
class StartSummary:
    repo_path: str
    scope_type: str
    scope_id: str
    run_mode: str
    create_draft_pr: bool
    auto_merge: str = "NO"
    commit: str = "YES"
    push: str = "YES"
    pr: str = "YES"


@dataclass(frozen=True)
class TaskRetrySummary:
    task_id: str
    state: str
    allowed_dirty_paths: tuple[str, ...]
    unexpected_paths: tuple[str, ...]
    codex_skipped: bool
    reason: str


@dataclass(frozen=True)
class ScopeExpansionSummary:
    task_id: str
    state: str
    current_allowlist: tuple[str, ...]
    unexpected_paths: tuple[str, ...]
    codex_summary: str
    suggested_metadata_change: str
    reason: str
    codex_skipped: bool


class ViewAdapter(Protocol):
    def get_repo_path(self) -> str: ...

    def set_repo_path(self, value: str) -> None: ...

    def get_scope_type(self) -> str: ...

    def set_scope_type(self, value: str) -> None: ...

    def get_scope_id(self) -> str: ...

    def set_scope_id(self, value: str) -> None: ...

    def set_scope_ids(self, values: list[str]) -> None: ...

    def get_run_mode(self) -> str: ...

    def set_run_mode(self, value: str) -> None: ...

    def get_create_draft_pr(self) -> bool: ...

    def set_create_draft_pr(self, value: bool) -> None: ...

    def set_action_states(self, *, busy: bool, can_resume: bool, can_open_pr: bool, can_retry: bool, can_retry_task: bool) -> None: ...

    def confirm_retry_task(self, summary: TaskRetrySummary) -> bool: ...

    def confirm_scope_expansion(self, summary: ScopeExpansionSummary) -> str: ...

    def set_snapshot(self, snapshot: ControllerSnapshot) -> None: ...

    def append_log(self, message: str) -> None: ...

    def clear_logs(self) -> None: ...

    def confirm_start(self, summary: StartSummary) -> bool: ...

    def confirm_close_during_run(self) -> bool: ...

    def show_info(self, title: str, message: str) -> None: ...

    def show_error(self, title: str, message: str) -> None: ...


class TkAutopilotView:
    def __init__(
        self,
        root: tk.Misc,
        *,
        on_browse_repo: Callable[[], None],
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_resume: Callable[[], None],
        on_open_pr: Callable[[], None],
        on_retry_push: Callable[[], None],
        on_retry_task: Callable[[], None],
        on_open_logs: Callable[[], None],
        on_scope_change: Callable[[], None],
        on_repo_change: Callable[[], None],
    ) -> None:
        self.root = root
        self._repo_path_var = tk.StringVar(master=root, value="")
        self._scope_type_var = tk.StringVar(master=root, value=ScopeType.EPIC.value)
        self._scope_id_var = tk.StringVar(master=root, value="")
        self._run_mode_var = tk.StringVar(master=root, value=RunMode.FULL.value)
        self._create_draft_pr_var = tk.BooleanVar(master=root, value=True)

        root.title("AI Content Studio Local Autopilot")
        root.geometry("1180x760")

        main = ttk.Frame(root, padding=12)
        main.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(5, weight=1)

        ttk.Label(main, text="Repository").grid(row=0, column=0, sticky="w")
        repo_row = ttk.Frame(main)
        repo_row.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        repo_row.columnconfigure(0, weight=1)
        self._repo_entry = ttk.Entry(repo_row, textvariable=self._repo_path_var)
        self._repo_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(repo_row, text="Browse", command=on_browse_repo).grid(row=0, column=1, padx=(8, 0))
        self._repo_entry.bind("<FocusOut>", lambda _event: on_repo_change())

        ttk.Label(main, text="Scope").grid(row=1, column=0, sticky="w")
        scope_row = ttk.Frame(main)
        scope_row.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        scope_row.columnconfigure(1, weight=1)
        self._scope_type = ttk.Combobox(scope_row, values=[ScopeType.EPIC.value, ScopeType.MILESTONE.value], textvariable=self._scope_type_var, state="readonly", width=18)
        self._scope_type.grid(row=0, column=0, sticky="w")
        self._scope_type.bind("<<ComboboxSelected>>", lambda _event: on_scope_change())
        self._scope_id = ttk.Combobox(scope_row, textvariable=self._scope_id_var, state="readonly")
        self._scope_id.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(main, text="Mode").grid(row=2, column=0, sticky="w")
        mode_row = ttk.Frame(main)
        mode_row.grid(row=2, column=1, sticky="ew", pady=(0, 8))
        mode_row.columnconfigure(1, weight=1)
        self._run_mode = ttk.Combobox(mode_row, values=[RunMode.FULL.value, RunMode.STOP_BEFORE_PUSH.value], textvariable=self._run_mode_var, state="readonly", width=18)
        self._run_mode.grid(row=0, column=0, sticky="w")
        self._create_draft_pr = ttk.Checkbutton(mode_row, text="Create draft PR", variable=self._create_draft_pr_var)
        self._create_draft_pr.grid(row=0, column=1, sticky="w", padx=(16, 0))

        button_row = ttk.Frame(main)
        button_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 10))
        self._start_button = ttk.Button(button_row, text="Start", command=on_start)
        self._stop_button = ttk.Button(button_row, text="Stop", command=on_stop)
        self._resume_button = ttk.Button(button_row, text="Resume", command=on_resume)
        self._open_pr_button = ttk.Button(button_row, text="Open PR", command=on_open_pr)
        self._retry_push_button = ttk.Button(button_row, text="Retry push", command=on_retry_push)
        self._retry_task_button = ttk.Button(button_row, text="Retry current task", command=on_retry_task)
        self._open_logs_button = ttk.Button(button_row, text="Open logs", command=on_open_logs)
        for column, widget in enumerate((self._start_button, self._stop_button, self._resume_button, self._open_pr_button, self._retry_push_button, self._retry_task_button, self._open_logs_button)):
            widget.grid(row=0, column=column, padx=(0, 8))

        summary = ttk.Frame(main)
        summary.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        for index in range(4):
            summary.columnconfigure(index * 2 + 1, weight=1)
        self._summary_vars: dict[str, tk.StringVar] = {
            "branch": tk.StringVar(master=root, value="-"),
            "status": tk.StringVar(master=root, value="idle"),
            "epic": tk.StringVar(master=root, value="-"),
            "task": tk.StringVar(master=root, value="-"),
            "progress": tk.StringVar(master=root, value="0%"),
            "last_commit": tk.StringVar(master=root, value="-"),
            "pull_request": tk.StringVar(master=root, value="-"),
        }
        labels = (
            ("Branch", "branch"),
            ("Status", "status"),
            ("Epic", "epic"),
            ("Task", "task"),
            ("Progress", "progress"),
            ("Last commit", "last_commit"),
            ("PR", "pull_request"),
        )
        row = 0
        col = 0
        for label, key in labels:
            ttk.Label(summary, text=f"{label}:").grid(row=row, column=col, sticky="w")
            ttk.Label(summary, textvariable=self._summary_vars[key]).grid(row=row, column=col + 1, sticky="ew", padx=(4, 16))
            col += 2
            if col >= 6:
                row += 1
                col = 0

        progress_row = ttk.Frame(main)
        progress_row.grid(row=5, column=0, columnspan=2, sticky="nsew")
        progress_row.columnconfigure(0, weight=1)
        self._progress_var = tk.IntVar(master=root, value=0)
        self._progress = ttk.Progressbar(progress_row, mode="determinate", maximum=100, variable=self._progress_var)
        self._progress.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self._log = ScrolledText(progress_row, height=18, wrap="word")
        self._log.grid(row=1, column=0, sticky="nsew")
        progress_row.rowconfigure(1, weight=1)
        self._log.configure(state="disabled")

        self.set_action_states(busy=False, can_resume=False, can_open_pr=False, can_retry=False, can_retry_task=False)

    def get_repo_path(self) -> str:
        return self._repo_path_var.get().strip()

    def set_repo_path(self, value: str) -> None:
        self._repo_path_var.set(value)

    def get_scope_type(self) -> str:
        return self._scope_type_var.get().strip() or ScopeType.EPIC.value

    def set_scope_type(self, value: str) -> None:
        self._scope_type_var.set(value)

    def get_scope_id(self) -> str:
        return self._scope_id_var.get().strip()

    def set_scope_id(self, value: str) -> None:
        self._scope_id_var.set(value)

    def set_scope_ids(self, values: list[str]) -> None:
        self._scope_id["values"] = values
        if self.get_scope_id() not in values:
            self._scope_id_var.set(values[0] if values else "")

    def get_run_mode(self) -> str:
        return self._run_mode_var.get().strip() or RunMode.FULL.value

    def set_run_mode(self, value: str) -> None:
        self._run_mode_var.set(value)

    def get_create_draft_pr(self) -> bool:
        return bool(self._create_draft_pr_var.get())

    def set_create_draft_pr(self, value: bool) -> None:
        self._create_draft_pr_var.set(bool(value))

    def set_action_states(self, *, busy: bool, can_resume: bool, can_open_pr: bool, can_retry: bool, can_retry_task: bool) -> None:
        self._start_button.configure(state="disabled" if busy else "normal")
        self._stop_button.configure(state="normal" if busy else "disabled")
        self._resume_button.configure(state="disabled" if busy or not can_resume else "normal")
        self._open_pr_button.configure(state="normal" if can_open_pr else "disabled")
        self._retry_push_button.configure(state="normal" if can_retry else "disabled")
        self._retry_task_button.configure(state="normal" if can_retry_task else "disabled")
        self._open_logs_button.configure(state="normal")
        self._repo_entry.configure(state="disabled" if busy else "normal")
        self._scope_type.configure(state="disabled" if busy else "readonly")
        self._scope_id.configure(state="disabled" if busy else "readonly")
        self._run_mode.configure(state="disabled" if busy else "readonly")
        self._create_draft_pr.configure(state="disabled" if busy else "normal")

    def set_snapshot(self, snapshot: ControllerSnapshot) -> None:
        self._summary_vars["branch"].set(snapshot.branch_name or "-")
        self._summary_vars["status"].set(snapshot.status.value)
        self._summary_vars["epic"].set(snapshot.epic_id or snapshot.scope_id or "-")
        self._summary_vars["task"].set(snapshot.current_task_id or "-")
        self._summary_vars["progress"].set(f"{snapshot.progress}%")
        self._summary_vars["last_commit"].set(snapshot.last_commit or "-")
        if snapshot.pull_request_url:
            pr_label = snapshot.pull_request_title or snapshot.pull_request_url
            if snapshot.pull_request_number is not None:
                pr_label = f"#{snapshot.pull_request_number} {pr_label}"
            self._summary_vars["pull_request"].set(pr_label)
        else:
            self._summary_vars["pull_request"].set("-")
        self._progress_var.set(snapshot.progress)

    def append_log(self, message: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", f"{message}\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def clear_logs(self) -> None:
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def confirm_start(self, summary: StartSummary) -> bool:
        message = "\n".join(
            [
                f"Repo: {summary.repo_path}",
                f"Scope: {summary.scope_type}",
                f"ID: {summary.scope_id}",
                f"Mode: {summary.run_mode}",
                f"Create draft PR: {'yes' if summary.create_draft_pr else 'no'}",
                f"AUTO MERGE: {summary.auto_merge}",
                f"Commit: {summary.commit}",
                f"Push: {summary.push}",
                f"PR: {summary.pr}",
            ]
        )
        return messagebox.askyesno("Start autopilot", message)

    def confirm_close_during_run(self) -> bool:
        return messagebox.askyesno("Close autopilot", "Autopilot is still running. Stop it and close the window?")

    def show_info(self, title: str, message: str) -> None:
        messagebox.showinfo(title, message)

    def show_error(self, title: str, message: str) -> None:
        messagebox.showerror(title, message)

    def confirm_retry_task(self, summary: TaskRetrySummary) -> bool:
        message = "\n".join(
            [
                f"Task: {summary.task_id}",
                f"State: {summary.state}",
                f"Allowed dirty paths: {', '.join(summary.allowed_dirty_paths) or 'none'}",
                f"Unexpected paths: {', '.join(summary.unexpected_paths) or 'none'}",
                f"Codex skipped: {'yes' if summary.codex_skipped else 'no'}",
                f"Reason: {summary.reason}",
            ]
        )
        return messagebox.askyesno("Retry current task", message)

    def confirm_scope_expansion(self, summary: ScopeExpansionSummary) -> str:
        dialog = tk.Toplevel(self.root)
        dialog.title("Scope expansion approval")
        dialog.transient(self.root)
        dialog.grab_set()
        result = tk.StringVar(master=self.root, value="")

        frame = ttk.Frame(dialog, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        unexpected_lines = [f"- {path}" for path in summary.unexpected_paths] or ["- none"]
        allowlist_lines = [f"- {path}" for path in summary.current_allowlist] or ["- none"]
        text = "\n".join(
            [
                f"Task: {summary.task_id}",
                f"State: {summary.state}",
                f"Codex skipped: {'yes' if summary.codex_skipped else 'no'}",
                "Unexpected paths:",
                *unexpected_lines,
                "Current allowlist:",
                *allowlist_lines,
                "Codex summary:",
                summary.codex_summary or "none",
                "Suggested metadata change:",
                summary.suggested_metadata_change,
                "Reason:",
                summary.reason,
            ]
        )
        label = ttk.Label(frame, text=text, justify="left")
        label.grid(row=0, column=0, columnspan=3, sticky="w")

        def _close(choice: str) -> None:
            result.set(choice)
            if dialog.winfo_exists():
                dialog.destroy()

        def _copy_and_keep() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(summary.suggested_metadata_change)
            _close("copy")

        ttk.Button(frame, text="Copy suggested metadata change", command=_copy_and_keep).grid(row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Button(frame, text="Retry after spec update", command=lambda: _close("retry")).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(12, 0))
        ttk.Button(frame, text="Reject changes", command=lambda: _close("reject")).grid(row=1, column=2, sticky="w", padx=(8, 0), pady=(12, 0))

        dialog.protocol("WM_DELETE_WINDOW", lambda: _close("reject"))
        dialog.wait_variable(result)
        return result.get()


class LocalAutopilotUI:
    def __init__(
        self,
        root: tk.Misc | None = None,
        *,
        controller: AutopilotController | None = None,
        view: ViewAdapter | None = None,
        opener: Callable[[str], bool] = webbrowser.open,
        folder_opener: Callable[[str], Any] | None = None,
        poll_interval_ms: int = 150,
    ) -> None:
        self.root = root or tk.Tk()
        self.controller = controller or AutopilotController()
        self.opener = opener
        self.folder_opener = folder_opener or _open_folder
        self.poll_interval_ms = poll_interval_ms
        self.view = view or TkAutopilotView(
            self.root,
            on_browse_repo=self.browse_repo,
            on_start=self.start,
            on_stop=self.stop,
            on_resume=self.resume,
            on_open_pr=self.open_pr,
            on_retry_push=self.retry_push,
            on_retry_task=self.retry_current_task,
            on_open_logs=self.open_logs,
            on_scope_change=self.refresh_scope_ids,
            on_repo_change=self.refresh_scope_ids,
        )
        self._closing_requested = False
        self._after_id: str | None = None
        self.root.protocol("WM_DELETE_WINDOW", self.close_requested)
        self.refresh_scope_ids()
        self._schedule_poll()

    def browse_repo(self) -> None:
        current = self.view.get_repo_path() or str(self.controller.root)
        selected = filedialog.askdirectory(title="Select repository", initialdir=current)
        if not selected:
            return
        self.view.set_repo_path(selected)
        self.refresh_scope_ids()

    def refresh_scope_ids(self) -> None:
        repo_path = self.view.get_repo_path() or str(self.controller.root)
        choices = self.controller.available_scope_choices(repo_path)
        scope_type = self._scope_type()
        scope_ids = list(choices.epic_ids if scope_type is ScopeType.EPIC else choices.milestone_ids)
        self.view.set_scope_ids(scope_ids)
        self._update_action_states()

    def start(self) -> None:
        try:
            repo_path = self._repo_path()
            scope_type = self._scope_type()
            scope_id = self._scope_id()
            run_mode = self._run_mode()
            summary = StartSummary(
                repo_path=repo_path,
                scope_type=scope_type.value,
                scope_id=scope_id,
                run_mode=run_mode.value,
                create_draft_pr=self.view.get_create_draft_pr(),
                commit="YES",
                push="YES" if run_mode is RunMode.FULL else "NO",
                pr="YES" if (run_mode is RunMode.FULL and self.view.get_create_draft_pr()) else "NO",
            )
            if not self.view.confirm_start(summary):
                return
            self.view.clear_logs()
            self.controller.start_run(
                repo_path=repo_path,
                scope_type=scope_type,
                scope_id=scope_id,
                run_mode=run_mode,
                create_draft_pr=self.view.get_create_draft_pr(),
            )
            self._update_from_snapshot(self.controller.snapshot())
        except (AutopilotControllerError, ValueError) as exc:
            self.view.show_error("Start autopilot", str(exc))

    def resume(self) -> None:
        try:
            repo_path = self._repo_path()
            scope_type = self._scope_type()
            scope_id = self._scope_id()
            self.controller.resume_run(
                repo_path=repo_path,
                scope_type=scope_type,
                scope_id=scope_id,
                create_draft_pr=self.view.get_create_draft_pr(),
            )
            self._update_from_snapshot(self.controller.snapshot())
        except (AutopilotControllerError, ValueError) as exc:
            self.view.show_error("Resume autopilot", str(exc))

    def retry_push(self) -> None:
        run = self.controller.current_run()
        if run is None or run.status is not RunStatus.FAILED or run.request.run_mode is not RunMode.FULL:
            self.view.show_error("Retry push", "There is no failed push to retry.")
            return
        if not run.last_error or "push failed" not in run.last_error.lower():
            self.view.show_error("Retry push", "The current run is not in a push-failed state.")
            return
        try:
            repo_path = Path(run.request.repo_path).expanduser().resolve(strict=False)
            process_runner_fn = getattr(self.controller, "_run", None)
            config = getattr(self.controller, "config", None)
            repository = Repository(repo_path, process_runner_fn=process_runner_fn) if process_runner_fn is not None else Repository(repo_path)
            github = GitHubAdapter(repo_path, process_runner_fn=process_runner_fn) if process_runner_fn is not None else GitHubAdapter(repo_path)
            result = retry_push_pipeline(
                run,
                root=repo_path,
                config=config,
                repository=repository,
                github_adapter=github,
                process_runner_fn=process_runner_fn or process_runner.run_process,
            )
        except (AutopilotControllerError, ValueError, RuntimeError) as exc:
            self.view.show_error("Retry push", str(exc))
            return

        snapshot = replace(
            self.controller.snapshot(),
            status=result.status,
            running=False,
            branch_name=result.branch_name,
            epic_id=result.epic_id,
            current_task_id=result.run.current_task_id,
            progress=95 if result.status is RunStatus.WAITING_FOR_MERGE else 100,
            pull_request_number=result.pull_request.number if result.pull_request is not None else None,
            pull_request_url=result.pull_request.url if result.pull_request is not None else None,
            pull_request_title=result.pull_request.title if result.pull_request is not None else None,
            last_error=result.reason,
            message=result.reason or result.status.value,
        )
        setattr(self.controller, "_current_run", result.run)
        if hasattr(self.controller, "_snapshot"):
            self.controller._snapshot = snapshot  # type: ignore[attr-defined]
        if hasattr(self.controller, "snapshot_value"):
            self.controller.snapshot_value = snapshot  # type: ignore[attr-defined]
        self.view.set_snapshot(snapshot)
        self._update_action_states()

    def retry_current_task(self) -> None:
        run = self.controller.current_run()
        if run is None or run.status not in {RunStatus.FAILED, RunStatus.BLOCKED, RunStatus.CANCELLED} or not run.current_task_id:
            self.view.show_error("Retry current task", "There is no interrupted task to retry.")
            return
        try:
            repo_path = Path(run.request.repo_path).expanduser().resolve(strict=False)
            process_runner_fn = getattr(self.controller, "_run", None)
            config = getattr(self.controller, "config", None)
            repository = Repository(repo_path, process_runner_fn=process_runner_fn) if process_runner_fn is not None else Repository(repo_path)
            assessment = assess_task_recovery(run.current_task_id, run.run_id, repo_path, repository=repository)
            if assessment.unexpected_paths:
                proposal = load_scope_expansion_proposal(assessment.task_id, repo_path)
                action = self.view.confirm_scope_expansion(
                    ScopeExpansionSummary(
                        task_id=assessment.task_id,
                        state=assessment.current_state.value,
                        current_allowlist=assessment.allowed_paths,
                        unexpected_paths=assessment.unexpected_paths,
                        codex_summary=proposal.codex_summary if proposal is not None else assessment.reason,
                        suggested_metadata_change=build_suggested_metadata_change(assessment.allowed_paths, assessment.unexpected_paths),
                        reason=assessment.reason,
                        codex_skipped=assessment.can_resume,
                    )
                )
                if action == "copy":
                    return
                if action == "reject":
                    reject_scope_expansion_proposal(assessment.task_id, repo_path)
                    self.view.show_error("Retry current task", assessment.reason)
                    return
                if action != "retry":
                    return
            elif not self.view.confirm_retry_task(
                TaskRetrySummary(
                    task_id=assessment.task_id,
                    state=assessment.current_state.value,
                    allowed_dirty_paths=assessment.allowed_paths,
                    unexpected_paths=assessment.unexpected_paths,
                    codex_skipped=assessment.can_resume,
                    reason=assessment.reason,
                )
            ):
                return
            pipeline = TaskPipeline(repo_path, config=config, repository=repository, process_runner_fn=process_runner_fn or process_runner.run_process)
            result = pipeline.run_task(run, task_id=run.current_task_id)
        except (AutopilotControllerError, ValueError, RuntimeError) as exc:
            self.view.show_error("Retry current task", str(exc))
            return

        snapshot = replace(
            self.controller.snapshot(),
            status=result.status,
            running=False,
            branch_name=result.run.branch_name,
            epic_id=result.run.epic_id,
            current_task_id=result.run.current_task_id,
            progress=100 if result.status is RunStatus.COMPLETED else 100,
            pull_request_number=None,
            pull_request_url=None,
            pull_request_title=None,
            last_commit=result.task_result.commit_sha,
            last_error=result.reason,
            message=result.reason or result.status.value,
        )
        setattr(self.controller, "_current_run", result.run)
        if hasattr(self.controller, "_snapshot"):
            self.controller._snapshot = snapshot  # type: ignore[attr-defined]
        if hasattr(self.controller, "snapshot_value"):
            self.controller.snapshot_value = snapshot  # type: ignore[attr-defined]
        self.view.set_snapshot(snapshot)
        self._update_action_states()

    def stop(self) -> None:
        self.controller.stop()

    def open_pr(self) -> None:
        url = self.controller.latest_pr_url()
        if not url:
            self.view.show_info("Open PR", "No pull request is available yet.")
            return
        self.opener(url)

    def open_logs(self) -> None:
        self.folder_opener(str(self.controller.open_logs_path()))

    def close_requested(self) -> None:
        if self.controller.is_running():
            if not self.view.confirm_close_during_run():
                return
            self._closing_requested = True
            self.controller.stop()
            return
        self.root.destroy()

    def _schedule_poll(self) -> None:
        self._after_id = self.root.after(self.poll_interval_ms, self._poll_controller_events)

    def _poll_controller_events(self) -> None:
        for event in self.controller.poll_events():
            self._handle_event(event)
        if self._closing_requested and not self.controller.is_running():
            self.root.destroy()
            return
        self._update_action_states()
        self._schedule_poll()

    def _handle_event(self, event: ControllerEvent) -> None:
        if event.kind == "log":
            if event.message:
                self.view.append_log(event.message)
            if event.snapshot is not None:
                self._update_from_snapshot(event.snapshot)
            return
        if event.snapshot is not None:
            self._update_from_snapshot(event.snapshot)
        if event.kind in {"finished", "failed"}:
            self._update_action_states()
            snapshot_status = self.controller.snapshot().status
            if snapshot_status == RunStatus.COMPLETED:
                self.view.show_info("Autopilot", "Run completed.")
            elif snapshot_status == RunStatus.PAUSED:
                self.view.show_info("Autopilot", event.message or "Run paused.")
            elif snapshot_status == RunStatus.CANCELLED:
                self.view.show_info("Autopilot", "Run cancelled.")
            else:
                self.view.show_error("Autopilot", event.message or "Run failed.")
        elif event.kind == "finished" and event.message:
            self.view.show_info("Autopilot", event.message)

    def _update_from_snapshot(self, snapshot: ControllerSnapshot) -> None:
        self.view.set_snapshot(snapshot)
        self._update_action_states()

    def _update_action_states(self) -> None:
        snapshot = self.controller.snapshot()
        can_resume = (
            snapshot.scope_type is ScopeType.MILESTONE
            and snapshot.status is RunStatus.WAITING_FOR_MERGE
            and not self.controller.is_running()
        )
        can_open_pr = bool(snapshot.pull_request_url or self.controller.latest_pr_url())
        can_retry = self._can_retry_push()
        can_retry_task = (
            snapshot.current_task_id is not None
            and snapshot.status in {RunStatus.FAILED, RunStatus.BLOCKED, RunStatus.CANCELLED}
            and not self.controller.is_running()
        )
        self.view.set_action_states(
            busy=self.controller.is_running(),
            can_resume=can_resume,
            can_open_pr=can_open_pr,
            can_retry=can_retry,
            can_retry_task=can_retry_task,
        )

    def _repo_path(self) -> str:
        repo_path = self.view.get_repo_path().strip()
        if not repo_path:
            raise ValueError("repository path is required")
        return str(Path(repo_path).expanduser().resolve(strict=False))

    def _scope_type(self) -> ScopeType:
        return ScopeType(self.view.get_scope_type().strip().lower())

    def _scope_id(self) -> str:
        scope_id = self.view.get_scope_id().strip()
        if not scope_id:
            raise ValueError("scope id is required")
        return scope_id

    def _run_mode(self) -> RunMode:
        return RunMode(self.view.get_run_mode().strip().lower())

    def _can_retry_push(self) -> bool:
        run = self.controller.current_run()
        if run is None or run.request.run_mode is not RunMode.FULL:
            return False
        if run.status is not RunStatus.FAILED:
            return False
        if not run.branch_name or not run.task_results:
            return False
        if not run.last_error or "push failed" not in run.last_error.lower():
            return False
        return True


def _open_folder(path: str) -> None:
    if hasattr(os, "startfile"):
        os.startfile(path)  # type: ignore[attr-defined]
        return
    webbrowser.open(Path(path).resolve().as_uri())


def create_app(root: tk.Misc | None = None) -> LocalAutopilotUI:
    return LocalAutopilotUI(root=root)


def launch_app() -> int:
    app = create_app()
    app.root.mainloop()
    return 0


__all__ = [
    "LocalAutopilotUI",
    "StartSummary",
    "ScopeExpansionSummary",
    "TkAutopilotView",
    "ViewAdapter",
    "create_app",
    "launch_app",
]

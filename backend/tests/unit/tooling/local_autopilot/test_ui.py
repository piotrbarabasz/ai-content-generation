from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from app.tooling.local_autopilot.controller import ControllerEvent, ControllerSnapshot, ScopeChoices
from app.tooling.local_autopilot.models import AutopilotRequest, AutopilotRun, PullRequestInfo, RunMode, RunStatus, ScopeType, TaskResult
from app.tooling.local_autopilot.scope_proposal import build_scope_expansion_proposal, save_scope_expansion_proposal, load_scope_expansion_proposal
from app.tooling.local_autopilot.ui import LocalAutopilotUI, StartSummary, TaskRetrySummary


class FakeRoot:
    def __init__(self) -> None:
        self.after_calls: list[tuple[int, object]] = []
        self.protocols: dict[str, object] = {}
        self.destroyed = False

    def after(self, delay_ms: int, callback):
        self.after_calls.append((delay_ms, callback))
        return f"after-{len(self.after_calls)}"

    def protocol(self, name: str, callback) -> None:
        self.protocols[name] = callback

    def destroy(self) -> None:
        self.destroyed = True


class FakeView:
    def __init__(self) -> None:
        self.repo_path = "D:/Projects/ai-content-generation"
        self.scope_type = ScopeType.EPIC.value
        self.scope_id = "E001"
        self.run_mode = RunMode.FULL.value
        self.create_draft_pr = True
        self.scope_ids: list[str] = []
        self.logs: list[str] = []
        self.snapshots: list[ControllerSnapshot] = []
        self.action_states: list[tuple[bool, bool, bool, bool, bool]] = []
        self.confirm_start_results: list[bool] = [True]
        self.confirm_close_results: list[bool] = [True]
        self.confirm_retry_results: list[bool] = [True]
        self.confirm_scope_expansion_results: list[str] = ["retry"]
        self.info_messages: list[tuple[str, str]] = []
        self.error_messages: list[tuple[str, str]] = []
        self.confirm_start_summaries: list[StartSummary] = []
        self.confirm_retry_summaries: list[object] = []
        self.confirm_scope_expansion_summaries: list[object] = []
        self.cleared = 0

    def get_repo_path(self) -> str:
        return self.repo_path

    def set_repo_path(self, value: str) -> None:
        self.repo_path = value

    def get_scope_type(self) -> str:
        return self.scope_type

    def set_scope_type(self, value: str) -> None:
        self.scope_type = value

    def get_scope_id(self) -> str:
        return self.scope_id

    def set_scope_id(self, value: str) -> None:
        self.scope_id = value

    def set_scope_ids(self, values: list[str]) -> None:
        self.scope_ids = list(values)
        if values and self.scope_id not in values:
            self.scope_id = values[0]

    def get_run_mode(self) -> str:
        return self.run_mode

    def set_run_mode(self, value: str) -> None:
        self.run_mode = value

    def get_create_draft_pr(self) -> bool:
        return self.create_draft_pr

    def set_create_draft_pr(self, value: bool) -> None:
        self.create_draft_pr = bool(value)

    def set_action_states(self, *, busy: bool, can_resume: bool, can_open_pr: bool, can_retry: bool, can_retry_task: bool) -> None:
        self.action_states.append((busy, can_resume, can_open_pr, can_retry, can_retry_task))

    def set_snapshot(self, snapshot: ControllerSnapshot) -> None:
        self.snapshots.append(snapshot)

    def append_log(self, message: str) -> None:
        self.logs.append(message)

    def clear_logs(self) -> None:
        self.cleared += 1
        self.logs.clear()

    def confirm_start(self, summary: StartSummary) -> bool:
        self.confirm_start_summaries.append(summary)
        return self.confirm_start_results.pop(0)

    def confirm_retry_task(self, summary) -> bool:
        self.confirm_retry_summaries.append(summary)
        return self.confirm_retry_results.pop(0)

    def confirm_scope_expansion(self, summary):
        self.confirm_scope_expansion_summaries.append(summary)
        return self.confirm_scope_expansion_results.pop(0)

    def confirm_close_during_run(self) -> bool:
        return self.confirm_close_results.pop(0)

    def show_info(self, title: str, message: str) -> None:
        self.info_messages.append((title, message))

    def show_error(self, title: str, message: str) -> None:
        self.error_messages.append((title, message))


class FakeController:
    def __init__(self) -> None:
        self.running = False
        self.stop_called = False
        self.start_calls: list[dict[str, object]] = []
        self.resume_calls: list[dict[str, object]] = []
        self.events: list[ControllerEvent] = []
        self.snapshot_value = ControllerSnapshot(
            run_id=None,
            repo_path="D:/Projects/ai-content-generation",
            scope_type=None,
            scope_id=None,
            run_mode=None,
            create_draft_pr=True,
            running=False,
            status=RunStatus.IDLE,
        )
        self.pr_url = "https://example.invalid/pr/7"
        request = AutopilotRequest(
            scope_type=ScopeType.EPIC,
            scope_id="E001",
            run_mode=RunMode.FULL,
            repo_path="D:/Projects/ai-content-generation",
        )
        self.run = AutopilotRun(
            run_id="run-123",
            request=request,
            status=RunStatus.IDLE,
            created_at="2026-07-23T12:00:00Z",
            updated_at="2026-07-23T12:00:00Z",
        )
        self._current_run = self.run

    def available_scope_choices(self, repo_path):
        self.repo_path = repo_path
        return ScopeChoices(epic_ids=("E001", "E002"), milestone_ids=("M001", "M002"))

    def start_run(self, *, repo_path, scope_type, scope_id, run_mode, create_draft_pr):
        self.running = True
        self.start_calls.append(
            {
                "repo_path": repo_path,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "run_mode": run_mode,
                "create_draft_pr": create_draft_pr,
            }
        )
        self.snapshot_value = replace(
            self.snapshot_value,
            run_id="run-123",
            repo_path=str(repo_path),
            scope_type=scope_type,
            scope_id=scope_id,
            run_mode=run_mode,
            create_draft_pr=create_draft_pr,
            running=True,
            status=RunStatus.PREFLIGHT,
        )
        self._current_run = self.run
        return self.run

    def resume_run(self, *, repo_path, scope_type, scope_id, create_draft_pr=True):
        self.running = True
        self.resume_calls.append(
            {
                "repo_path": repo_path,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "create_draft_pr": create_draft_pr,
            }
        )
        self.snapshot_value = replace(self.snapshot_value, running=True, status=RunStatus.WAITING_FOR_MERGE, scope_type=scope_type, scope_id=scope_id)
        self._current_run = self.run
        return self.run

    def stop(self) -> bool:
        self.stop_called = True
        self.running = False
        return True

    def is_running(self) -> bool:
        return self.running

    def snapshot(self) -> ControllerSnapshot:
        return self.snapshot_value

    def current_run(self):
        return getattr(self, "_current_run", self.run)

    def poll_events(self) -> list[ControllerEvent]:
        events = list(self.events)
        self.events.clear()
        return events

    def latest_pr_url(self):
        return self.pr_url if self.snapshot_value.pull_request_url or self.pr_url else None

    def open_logs_path(self):
        return Path("D:/Projects/ai-content-generation/.specify/runtime/autopilot")


def test_refresh_scope_ids_and_selection_updates_view():
    root = FakeRoot()
    controller = FakeController()
    view = FakeView()

    app = LocalAutopilotUI(root=root, controller=controller, view=view, poll_interval_ms=10)

    assert view.scope_ids == ["E001", "E002"]

    view.scope_type = ScopeType.MILESTONE.value
    app.refresh_scope_ids()
    assert view.scope_ids == ["M001", "M002"]
    assert root.after_calls


def test_start_requires_confirmation_and_calls_controller():
    root = FakeRoot()
    controller = FakeController()
    view = FakeView()
    view.repo_path = "D:/Projects/ai-content-generation"
    view.scope_type = ScopeType.EPIC.value
    view.scope_id = "E002"
    view.run_mode = RunMode.STOP_BEFORE_PUSH.value
    view.create_draft_pr = False

    app = LocalAutopilotUI(root=root, controller=controller, view=view, poll_interval_ms=10)
    app.start()

    assert controller.start_calls[0]["scope_type"] is ScopeType.EPIC
    assert controller.start_calls[0]["scope_id"] == "E002"
    assert controller.start_calls[0]["run_mode"] is RunMode.STOP_BEFORE_PUSH
    assert controller.start_calls[0]["create_draft_pr"] is False
    expected_repo_path = str(Path("D:/Projects/ai-content-generation").resolve(strict=False))
    assert view.confirm_start_summaries[0] == StartSummary(
        repo_path=expected_repo_path,
        scope_type="epic",
        scope_id="E002",
        run_mode="stop_before_push",
        create_draft_pr=False,
        commit="YES",
        push="NO",
        pr="NO",
    )
    assert view.cleared == 1


def test_poll_events_updates_snapshot_logs_and_messages():
    root = FakeRoot()
    controller = FakeController()
    view = FakeView()
    app = LocalAutopilotUI(root=root, controller=controller, view=view, poll_interval_ms=10)

    snapshot = replace(
        controller.snapshot_value,
        status=RunStatus.COMPLETED,
        branch_name="feat/local-autopilot-ui",
        epic_id="E001",
        current_task_id="T001",
        progress=100,
        pull_request_number=7,
        pull_request_url="https://example.invalid/pr/7",
        pull_request_title="E001: launch",
        last_commit="a" * 40,
        running=False,
    )
    controller.snapshot_value = snapshot
    controller.events = [
        ControllerEvent(kind="log", message="hello", snapshot=snapshot),
        ControllerEvent(kind="finished", message="completed", snapshot=snapshot),
    ]

    app._poll_controller_events()

    assert view.logs[0] == "hello"
    assert view.snapshots[-1].status is RunStatus.COMPLETED
    assert view.info_messages[-1] == ("Autopilot", "Run completed.")


def test_close_during_run_requests_stop_instead_of_destroying():
    root = FakeRoot()
    controller = FakeController()
    controller.running = True
    controller.snapshot_value = replace(controller.snapshot_value, running=True, status=RunStatus.WAITING_FOR_MERGE)
    view = FakeView()
    app = LocalAutopilotUI(root=root, controller=controller, view=view, poll_interval_ms=10)

    app.close_requested()

    assert controller.stop_called is True
    assert root.destroyed is False


def test_retry_push_button_is_enabled_for_failed_push_runs():
    root = FakeRoot()
    controller = FakeController()
    controller.run = replace(
        controller.run,
        status=RunStatus.FAILED,
        branch_name="feature/E001",
        task_results=(
            TaskResult(
                task_id="T001",
                status=RunStatus.COMPLETED,
                commit_sha="2" * 40,
                title="Task 1",
            ),
        ),
        last_error="push failed: exit_code=1; remote rejected branch",
    )
    controller._current_run = controller.run
    controller.snapshot_value = replace(controller.snapshot_value, status=RunStatus.FAILED, running=False)
    view = FakeView()
    app = LocalAutopilotUI(root=root, controller=controller, view=view, poll_interval_ms=10)

    assert view.action_states[-1] == (False, False, True, True, False)


def test_retry_push_updates_snapshot_after_success(monkeypatch):
    root = FakeRoot()
    controller = FakeController()
    controller.run = replace(
        controller.run,
        status=RunStatus.FAILED,
        branch_name="feature/E001",
        task_results=(
            TaskResult(
                task_id="T001",
                status=RunStatus.COMPLETED,
                commit_sha="2" * 40,
                title="Task 1",
            ),
        ),
        last_error="push failed: exit_code=1; remote rejected branch",
    )
    controller._current_run = controller.run
    controller.snapshot_value = replace(controller.snapshot_value, status=RunStatus.FAILED, running=False)
    view = FakeView()

    def fake_retry_push_pipeline(run, **kwargs):
        return SimpleNamespace(
            status=RunStatus.WAITING_FOR_MERGE,
            run=replace(run, status=RunStatus.WAITING_FOR_MERGE, pull_request=PullRequestInfo(number=17, url="https://example.invalid/pr/17", title="PR 17", base_branch="master", head_branch="feature/E001")),
            branch_name="feature/E001",
            epic_id="E001",
            pull_request=PullRequestInfo(number=17, url="https://example.invalid/pr/17", title="PR 17", base_branch="master", head_branch="feature/E001"),
            reason=None,
        )

    monkeypatch.setattr("app.tooling.local_autopilot.ui.retry_push_pipeline", fake_retry_push_pipeline)
    app = LocalAutopilotUI(root=root, controller=controller, view=view, poll_interval_ms=10)

    app.retry_push()

    assert view.snapshots[-1].status is RunStatus.WAITING_FOR_MERGE
    assert controller.snapshot_value.status is RunStatus.WAITING_FOR_MERGE
    assert controller.current_run().status is RunStatus.WAITING_FOR_MERGE
    assert not view.error_messages


def test_retry_current_task_shows_assessment_and_updates_snapshot(monkeypatch):
    root = FakeRoot()
    controller = FakeController()
    controller.run = replace(
        controller.run,
        status=RunStatus.FAILED,
        current_task_id="T045",
        branch_name="feat/local-autopilot-ui",
        last_error="task failed",
    )
    controller._current_run = controller.run
    controller.snapshot_value = replace(
        controller.snapshot_value,
        status=RunStatus.FAILED,
        current_task_id="T045",
        branch_name="feat/local-autopilot-ui",
        running=False,
    )
    view = FakeView()

    assessment = SimpleNamespace(
        task_id="T045",
        current_state=SimpleNamespace(value="FAILED"),
        allowed_paths=("backend/app/tooling/local_autopilot/task_pipeline.py",),
        unexpected_paths=(),
        can_resume=True,
        reason="task T045 can resume from terminal state FAILED",
    )
    monkeypatch.setattr("app.tooling.local_autopilot.ui.assess_task_recovery", lambda *args, **kwargs: assessment)

    class FakeTaskPipeline:
        def __init__(self, *args, **kwargs):
            self.calls: list[tuple[str, str]] = []

        def run_task(self, run, *, task_id, cancel_event=None):
            self.calls.append((run.run_id, task_id))
            task_result = TaskResult(task_id=task_id, status=RunStatus.COMPLETED, commit_sha="b" * 40, title="Task 45")
            updated_run = replace(run, status=RunStatus.COMPLETED, current_task_id=task_id, task_results=(task_result,))
            return SimpleNamespace(
                status=RunStatus.COMPLETED,
                run=updated_run,
                task_result=task_result,
                attempts=1,
                baseline_path="baseline.json",
                allowlist=("backend/app/tooling/local_autopilot/task_pipeline.py",),
                validation_commands=("python -m pytest",),
                command_results=(),
                reason=None,
            )

    monkeypatch.setattr("app.tooling.local_autopilot.ui.TaskPipeline", FakeTaskPipeline)
    app = LocalAutopilotUI(root=root, controller=controller, view=view, poll_interval_ms=10)

    app.retry_current_task()

    assert isinstance(view.confirm_retry_summaries[-1], TaskRetrySummary)
    assert view.confirm_retry_summaries[-1].codex_skipped is True
    assert view.snapshots[-1].status is RunStatus.COMPLETED
    assert controller.current_run().status is RunStatus.COMPLETED
    assert controller.current_run().current_task_id == "T045"
    assert not view.error_messages


def test_retry_current_task_shows_scope_expansion_dialog_and_marks_rejected(monkeypatch, tmp_path):
    root = FakeRoot()
    controller = FakeController()
    controller.run = AutopilotRun(
        run_id="run-123",
        request=AutopilotRequest(
            scope_type=ScopeType.EPIC,
            scope_id="E001",
            run_mode=RunMode.FULL,
            repo_path=str(tmp_path),
        ),
        status=RunStatus.BLOCKED,
        created_at="2026-07-23T12:00:00Z",
        updated_at="2026-07-23T12:00:00Z",
        epic_id="E001",
        branch_name="feat/local-autopilot-ui",
        current_task_id="T045",
        last_error="scope expansion approval required",
    )
    controller._current_run = controller.run
    controller.snapshot_value = replace(
        controller.snapshot_value,
        repo_path=str(tmp_path),
        status=RunStatus.BLOCKED,
        current_task_id="T045",
        branch_name="feat/local-autopilot-ui",
        running=False,
    )
    view = FakeView()
    view.confirm_scope_expansion_results = ["reject"]

    assessment = SimpleNamespace(
        task_id="T045",
        current_state=SimpleNamespace(value="BLOCKED"),
        allowed_paths=("backend/app/tooling/local_autopilot/task_pipeline.py",),
        unexpected_paths=("backend/other.py",),
        can_resume=False,
        reason="scope expansion approval required for T045",
    )
    monkeypatch.setattr("app.tooling.local_autopilot.ui.assess_task_recovery", lambda *args, **kwargs: assessment)

    proposal = build_scope_expansion_proposal(
        proposal_id="T045-run-123",
        run_id="run-123",
        task_id="T045",
        epic_id="E001",
        branch="feat/local-autopilot-ui",
        head_sha="a" * 40,
        baseline_head_sha="b" * 40,
        current_allowlist=("backend/app/tooling/local_autopilot/task_pipeline.py",),
        files_touched=("backend/app/tooling/local_autopilot/task_pipeline.py",),
        unexpected_paths=("backend/other.py",),
        codex_summary="Implementation complete.",
        codex_notes=("Touched outside scope",),
        created_at="2026-07-29T12:00:00Z",
    )
    save_scope_expansion_proposal(proposal, root=tmp_path)

    class FakeTaskPipeline:
        def __init__(self, *args, **kwargs):
            raise AssertionError("task pipeline should not run when changes are rejected")

    monkeypatch.setattr("app.tooling.local_autopilot.ui.TaskPipeline", FakeTaskPipeline)
    app = LocalAutopilotUI(root=root, controller=controller, view=view, poll_interval_ms=10)

    app.retry_current_task()

    assert view.confirm_scope_expansion_summaries[-1].unexpected_paths == ("backend/other.py",)
    assert view.confirm_scope_expansion_summaries[-1].current_allowlist == ("backend/app/tooling/local_autopilot/task_pipeline.py",)
    assert view.confirm_scope_expansion_summaries[-1].codex_summary == "Implementation complete."
    assert load_scope_expansion_proposal("T045", root=tmp_path).status == "rejected"
    assert view.error_messages[-1][0] == "Retry current task"
    assert "scope expansion approval required" in view.error_messages[-1][1]

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.tooling.local_autopilot.controller import ControllerEvent, ControllerSnapshot, ScopeChoices
from app.tooling.local_autopilot.models import AutopilotRequest, AutopilotRun, PullRequestInfo, RunMode, RunStatus, ScopeType
from app.tooling.local_autopilot.ui import LocalAutopilotUI, StartSummary


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
        self.action_states: list[tuple[bool, bool, bool]] = []
        self.confirm_start_results: list[bool] = [True]
        self.confirm_close_results: list[bool] = [True]
        self.info_messages: list[tuple[str, str]] = []
        self.error_messages: list[tuple[str, str]] = []
        self.confirm_start_summaries: list[StartSummary] = []
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

    def set_action_states(self, *, busy: bool, can_resume: bool, can_open_pr: bool) -> None:
        self.action_states.append((busy, can_resume, can_open_pr))

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
        return self.run

    def stop(self) -> bool:
        self.stop_called = True
        self.running = False
        return True

    def is_running(self) -> bool:
        return self.running

    def snapshot(self) -> ControllerSnapshot:
        return self.snapshot_value

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

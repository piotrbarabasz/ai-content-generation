from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.tooling.local_autopilot.epic_pipeline import EpicPipeline, EpicPipelineResult, run_epic_pipeline
from app.tooling.local_autopilot.github_adapter import GitHubAuthResult
from app.tooling.local_autopilot.models import (
    AutopilotRequest,
    AutopilotRun,
    CommandResult,
    PullRequestInfo,
    RunMode,
    RunStatus,
    ScopeType,
    TaskResult,
)
from app.tooling.local_autopilot.process_runner import ProcessResult
from app.tooling.local_autopilot.task_pipeline import TaskPipelineResult


@dataclass
class FakeRepository:
    root: Path
    current_branch: str = "master"
    head_sha_value: str = "a" * 40
    clean: bool = True
    commit_should_fail: bool = False
    push_should_fail: bool = False

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.commit_messages: list[str] = []
        self.pushed_branches: list[str] = []
        self.created_branches: list[tuple[str, str]] = []
        self.staged_paths: list[tuple[str, ...]] = []
        self.diff_checks: list[bool] = []
        self.require_clean_tree_calls = 0
        self._commit_index = 0

    def require_clean_tree(self) -> None:
        self.require_clean_tree_calls += 1
        self.calls.append(("require_clean_tree",))
        if not self.clean:
            raise RuntimeError("working tree must be clean")

    def switch_to_master_and_pull(self, base_branch: str = "master", remote: str = "origin") -> None:
        self.calls.append(("switch_to_master_and_pull", base_branch, remote))
        self.current_branch = base_branch

    def create_branch(self, branch: str, *, base_branch: str = "master") -> None:
        self.calls.append(("create_branch", branch, base_branch))
        self.current_branch = branch

    def stage_allowlist(self, allowlist) -> None:
        values = tuple(str(item) for item in allowlist)
        self.calls.append(("stage_allowlist", *values))
        self.staged_paths.append(values)

    def commit(self, message: str) -> ProcessResult:
        self.calls.append(("commit", message))
        self.commit_messages.append(message)
        if self.commit_should_fail:
            return self._result(("git", "commit", "-m", message), status="FAIL", exit_code=1)
        self._commit_index += 1
        self.head_sha_value = f"{self._commit_index:040x}"[-40:]
        self.clean = True
        return self._result(("git", "commit", "-m", message))

    def push(self, branch: str, remote: str = "origin") -> ProcessResult:
        self.calls.append(("push", branch, remote))
        self.pushed_branches.append(branch)
        if self.push_should_fail:
            return self._result(("git", "push", "-u", remote, branch), status="FAIL", exit_code=1)
        return self._result(("git", "push", "-u", remote, branch))

    def diff_check(self, *, cached: bool = False) -> ProcessResult:
        self.calls.append(("diff_check", "cached" if cached else "worktree"))
        self.diff_checks.append(cached)
        return self._result(("git", "--no-pager", "diff", "--cached", "--check") if cached else ("git", "--no-pager", "diff", "--check"))

    def head_sha(self) -> str:
        self.calls.append(("head_sha",))
        return self.head_sha_value

    def status(self):
        return type(
            "Status",
            (),
            {
                "branch": self.current_branch,
                "head_sha": self.head_sha_value,
                "tracked": (),
                "staged": (),
                "untracked": (),
                "deleted": (),
                "renamed": (),
                "clean": self.clean,
            },
        )()

    def _result(
        self,
        command: tuple[str, ...],
        *,
        status: str = "PASS",
        exit_code: int | None = 0,
    ) -> ProcessResult:
        return ProcessResult(
            command=command,
            status=status,
            exit_code=exit_code,
            duration_ms=1,
            timed_out=False,
            cancelled=False,
            stdout_lines=(),
            stderr_lines=(),
            output_truncated=False,
            process_tree_killed=False,
            pid=4321,
        )


class FakeGitHubAdapter:
    def __init__(
        self,
        *,
        auth_available: bool = True,
        auth_authenticated: bool = True,
        existing_pr: PullRequestInfo | None = None,
        created_pr: PullRequestInfo | None = None,
    ) -> None:
        self.auth_available = auth_available
        self.auth_authenticated = auth_authenticated
        self.existing_pr = existing_pr
        self.created_pr = created_pr
        self.calls: list[tuple[str, ...]] = []

    def validate_auth(self, *, timeout_seconds: int = 20) -> GitHubAuthResult:
        self.calls.append(("validate_auth", str(timeout_seconds)))
        return GitHubAuthResult(
            available=self.auth_available,
            authenticated=self.auth_authenticated,
            command=("gh", "auth", "status"),
            status="PASS" if self.auth_available and self.auth_authenticated else "FAIL",
            exit_code=0 if self.auth_available and self.auth_authenticated else 1,
            reason=None if self.auth_available and self.auth_authenticated else "gh auth failed",
        )

    def create_draft_pr(self, base: str, head: str, title: str, body: str, *, timeout_seconds: int = 120) -> PullRequestInfo:
        self.calls.append(("create_draft_pr", base, head, title))
        if self.existing_pr is not None:
            return self.existing_pr
        if self.created_pr is not None:
            return self.created_pr
        return PullRequestInfo(
            number=99,
            url="https://example.invalid/pr/99",
            title=title,
            base_branch=base,
            head_branch=head,
            draft=True,
            merged=False,
        )


class FakeTaskPipeline:
    def __init__(
        self,
        root: Path,
        repo: FakeRepository,
        *,
        outcomes: dict[str, dict[str, object]],
    ) -> None:
        self.root = root
        self.repo = repo
        self.outcomes = outcomes
        self.calls: list[str] = []

    def run_task(self, run: AutopilotRun, *, task_id: str, cancel_event=None) -> TaskPipelineResult:
        self.calls.append(task_id)
        outcome = self.outcomes[task_id]
        status = outcome.get("status", RunStatus.COMPLETED)
        commit_sha = str(outcome.get("commit_sha") or f"{len(self.calls):040x}"[-40:])
        title = str(outcome.get("title") or f"Task {task_id}")
        allowlist = tuple(outcome.get("allowlist") or ("backend/app/tooling/local_autopilot/epic_pipeline.py",))
        validation_commands = tuple(outcome.get("validation_commands") or ("python -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py",))
        if status == RunStatus.COMPLETED:
            self._mark_complete(task_id)
            self.repo.head_sha_value = commit_sha
            self.repo.clean = True
        else:
            self.repo.clean = True
        task_result = TaskResult(
            task_id=task_id,
            status=status,
            command_results=(
                CommandResult(
                    command=("git", "commit", "-m", f"feat({task_id}): {title}"),
                    status="PASS" if status == RunStatus.COMPLETED else "FAIL",
                    exit_code=0 if status == RunStatus.COMPLETED else 1,
                    duration_ms=1,
                    timed_out=False,
                ),
            ),
            commit_sha=commit_sha if status == RunStatus.COMPLETED else None,
            title=title,
        )
        updated_run = AutopilotRun(
            run_id=run.run_id,
            request=run.request,
            status=status,
            created_at=run.created_at,
            updated_at=run.updated_at,
            epic_id=run.epic_id,
            milestone_id=run.milestone_id,
            branch_name=run.branch_name,
            current_task_id=task_id,
            task_results=tuple([*run.task_results, task_result]),
            command_results=tuple(run.command_results),
            pull_request=run.pull_request,
            last_error=outcome.get("reason") if status != RunStatus.COMPLETED else None,
        )
        return TaskPipelineResult(
            status=status,
            run=updated_run,
            task_result=task_result,
            attempts=1,
            baseline_path=str(self.root / ".specify" / "runtime" / "task-runs" / "T045" / "baseline.json"),
            allowlist=allowlist,
            validation_commands=validation_commands,
            command_results=task_result.command_results,
            reason=outcome.get("reason"),
        )

    def _mark_complete(self, task_id: str) -> None:
        tasks_path = self.root / "specs" / "001-ai-content-studio" / "tasks.md"
        text = tasks_path.read_text(encoding="utf-8")
        text = text.replace(f"- [ ] {task_id}", f"- [X] {task_id}", 1)
        tasks_path.write_text(text, encoding="utf-8")


class FakeReviewReceipt:
    def __init__(self, root: Path, *, validator_errors: list[str] | None = None) -> None:
        self.root = root
        self.validator_errors = validator_errors or []
        self.writes: list[dict[str, object]] = []
        self.validations: list[dict[str, object]] = []

    def write(self, **kwargs):
        self.writes.append(kwargs)
        path = self.root / ".specify" / "runtime" / "reviews" / f"{kwargs['epic_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"epic_id": kwargs["epic_id"], "verdict": "PASS"}), encoding="utf-8")
        return path

    def validate(self, path: Path, **kwargs):
        self.validations.append({"path": path, **kwargs})
        return list(self.validator_errors)


class FakeProcessRunner:
    def __init__(self, repo: FakeRepository, *, python_executable: str = r"D:\Projects\ai-content-generation\.venv\Scripts\python.exe", base_sha: str = "b" * 40) -> None:
        self.repo = repo
        self.python_executable = python_executable
        self.base_sha = base_sha
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, **kwargs):
        command = tuple(str(part) for part in argv)
        self.calls.append(command)
        if command == ("git", "config", "--local", "--get", "agent.python"):
            return self._result(command, stdout=(self.python_executable,))
        if command == ("git", "rev-parse", "HEAD"):
            return self._result(command, stdout=(self.repo.head_sha(),))
        if command and command[0:2] == ("git", "rev-parse"):
            return self._result(command, stdout=(self.base_sha,))
        if command[:2] == (self.python_executable, "-m"):
            return self._result(command, stdout=("ok",))
        return self._result(command)

    def _result(self, command: tuple[str, ...], *, stdout: tuple[str, ...] = (), status: str = "PASS", exit_code: int | None = 0) -> ProcessResult:
        return ProcessResult(
            command=command,
            status=status,
            exit_code=exit_code,
            duration_ms=1,
            timed_out=False,
            cancelled=False,
            stdout_lines=stdout,
            stderr_lines=(),
            output_truncated=False,
            process_tree_killed=False,
            pid=1234,
        )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not text.endswith("\n"):
        text = f"{text}\n"
    path.write_text(text, encoding="utf-8")


def _setup_repo(
    tmp_path: Path,
    *,
    epic_status: str = "planned",
    dependency_status: str = "completed",
    task7_checked: bool = False,
    task8_checked: bool = False,
) -> tuple[Path, Path]:
    workstreams = tmp_path / ".specify" / "workstreams"
    runtime = tmp_path / ".specify" / "runtime" / "task-runs" / "T045"
    feature_dir = tmp_path / "specs" / "001-ai-content-studio"
    workstreams.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    feature_dir.mkdir(parents=True, exist_ok=True)

    _write(
        workstreams / "M001.yml",
        "\n".join(
            [
                "id: M001",
                "title: Milestone M001",
                "status: active",
                "goal: goal",
                "epics:",
                "  - E001",
                "  - E002",
                "completion_criteria:",
                "  - Tests pass",
                "",
            ]
        ),
    )
    _write(
        workstreams / "E001.yml",
        "\n".join(
            [
                "id: E001",
                "title: Epic E001",
                "milestone: M001",
                "feature: specs/001-ai-content-studio",
                "base_branch: master",
                "branch: epic/E001",
                f"status: {dependency_status}",
                "risk: medium",
                "depends_on: []",
                "tasks:",
                "  - T001",
                "",
            ]
        ),
    )
    _write(
        workstreams / "E002.yml",
        "\n".join(
            [
                "id: E002",
                "title: Epic E002",
                "milestone: M001",
                "feature: specs/001-ai-content-studio",
                "base_branch: master",
                "branch: feature/E002",
                f"status: {epic_status}",
                "risk: medium",
                "depends_on:",
                "  - E001",
                "tasks:",
                "  - T007",
                "  - T008",
                "required_checks:",
                "  - python -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py",
                "  - git --no-pager diff --check",
                "",
            ]
        ),
    )
    _write(
        feature_dir / "tasks.md",
        "\n".join(
            [
                f"- [{'X' if task7_checked else ' '}] T007 Implement epic task 1",
                "Milestone: M001",
                "Epic: E002",
                "Risk: medium",
                "Implementation files: backend/app/tooling/local_autopilot/task_pipeline.py",
                "Test files: backend/tests/unit/tooling/local_autopilot/test_task_pipeline.py",
                "Validation commands: python -m pytest backend/tests/unit/tooling/local_autopilot/test_task_pipeline.py",
                "Acceptance criteria: done",
                "Dependencies: None",
                "",
                f"- [{'X' if task8_checked else ' '}] T008 Implement epic task 2",
                "Milestone: M001",
                "Epic: E002",
                "Risk: medium",
                "Implementation files: backend/app/tooling/local_autopilot/task_pipeline.py",
                "Test files: backend/tests/unit/tooling/local_autopilot/test_task_pipeline.py",
                "Validation commands: python -m pytest backend/tests/unit/tooling/local_autopilot/test_task_pipeline.py",
                "Acceptance criteria: done",
                "Dependencies: T007",
                "",
            ]
        ),
    )
    _write(
        runtime / "baseline.json",
        json.dumps(
            {
                "schema_version": 1,
                "task": "T045",
                "epic": "E002",
                "branch": "feature/E002",
                "head_sha": "a" * 40,
                "tracked": [],
                "staged": [],
                "untracked": [],
                "deleted": [],
                "renamed": [],
            },
            indent=2,
        ),
    )
    (tmp_path / ".specify" / "runtime" / "active-epic").write_text("E002\n", encoding="utf-8")
    return workstreams / "E002.yml", feature_dir / "tasks.md"


def _make_run(tmp_path: Path, *, run_mode: RunMode = RunMode.STOP_BEFORE_PUSH) -> AutopilotRun:
    request = AutopilotRequest(
        scope_type=ScopeType.EPIC,
        scope_id="E002",
        run_mode=run_mode,
        repo_path=str(tmp_path),
    )
    return AutopilotRun(
        run_id="run-epic-002",
        request=request,
        status=RunStatus.PREFLIGHT,
        created_at="2026-07-23T12:00:00Z",
        updated_at="2026-07-23T12:00:00Z",
        epic_id="E002",
        branch_name="feature/E002",
    )


def _build_pipeline(
    tmp_path: Path,
    repo: FakeRepository,
    github: FakeGitHubAdapter,
    receipt: FakeReviewReceipt,
    task_outcomes: dict[str, dict[str, object]],
    *,
    base_sha: str = "b" * 40,
) -> tuple[EpicPipeline, FakeTaskPipeline, FakeProcessRunner]:
    process = FakeProcessRunner(repo, base_sha=base_sha)
    task_factory_calls: list[tuple[Path, AutopilotConfig]] = []

    def factory(root: Path, config: AutopilotConfig, process_runner_fn):
        task_factory_calls.append((root, config))
        return FakeTaskPipeline(root, repo, outcomes=task_outcomes)

    pipeline = EpicPipeline(
        tmp_path,
        repository=repo,
        task_pipeline_factory=factory,
        github_adapter=github,
        process_runner_fn=process,
        review_receipt_writer=receipt.write,
        review_receipt_validator=receipt.validate,
    )
    task_pipeline = factory(tmp_path, pipeline.config, process)
    return pipeline, task_pipeline, process


def test_run_epic_happy_path_stop_before_push_activates_branch_and_completes(tmp_path):
    manifest_path, tasks_file = _setup_repo(tmp_path, epic_status="planned", dependency_status="completed")
    repo = FakeRepository(tmp_path)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {
            "T007": {"status": RunStatus.COMPLETED, "commit_sha": "1" * 40, "title": "Task 7"},
            "T008": {"status": RunStatus.COMPLETED, "commit_sha": "2" * 40, "title": "Task 8"},
        },
    )

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH), human_authorized=True)

    assert isinstance(result, EpicPipelineResult)
    assert result.status == RunStatus.COMPLETED
    assert result.task_ids == ("T007", "T008")
    assert task_pipeline.calls == []
    assert repo.commit_messages[0] == "feat(E002): activate epic"
    assert repo.current_branch == "feature/E002"
    assert (tmp_path / ".specify" / "runtime" / "active-epic").read_text(encoding="utf-8").strip() == "E002"
    assert "status: active" in manifest_path.read_text(encoding="utf-8")
    assert receipt.writes and receipt.validations
    assert not repo.pushed_branches
    assert not github.calls
    assert result.pull_request is None


def test_run_epic_resumes_from_next_ready_task(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="a" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {"T008": {"status": RunStatus.COMPLETED, "commit_sha": "2" * 40, "title": "Task 8"}},
    )

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH))

    assert result.status == RunStatus.COMPLETED
    assert task_pipeline.calls == []


def test_run_epic_dependency_failure_blocks_before_tasks(tmp_path):
    _setup_repo(tmp_path, epic_status="planned", dependency_status="planned")
    repo = FakeRepository(tmp_path)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {"T007": {"status": RunStatus.COMPLETED, "commit_sha": "1" * 40, "title": "Task 7"}},
    )

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH), human_authorized=True)

    assert result.status == RunStatus.FAILED
    assert "dependency" in (result.reason or "").lower()
    assert task_pipeline.calls == []
    assert not repo.commit_messages


def test_run_epic_task_failure_stops_after_current_task(tmp_path):
    _setup_repo(tmp_path, epic_status="planned", dependency_status="completed")
    repo = FakeRepository(tmp_path)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {
            "T007": {"status": RunStatus.FAILED, "reason": "task failed"},
            "T008": {"status": RunStatus.COMPLETED, "commit_sha": "2" * 40, "title": "Task 8"},
        },
    )

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH), human_authorized=True)

    assert result.status == RunStatus.FAILED
    assert task_pipeline.calls == []
    assert "task failed" in (result.reason or "").lower()
    assert not receipt.writes


def test_run_epic_review_failure_blocks_push(tmp_path):
    _setup_repo(tmp_path, epic_status="planned", dependency_status="completed")
    repo = FakeRepository(tmp_path)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path, validator_errors=["review failed"])
    pipeline, task_pipeline, _ = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {
            "T007": {"status": RunStatus.COMPLETED, "commit_sha": "1" * 40, "title": "Task 7"},
            "T008": {"status": RunStatus.COMPLETED, "commit_sha": "2" * 40, "title": "Task 8"},
        },
    )

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)

    assert result.status == RunStatus.FAILED
    assert task_pipeline.calls == []
    assert not repo.pushed_branches
    assert not github.calls
    assert "review failed" in (result.reason or "")


def test_run_epic_stop_before_push_ends_without_push_or_pr(tmp_path):
    _setup_repo(tmp_path, epic_status="planned", dependency_status="completed")
    repo = FakeRepository(tmp_path)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {
            "T007": {"status": RunStatus.COMPLETED, "commit_sha": "1" * 40, "title": "Task 7"},
            "T008": {"status": RunStatus.COMPLETED, "commit_sha": "2" * 40, "title": "Task 8"},
        },
    )

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH), human_authorized=True)

    assert result.status == RunStatus.COMPLETED
    assert repo.pushed_branches == []
    assert github.calls == []
    assert result.pull_request is None


def test_run_epic_push_failure_blocks_pr_creation(tmp_path):
    _setup_repo(tmp_path, epic_status="planned", dependency_status="completed")
    repo = FakeRepository(tmp_path, push_should_fail=True)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {
            "T007": {"status": RunStatus.COMPLETED, "commit_sha": "1" * 40, "title": "Task 7"},
            "T008": {"status": RunStatus.COMPLETED, "commit_sha": "2" * 40, "title": "Task 8"},
        },
    )

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)

    assert result.status == RunStatus.FAILED
    assert repo.pushed_branches == ["feature/E002"]
    assert github.calls == [("validate_auth", "180")]
    assert "push failed" in (result.reason or "")


def test_run_epic_reuses_existing_pr(tmp_path):
    _setup_repo(tmp_path, epic_status="planned", dependency_status="completed")
    repo = FakeRepository(tmp_path)
    existing_pr = PullRequestInfo(
        number=17,
        url="https://example.invalid/pr/17",
        title="E002: Epic E002",
        base_branch="master",
        head_branch="feature/E002",
    )
    github = FakeGitHubAdapter(existing_pr=existing_pr)
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, _, _ = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {
            "T007": {"status": RunStatus.COMPLETED, "commit_sha": "1" * 40, "title": "Task 7"},
            "T008": {"status": RunStatus.COMPLETED, "commit_sha": "2" * 40, "title": "Task 8"},
        },
    )

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)

    assert result.status == RunStatus.WAITING_FOR_MERGE
    assert result.pull_request == existing_pr
    assert github.calls[-1][:2] == ("create_draft_pr", "master")


def test_run_epic_creates_new_pr_when_none_exists(tmp_path):
    _setup_repo(tmp_path, epic_status="planned", dependency_status="completed")
    repo = FakeRepository(tmp_path)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, _, _ = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {
            "T007": {"status": RunStatus.COMPLETED, "commit_sha": "1" * 40, "title": "Task 7"},
            "T008": {"status": RunStatus.COMPLETED, "commit_sha": "2" * 40, "title": "Task 8"},
        },
    )

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)

    assert result.status == RunStatus.WAITING_FOR_MERGE
    assert result.pull_request is not None
    assert result.pull_request.number == 99
    assert github.calls[-1][:2] == ("create_draft_pr", "master")


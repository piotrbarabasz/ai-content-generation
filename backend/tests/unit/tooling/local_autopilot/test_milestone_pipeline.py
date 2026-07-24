from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.tooling.local_autopilot.milestone_pipeline import MilestonePipeline, MilestonePipelineResult, run_milestone_pipeline
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
from app.tooling.local_autopilot.state_store import load_run_state
from app.tooling.local_autopilot.epic_pipeline import EpicPipelineResult
from app.tooling.local_autopilot import workstreams


@dataclass
class FakeRepository:
    root: Path
    current_branch: str = "master"
    head_sha_value: str = "a" * 40
    clean: bool = True
    push_should_fail: bool = False

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.commit_messages: list[str] = []
        self.pushed_branches: list[str] = []
        self.created_branches: list[tuple[str, str]] = []
        self.staged_paths: list[tuple[str, ...]] = []
        self._commit_index = 0

    def require_clean_tree(self) -> None:
        self.calls.append(("require_clean_tree",))
        if not self.clean:
            raise RuntimeError("working tree must be clean")

    def switch_to_master_and_pull(self, base_branch: str = "master", remote: str = "origin") -> None:
        self.calls.append(("switch_to_master_and_pull", base_branch, remote))
        self.current_branch = base_branch

    def create_branch(self, branch: str, *, base_branch: str = "master") -> None:
        self.calls.append(("create_branch", branch, base_branch))
        self.current_branch = branch
        self.created_branches.append((branch, base_branch))

    def stage_allowlist(self, allowlist) -> None:
        values = tuple(str(item) for item in allowlist)
        self.calls.append(("stage_allowlist", *values))
        self.staged_paths.append(values)

    def commit(self, message: str) -> ProcessResult:
        self.calls.append(("commit", message))
        self.commit_messages.append(message)
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
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._next_number = 101
        self._prs: dict[tuple[str, str], PullRequestInfo] = {}

    def validate_auth(self, *, timeout_seconds: int = 20) -> GitHubAuthResult:
        self.calls.append(("validate_auth", str(timeout_seconds)))
        return GitHubAuthResult(
            available=True,
            authenticated=True,
            command=("gh", "auth", "status"),
            status="PASS",
            exit_code=0,
        )

    def create_draft_pr(self, base: str, head: str, title: str, body: str, *, timeout_seconds: int = 120) -> PullRequestInfo:
        self.calls.append(("create_draft_pr", base, head, title))
        key = (base, head)
        if key not in self._prs:
            self._prs[key] = PullRequestInfo(
                number=self._next_number,
                url=f"https://example.invalid/pr/{self._next_number}",
                title=title,
                base_branch=base,
                head_branch=head,
                draft=True,
                merged=False,
            )
            self._next_number += 1
        return self._prs[key]


class FakeProcessRunner:
    def __init__(self, repo: FakeRepository, *, python_executable: str = r"D:\Projects\ai-content-generation\.venv\Scripts\python.exe") -> None:
        self.repo = repo
        self.python_executable = python_executable
        self.calls: list[tuple[str, ...]] = []
        self.pr_metadata: dict[int, dict[str, object]] = {}

    def set_pr_metadata(
        self,
        number: int,
        *,
        state: str,
        merged_at: str | None,
        head_ref_name: str,
        base_ref_name: str = "master",
        head_ref_oid: str | None = None,
        base_ref_oid: str | None = None,
    ) -> None:
        self.pr_metadata[number] = {
            "number": number,
            "url": f"https://example.invalid/pr/{number}",
            "title": f"PR {number}",
            "baseRefName": base_ref_name,
            "headRefName": head_ref_name,
            "isDraft": True,
            "state": state,
            "mergedAt": merged_at,
            "headRefOid": head_ref_oid or ("c" * 40),
            "baseRefOid": base_ref_oid or ("b" * 40),
        }

    def __call__(self, argv, **kwargs):
        command = tuple(str(part) for part in argv)
        self.calls.append(command)
        if command == ("git", "config", "--local", "--get", "agent.python"):
            return self._result(command, stdout=(self.python_executable,))
        if command == ("gh", "auth", "status"):
            return self._result(command, stdout=("github.com", "logged in as tester"))
        if command[:3] == ("gh", "pr", "view"):
            number = int(command[3])
            payload = self.pr_metadata.get(number)
            if payload is None:
                return self._result(command, status="FAIL", exit_code=1, stdout=("not found",))
            return self._result(command, stdout=(json.dumps(payload),))
        return self._result(command)

    def _result(
        self,
        command: tuple[str, ...],
        *,
        stdout: tuple[str, ...] = (),
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
            stdout_lines=stdout,
            stderr_lines=(),
            output_truncated=False,
            process_tree_killed=False,
            pid=1234,
        )


class FakeEpicPipeline:
    def __init__(self, root: Path, repo: FakeRepository, *, pr_numbers: dict[str, int]) -> None:
        self.root = root
        self.repo = repo
        self.pr_numbers = pr_numbers
        self.calls: list[str] = []

    def run_epic(self, run: AutopilotRun, *, human_authorized=None, cancel_event=None) -> EpicPipelineResult:
        epic_id = run.epic_id or run.request.scope_id
        self.calls.append(epic_id)
        task_id = "T001" if epic_id == "E001" else "T002"
        self._mark_task_complete(task_id)
        branch_name = f"epic/{epic_id}"
        self.repo.current_branch = branch_name
        self.repo.clean = True
        self.repo.head_sha_value = f"{len(self.calls):040x}"[-40:]
        task_result = TaskResult(
            task_id=task_id,
            status=RunStatus.COMPLETED,
            command_results=(CommandResult(command=("git", "commit"), status="PASS", exit_code=0, duration_ms=1, timed_out=False),),
            commit_sha=self.repo.head_sha_value,
            title=f"Task {task_id}",
        )
        updated_run = AutopilotRun(
            run_id=run.run_id,
            request=run.request,
            status=RunStatus.WAITING_FOR_MERGE,
            created_at=run.created_at,
            updated_at=run.updated_at,
            epic_id=epic_id,
            milestone_id=run.milestone_id,
            branch_name=branch_name,
            current_task_id=task_id,
            task_results=tuple([*run.task_results, task_result]),
            command_results=tuple(run.command_results),
            pull_request=PullRequestInfo(
                number=self.pr_numbers[epic_id],
                url=f"https://example.invalid/pr/{self.pr_numbers[epic_id]}",
                title=f"{epic_id}: close epic",
                base_branch="master",
                head_branch=branch_name,
                draft=True,
                merged=False,
            ),
            last_error=None,
        )
        return EpicPipelineResult(
            status=RunStatus.WAITING_FOR_MERGE,
            run=updated_run,
            epic_id=epic_id,
            branch_name=branch_name,
            task_ids=(task_id,),
            task_results=(task_result,),
            command_results=task_result.command_results,
            pull_request=updated_run.pull_request,
        )

    def _mark_task_complete(self, task_id: str) -> None:
        tasks_path = self.root / "specs" / "001-ai-content-studio" / "tasks.md"
        text = tasks_path.read_text(encoding="utf-8")
        text = text.replace(f"- [ ] {task_id}", f"- [X] {task_id}", 1)
        tasks_path.write_text(text, encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not text.endswith("\n"):
        text = f"{text}\n"
    path.write_text(text, encoding="utf-8")


def _setup_repo(tmp_path: Path) -> None:
    workstreams = tmp_path / ".specify" / "workstreams"
    feature_dir = tmp_path / "specs" / "001-ai-content-studio"
    runtime = tmp_path / ".specify" / "runtime" / "task-runs" / "T900"
    workstreams.mkdir(parents=True, exist_ok=True)
    feature_dir.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    _write(
        workstreams / "M001.yml",
        "\n".join(
            [
                "id: M001",
                "title: Milestone M001",
                "status: active",
                "goal: deliver milestone",
                "epics:",
                "  - E001",
                "  - E002",
                "completion_criteria:",
                "  - all epics merged",
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
                "status: planned",
                "risk: low",
                "depends_on: []",
                "tasks:",
                "  - T001",
                "required_checks:",
                "  - python -m pytest",
                "pr_policy:",
                "  one_pr_per_epic: true",
                "  merge_requires_human: true",
                "  auto_merge: false",
                "commit_policy:",
                "  one_commit_per_task: true",
                "  commit_requires_human: true",
                "  auto_commit: false",
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
                "branch: epic/E002",
                "status: planned",
                "risk: low",
                "depends_on:",
                "  - E001",
                "tasks:",
                "  - T002",
                "required_checks:",
                "  - python -m pytest",
                "pr_policy:",
                "  one_pr_per_epic: true",
                "  merge_requires_human: true",
                "  auto_merge: false",
                "commit_policy:",
                "  one_commit_per_task: true",
                "  commit_requires_human: true",
                "  auto_commit: false",
                "",
            ]
        ),
    )
    _write(
        feature_dir / "tasks.md",
        "\n".join(
            [
                "- [ ] T001 Epic 1",
                "Milestone: M001",
                "Epic: E001",
                "Risk: low",
                "Implementation files: backend/app/tooling/local_autopilot/milestone_pipeline.py",
                "Test files: backend/tests/unit/tooling/local_autopilot/test_milestone_pipeline.py",
                "Dependencies: None",
                "Validation commands: python -m pytest backend/tests/unit/tooling/local_autopilot/test_milestone_pipeline.py",
                "Acceptance criteria: done",
                "Test requirements: direct coverage",
                "",
                "- [ ] T002 Epic 2",
                "Milestone: M001",
                "Epic: E002",
                "Risk: low",
                "Implementation files: backend/app/tooling/local_autopilot/milestone_pipeline.py",
                "Test files: backend/tests/unit/tooling/local_autopilot/test_milestone_pipeline.py",
                "Dependencies: T001",
                "Validation commands: python -m pytest backend/tests/unit/tooling/local_autopilot/test_milestone_pipeline.py",
                "Acceptance criteria: done",
                "Test requirements: direct coverage",
                "",
            ]
        ),
    )
    _write(
        runtime / "baseline.json",
        json.dumps(
            {
                "schema_version": 1,
                "task": "T900",
                "epic": "E001",
                "branch": "master",
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


def _make_run(tmp_path: Path, *, status: RunStatus = RunStatus.PREFLIGHT, epic_id: str | None = None, branch_name: str | None = None, pull_request: PullRequestInfo | None = None) -> AutopilotRun:
    request = AutopilotRequest(
        scope_type=ScopeType.MILESTONE,
        scope_id="M001",
        run_mode=RunMode.FULL,
        repo_path=str(tmp_path),
    )
    return AutopilotRun(
        run_id="run-m001",
        request=request,
        status=status,
        created_at="2026-07-23T12:00:00Z",
        updated_at="2026-07-23T12:00:00Z",
        milestone_id="M001",
        epic_id=epic_id,
        branch_name=branch_name,
        pull_request=pull_request,
    )


def _build_pipeline(tmp_path: Path, repo: FakeRepository, github: FakeGitHubAdapter, process: FakeProcessRunner, fake_epic_pipeline: FakeEpicPipeline) -> MilestonePipeline:
    def factory(root: Path, config, repository, github_adapter, process_runner_fn):
        return fake_epic_pipeline

    return MilestonePipeline(
        tmp_path,
        repository=repo,
        epic_pipeline_factory=factory,
        github_adapter=github,
        process_runner_fn=process,
    )


def test_selects_first_ready_epic_and_waits_for_merge(tmp_path):
    _setup_repo(tmp_path)
    repo = FakeRepository(tmp_path)
    github = FakeGitHubAdapter()
    process = FakeProcessRunner(repo)
    process.set_pr_metadata(101, state="OPEN", merged_at=None, head_ref_name="epic/E001")
    fake_epic = FakeEpicPipeline(tmp_path, repo, pr_numbers={"E001": 101, "E002": 102})
    pipeline = _build_pipeline(tmp_path, repo, github, process, fake_epic)

    result = pipeline.run_milestone(_make_run(tmp_path), human_authorized=True)

    assert isinstance(result, MilestonePipelineResult)
    assert result.status == RunStatus.WAITING_FOR_MERGE
    assert result.epic_id == "E001"
    assert result.pull_request is not None
    assert result.pull_request.number == 101
    assert fake_epic.calls == ["E001"]
    assert repo.current_branch == "epic/E001"


def test_resume_open_epic_pr_stays_waiting(tmp_path):
    _setup_repo(tmp_path)
    repo = FakeRepository(tmp_path, current_branch="epic/E001")
    github = FakeGitHubAdapter()
    process = FakeProcessRunner(repo)
    process.set_pr_metadata(101, state="OPEN", merged_at=None, head_ref_name="epic/E001")
    fake_epic = FakeEpicPipeline(tmp_path, repo, pr_numbers={"E001": 101, "E002": 102})
    pipeline = _build_pipeline(tmp_path, repo, github, process, fake_epic)
    run = _make_run(tmp_path)

    result = pipeline.run_milestone(run)

    assert result.status == RunStatus.WAITING_FOR_MERGE
    assert result.epic_id == "E001"
    assert result.pull_request is not None
    assert result.pull_request.number == 101
    assert fake_epic.calls == ["E001"]


def test_resume_closed_not_merged_fails(tmp_path):
    _setup_repo(tmp_path)
    repo = FakeRepository(tmp_path, current_branch="epic/E001")
    github = FakeGitHubAdapter()
    process = FakeProcessRunner(repo)
    process.set_pr_metadata(101, state="CLOSED", merged_at=None, head_ref_name="epic/E001")
    fake_epic = FakeEpicPipeline(tmp_path, repo, pr_numbers={"E001": 101, "E002": 102})
    pipeline = _build_pipeline(tmp_path, repo, github, process, fake_epic)
    run = _make_run(tmp_path, status=RunStatus.WAITING_FOR_MERGE, epic_id="E001", branch_name="epic/E001", pull_request=PullRequestInfo(number=101, url="https://example.invalid/pr/101", title="PR 101", base_branch="master", head_branch="epic/E001"))

    result = pipeline.run_milestone(run)

    assert result.status == RunStatus.FAILED
    assert "closed" in (result.reason or "").lower()


def test_resume_merged_epic_creates_bookkeeping_closure_pr(tmp_path):
    _setup_repo(tmp_path)
    repo = FakeRepository(tmp_path, current_branch="epic/E001")
    github = FakeGitHubAdapter()
    process = FakeProcessRunner(repo)
    process.set_pr_metadata(101, state="MERGED", merged_at="2026-07-23T10:00:00Z", head_ref_name="epic/E001")
    fake_epic = FakeEpicPipeline(tmp_path, repo, pr_numbers={"E001": 101, "E002": 102})
    pipeline = _build_pipeline(tmp_path, repo, github, process, fake_epic)
    run = _make_run(tmp_path, status=RunStatus.WAITING_FOR_MERGE, epic_id="E001", branch_name="epic/E001", pull_request=PullRequestInfo(number=101, url="https://example.invalid/pr/101", title="PR 101", base_branch="master", head_branch="epic/E001"))

    result = pipeline.run_milestone(run)

    assert result.status == RunStatus.WAITING_FOR_MERGE
    assert result.branch_name == "bookkeeping/M001/E001"
    assert result.pull_request is not None
    assert result.pull_request.head_branch == "bookkeeping/M001/E001"
    assert (tmp_path / ".specify" / "workstreams" / "E001.yml").read_text(encoding="utf-8").splitlines()[6] == "status: completed"
    assert github.calls[-1][:3] == ("create_draft_pr", "master", "bookkeeping/M001/E001")


def test_resume_after_restart_uses_saved_run_state(tmp_path):
    _setup_repo(tmp_path)
    repo = FakeRepository(tmp_path, current_branch="epic/E001")
    github = FakeGitHubAdapter()
    process = FakeProcessRunner(repo)
    process.set_pr_metadata(101, state="MERGED", merged_at="2026-07-23T10:00:00Z", head_ref_name="epic/E001")
    fake_epic = FakeEpicPipeline(tmp_path, repo, pr_numbers={"E001": 101, "E002": 102})
    pipeline = _build_pipeline(tmp_path, repo, github, process, fake_epic)
    run = _make_run(tmp_path, status=RunStatus.WAITING_FOR_MERGE, epic_id="E001", branch_name="epic/E001", pull_request=PullRequestInfo(number=101, url="https://example.invalid/pr/101", title="PR 101", base_branch="master", head_branch="epic/E001"))

    saved = pipeline.run_milestone(run)
    loaded = load_run_state(saved.run.run_id, root=tmp_path)

    assert loaded.status == RunStatus.WAITING_FOR_MERGE
    assert loaded.pull_request is not None


def test_selects_next_epic_after_closure_merge(tmp_path):
    _setup_repo(tmp_path)
    repo = FakeRepository(tmp_path, current_branch="bookkeeping/M001/E001")
    github = FakeGitHubAdapter()
    process = FakeProcessRunner(repo)
    process.set_pr_metadata(101, state="OPEN", merged_at=None, head_ref_name="epic/E001")
    fake_epic = FakeEpicPipeline(tmp_path, repo, pr_numbers={"E001": 101, "E002": 102})
    pipeline = _build_pipeline(tmp_path, repo, github, process, fake_epic)
    run = _make_run(tmp_path, status=RunStatus.WAITING_FOR_MERGE, epic_id="E001", branch_name="epic/E001", pull_request=PullRequestInfo(number=101, url="https://example.invalid/pr/101", title="PR 101", base_branch="master", head_branch="epic/E001"))

    first = pipeline.run_milestone(run)
    assert first.status == RunStatus.WAITING_FOR_MERGE
    assert first.branch_name == "epic/E001"
    process.set_pr_metadata(first.pull_request.number, state="MERGED", merged_at="2026-07-23T10:00:00Z", head_ref_name="epic/E001")
    closure = pipeline.run_milestone(first.run)
    assert closure.status == RunStatus.WAITING_FOR_MERGE
    assert closure.branch_name == "bookkeeping/M001/E001"
    process.set_pr_metadata(closure.pull_request.number, state="MERGED", merged_at="2026-07-23T11:00:00Z", head_ref_name="bookkeeping/M001/E001")
    assert (tmp_path / ".specify" / "workstreams" / "E001.yml").read_text(encoding="utf-8").splitlines()[6] == "status: completed"


def test_creates_final_milestone_closure_pr_and_completes_after_merge(tmp_path):
    _setup_repo(tmp_path)
    repo = FakeRepository(tmp_path, current_branch="bookkeeping/M001/E002")
    github = FakeGitHubAdapter()
    process = FakeProcessRunner(repo)
    process.set_pr_metadata(301, state="MERGED", merged_at="2026-07-23T11:00:00Z", head_ref_name="bookkeeping/M001/E002")
    fake_epic = FakeEpicPipeline(tmp_path, repo, pr_numbers={"E001": 101, "E002": 102})
    pipeline = _build_pipeline(tmp_path, repo, github, process, fake_epic)

    _write(tmp_path / ".specify" / "workstreams" / "E001.yml", "\n".join([
        "id: E001",
        "title: Epic E001",
        "milestone: M001",
        "feature: specs/001-ai-content-studio",
        "base_branch: master",
        "branch: epic/E001",
        "status: completed",
        "risk: low",
        "depends_on: []",
        "tasks:",
        "  - T001",
        "required_checks:",
        "  - python -m pytest",
        "pr_policy:",
        "  one_pr_per_epic: true",
        "merge_requires_human: true",
        "auto_merge: false",
        "commit_policy:",
        "  one_commit_per_task: true",
        "  commit_requires_human: true",
        "  auto_commit: false",
        "",
    ]))
    _write(tmp_path / ".specify" / "workstreams" / "E002.yml", "\n".join([
        "id: E002",
        "title: Epic E002",
        "milestone: M001",
        "feature: specs/001-ai-content-studio",
        "base_branch: master",
        "branch: epic/E002",
        "status: completed",
        "risk: low",
        "depends_on:",
        "  - E001",
        "tasks:",
        "  - T002",
        "required_checks:",
        "  - python -m pytest",
        "pr_policy:",
        "  one_pr_per_epic: true",
        "merge_requires_human: true",
        "auto_merge: false",
        "commit_policy:",
        "  one_commit_per_task: true",
        "  commit_requires_human: true",
        "  auto_commit: false",
        "",
    ]))
    _write(tmp_path / "specs" / "001-ai-content-studio" / "tasks.md", "\n".join([
        "- [X] T001 Epic 1",
        "Milestone: M001",
        "Epic: E001",
        "Risk: low",
        "Implementation files: backend/app/tooling/local_autopilot/milestone_pipeline.py",
        "Test files: backend/tests/unit/tooling/local_autopilot/test_milestone_pipeline.py",
        "Dependencies: None",
        "Validation commands: python -m pytest backend/tests/unit/tooling/local_autopilot/test_milestone_pipeline.py",
        "Acceptance criteria: done",
        "Test requirements: direct coverage",
        "",
        "- [X] T002 Epic 2",
        "Milestone: M001",
        "Epic: E002",
        "Risk: low",
        "Implementation files: backend/app/tooling/local_autopilot/milestone_pipeline.py",
        "Test files: backend/tests/unit/tooling/local_autopilot/test_milestone_pipeline.py",
        "Dependencies: T001",
        "Validation commands: python -m pytest backend/tests/unit/tooling/local_autopilot/test_milestone_pipeline.py",
        "Acceptance criteria: done",
        "Test requirements: direct coverage",
        "",
    ]))

    run = _make_run(tmp_path, status=RunStatus.WAITING_FOR_MERGE, epic_id="E002", branch_name="bookkeeping/M001/E002", pull_request=PullRequestInfo(number=301, url="https://example.invalid/pr/301", title="PR 301", base_branch="master", head_branch="bookkeeping/M001/E002"))

    waiting = pipeline.run_milestone(run)
    assert waiting.status == RunStatus.WAITING_FOR_MERGE
    assert waiting.branch_name == "bookkeeping/M001/close"
    close_number = waiting.pull_request.number

    process.set_pr_metadata(close_number, state="MERGED", merged_at="2026-07-23T12:00:00Z", head_ref_name="bookkeeping/M001/close")
    completed = pipeline.run_milestone(waiting.run)
    assert completed.status == RunStatus.COMPLETED


def test_run_milestone_pipeline_wrapper(tmp_path):
    _setup_repo(tmp_path)
    repo = FakeRepository(tmp_path)
    github = FakeGitHubAdapter()
    process = FakeProcessRunner(repo)
    process.set_pr_metadata(101, state="OPEN", merged_at=None, head_ref_name="epic/E001")
    fake_epic = FakeEpicPipeline(tmp_path, repo, pr_numbers={"E001": 101, "E002": 102})

    result = run_milestone_pipeline(
        _make_run(tmp_path),
        root=tmp_path,
        repository=repo,
        github_adapter=github,
        process_runner_fn=process,
        epic_pipeline_factory=lambda *args, **kwargs: fake_epic,
        human_authorized=True,
    )

    assert result.status == RunStatus.WAITING_FOR_MERGE

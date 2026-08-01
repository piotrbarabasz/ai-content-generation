from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.tooling import task_consistency
from app.tooling.local_autopilot import epic_pipeline as epic_module
from app.tooling.local_autopilot import validation_receipt as validation_receipt_module
from app.tooling.local_autopilot.epic_pipeline import EpicPipeline, EpicPipelineResult, run_epic_pipeline
from app.tooling.local_autopilot.github_adapter import GitHubAuthResult
from app.tooling.local_autopilot.config import AutopilotConfig
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
from app.tooling.local_autopilot.task_state_machine import (
    _load_task_snapshot,
    TaskLifecycleState,
    TaskReceiptRecord,
    TaskReceiptStage,
    TaskStateRecord,
    load_task_state,
    save_task_receipt,
    save_task_state,
)
from app.tooling.local_autopilot.task_pipeline import TaskPipelineResult


@dataclass
class FakeRepository:
    root: Path
    current_branch: str = "master"
    head_sha_value: str = "a" * 40
    master_head_sha_value: str | None = None
    clean: bool = True
    commit_should_fail: bool = False
    push_should_fail: bool = False
    push_result: ProcessResult | None = None
    diverged: bool = False
    remote_url: str = "https://example.invalid/repo.git"
    remote_should_fail: bool = False
    remote_probe_should_fail: bool = False

    def __post_init__(self) -> None:
        if self.master_head_sha_value is None:
            self.master_head_sha_value = self.head_sha_value
        self.calls: list[tuple[str, ...]] = []
        self.commit_messages: list[str] = []
        self.commit_history: list[tuple[str, str]] = []
        self.commit_subjects: dict[str, str] = {}
        self.commit_files: dict[str, tuple[str, ...]] = {}
        self.path_additions: dict[str, list[str]] = {}
        self.pushed_branches: list[str] = []
        self.push_calls: list[tuple[str, str, int]] = []
        self.validate_remote_calls: list[tuple[str, int, int | None]] = []
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
        return self.status()

    def switch_to_master_and_pull(self, base_branch: str = "master", remote: str = "origin") -> None:
        self.calls.append(("switch_to_master_and_pull", base_branch, remote))
        self.current_branch = base_branch
        self.head_sha_value = self.master_head_sha_value or self.head_sha_value

    def create_branch(self, branch: str, *, base_branch: str = "master") -> None:
        self.calls.append(("create_branch", branch, base_branch))
        self.current_branch = branch

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        self.calls.append(("is_ancestor", ancestor, descendant))
        history_index = {sha: index for index, (sha, _subject) in enumerate(self.commit_history)}
        if ancestor in history_index and descendant in history_index:
            return history_index[ancestor] <= history_index[descendant]
        if self.diverged:
            return False
        if ancestor == descendant:
            return True
        if ancestor == self.head_sha_value and descendant == (self.master_head_sha_value or self.head_sha_value):
            return True
        if ancestor == (self.master_head_sha_value or self.head_sha_value) and descendant == self.head_sha_value:
            return self.head_sha_value != (self.master_head_sha_value or self.head_sha_value)
        return False

    def merge_ff_only(self, branch: str) -> None:
        self.calls.append(("merge_ff_only", branch))
        if self.diverged:
            raise RuntimeError(f"git merge --ff-only {branch} failed")
        self.head_sha_value = self.master_head_sha_value or self.head_sha_value

    def merge_base_into_active_branch(self, branch: str, base_branch: str, *, timeout_seconds: int = 20) -> None:
        self.calls.append(("merge_base_into_active_branch", branch, base_branch, str(timeout_seconds)))
        if self.current_branch != branch:
            raise RuntimeError(f"current branch {self.current_branch!r} does not match {branch!r}")
        if self.diverged:
            self.calls.append(("merge_no_edit", base_branch))
            self.head_sha_value = "d" * 40
            self.diverged = False
            return
        if self.is_ancestor(self.head_sha_value, self.master_head_sha_value or self.head_sha_value):
            self.calls.append(("merge_ff_only", base_branch))
            self.head_sha_value = self.master_head_sha_value or self.head_sha_value
            return
        if self.is_ancestor(self.master_head_sha_value or self.head_sha_value, self.head_sha_value):
            return
        self.calls.append(("merge_no_edit", base_branch))
        self.head_sha_value = "e" * 40

    def sync_branch_with_base(self, branch: str, *, base_branch: str = "master", base_head_sha: str | None = None) -> None:
        self.calls.append(("sync_branch_with_base", branch, base_branch, base_head_sha or ""))
        if self.current_branch != branch:
            raise RuntimeError(f"current branch {self.current_branch!r} does not match {branch!r}")
        branch_head_sha = self.head_sha()
        resolved_base_head = base_head_sha or self.master_head_sha_value or self.head_sha_value
        if self.is_ancestor(branch_head_sha, resolved_base_head):
            self.merge_ff_only(base_branch)
            return
        if self.is_ancestor(resolved_base_head, branch_head_sha):
            return
        raise RuntimeError(f"{branch} and {base_branch} have diverged")

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
        self.record_commit(self.head_sha_value, message)
        self.clean = True
        return self._result(("git", "commit", "-m", message))

    def push(self, branch: str, remote: str = "origin", *, timeout_seconds: int = 20) -> ProcessResult:
        self.calls.append(("push", branch, remote))
        self.pushed_branches.append(branch)
        self.push_calls.append((branch, remote, timeout_seconds))
        if self.push_result is not None:
            return self.push_result
        if self.push_should_fail:
            return self._result(("git", "push", "-u", remote, branch), status="FAIL", exit_code=1)
        return self._result(("git", "push", "-u", remote, branch))

    def validate_remote(self, remote: str = "origin", *, timeout_seconds: int = 20, probe_timeout_seconds: int | None = None) -> str:
        self.calls.append(("validate_remote", remote))
        self.validate_remote_calls.append((remote, timeout_seconds, probe_timeout_seconds))
        if self.remote_should_fail:
            raise RuntimeError(f"{remote} remote is missing")
        if self.remote_probe_should_fail:
            raise RuntimeError(f"{remote} remote HEAD probe failed")
        return self.remote_url

    def diff_check(self, *, cached: bool = False) -> ProcessResult:
        self.calls.append(("diff_check", "cached" if cached else "worktree"))
        self.diff_checks.append(cached)
        return self._result(("git", "--no-pager", "diff", "--cached", "--check") if cached else ("git", "--no-pager", "diff", "--check"))

    def head_sha(self) -> str:
        self.calls.append(("head_sha",))
        return self.head_sha_value

    def find_commit_shas_by_subject(self, subject: str, ref: str = "HEAD") -> tuple[str, ...]:
        self.calls.append(("find_commit_shas_by_subject", subject, ref))
        normalized = subject.strip()
        return tuple(sha for sha, commit_subject in self.commit_history if commit_subject.strip() == normalized)

    def list_commit_history(self, ref: str = "HEAD") -> tuple[tuple[str, str], ...]:
        self.calls.append(("list_commit_history", ref))
        return tuple(self.commit_history)

    def find_commits_adding_path(self, path: str, ref: str = "HEAD") -> tuple[str, ...]:
        normalized = str(path).replace("\\", "/").strip()
        self.calls.append(("find_commits_adding_path", normalized, ref))
        return tuple(self.path_additions.get(normalized, ()))

    def list_commit_files(self, commit_sha: str) -> tuple[str, ...]:
        normalized = str(commit_sha).strip()
        self.calls.append(("list_commit_files", normalized))
        return self.commit_files.get(normalized, ())

    def record_commit(self, sha: str, subject: str, *, files: tuple[str, ...] = ()) -> None:
        sha = str(sha).strip()
        subject = str(subject).strip()
        if not sha or not subject:
            return
        self.commit_subjects[sha] = subject
        self.commit_history.append((sha, subject))
        normalized_files = tuple(str(path).replace("\\", "/").strip() for path in files if str(path).strip())
        self.commit_files[sha] = normalized_files
        for path in normalized_files:
            self.path_additions.setdefault(path, []).append(sha)

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
        prs_by_branch: dict[tuple[str, str], PullRequestInfo] | None = None,
    ) -> None:
        self.auth_available = auth_available
        self.auth_authenticated = auth_authenticated
        self.existing_pr = existing_pr
        self.created_pr = created_pr
        self.prs_by_branch = dict(prs_by_branch or {})
        self.calls: list[tuple[str, ...]] = []
        self.find_pr_calls: list[tuple[str, str, int]] = []
        self.create_pr_calls: list[tuple[str, str, bool, int]] = []

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

    def find_pr(self, base: str, head: str, *, timeout_seconds: int = 30) -> PullRequestInfo | None:
        self.calls.append(("find_pr", base, head))
        self.find_pr_calls.append((base, head, timeout_seconds))
        if (base, head) in self.prs_by_branch:
            return self.prs_by_branch[(base, head)]
        if self.existing_pr is not None and self.existing_pr.base_branch == base and self.existing_pr.head_branch == head:
            return self.existing_pr
        return self.existing_pr

    def create_pr(self, base: str, head: str, title: str, body: str, *, draft: bool, timeout_seconds: int = 120) -> PullRequestInfo:
        self.calls.append(("create_pr", base, head, str(draft), title))
        self.create_pr_calls.append((base, head, draft, timeout_seconds))
        if (base, head) in self.prs_by_branch:
            return self.prs_by_branch[(base, head)]
        if self.existing_pr is not None and self.existing_pr.base_branch == base and self.existing_pr.head_branch == head:
            return self.existing_pr
        if self.created_pr is not None:
            return self.created_pr
        created = PullRequestInfo(
            number=99,
            url="https://example.invalid/pr/99",
            title=title,
            base_branch=base,
            head_branch=head,
            draft=draft,
            merged=False,
        )
        self.prs_by_branch[(base, head)] = created
        return created

    def create_draft_pr(self, base: str, head: str, title: str, body: str, *, timeout_seconds: int = 120) -> PullRequestInfo:
        self.calls.append(("create_draft_pr", base, head, title))
        return self.create_pr(base, head, title, body, draft=True, timeout_seconds=timeout_seconds)


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
        title = self._task_title(task_id) or str(outcome.get("title") or f"Task {task_id}")
        allowlist = tuple(outcome.get("allowlist") or ("backend/app/tooling/local_autopilot/epic_pipeline.py",))
        validation_commands = tuple(outcome.get("validation_commands") or ("python -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py",))
        if status == RunStatus.COMPLETED:
            self._mark_complete(task_id)
            self.repo.head_sha_value = commit_sha
            self.repo.record_commit(commit_sha, f"feat({task_id}): {title}")
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

    def _task_title(self, task_id: str) -> str:
        tasks_path = self.root / "specs" / "001-ai-content-studio" / "tasks.md"
        for found_task_id, _start_line, lines in task_consistency._iter_task_blocks(tasks_path):
            if found_task_id != task_id:
                continue
            header = lines[0][1]
            return header[header.index(task_id) + len(task_id) :].strip()
        return ""


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
    def __init__(self, repo: FakeRepository, *, python_executable: str = sys.executable, base_sha: str = "b" * 40) -> None:
        self.repo = repo
        self.python_executable = python_executable
        self.base_sha = base_sha
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, **kwargs):
        command = tuple(str(part) for part in argv)
        self.calls.append(command)
        if command == ("git", "config", "--local", "--get", "agent.python"):
            return self._result(command, stdout=(self.python_executable,))
        if command == ("git", "remote", "get-url", "origin"):
            return self._result(command, stdout=("https://example.invalid/repo.git",))
        if command == ("git", "ls-remote", "--exit-code", "origin", "HEAD"):
            return self._result(command, stdout=(f"{self.base_sha}\tHEAD",))
        if command == ("git", "rev-parse", "HEAD"):
            return self._result(command, stdout=(self.repo.head_sha(),))
        if command and command[0:2] == ("git", "rev-parse"):
            return self._result(command, stdout=(self.base_sha,))
        if command[:2] == (self.python_executable, "-m"):
            return self._result(command, stdout=("ok",))
        if command == (self.python_executable, "--version"):
            return self._result(command, stdout=("Python 3.11.8",))
        if Path(command[0]).name.lower() in {"codex", "codex.exe", "codex.cmd"}:
            if command[1:] == ("--help",):
                return self._result(command, stdout=("Codex CLI",))
            if command[1:] == ("exec", "--help"):
                return self._result(command, stdout=("Run Codex non-interactively",))
            return self._result(command, stdout=("codex",))
        if Path(command[0]).name.lower() == "gh":
            if command[1:] == ("auth", "status"):
                return self._result(command, stdout=("github.com", "Logged in to github.com as tester"))
            if command[1] == "pr" and command[2] == "list":
                return self._result(command, stdout=("[]",))
            if command[1] == "pr" and command[2] == "create":
                return self._result(command, stdout=("created",))
            if command[1] == "pr" and command[2] == "view":
                return self._result(command, stdout=("{}",))
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


def _make_run(
    tmp_path: Path,
    *,
    run_mode: RunMode = RunMode.STOP_BEFORE_PUSH,
    epic_id: str = "E002",
    branch_name: str = "feature/E002",
) -> AutopilotRun:
    request = AutopilotRequest(
        scope_type=ScopeType.EPIC,
        scope_id=epic_id,
        run_mode=run_mode,
        repo_path=str(tmp_path),
    )
    return AutopilotRun(
        run_id=f"run-epic-{epic_id.lower()}",
        request=request,
        status=RunStatus.PREFLIGHT,
        created_at="2026-07-23T12:00:00Z",
        updated_at="2026-07-23T12:00:00Z",
        epic_id=epic_id,
        branch_name=branch_name,
    )


def _config(
    *,
    auto_commit: bool = True,
    auto_push: bool = True,
    create_draft_pr: bool = True,
    max_tasks_per_run: int = 20,
    command_timeout_seconds: int = 180,
    push_timeout_seconds: int = 1200,
    pre_push_pytest_timeout_seconds: int = 900,
    ci_pytest_timeout_seconds: int = 900,
    hook_timeout_buffer_seconds: int = 120,
) -> AutopilotConfig:
    return AutopilotConfig(
        auto_commit=auto_commit,
        auto_push=auto_push,
        create_draft_pr=create_draft_pr,
        auto_merge=False,
        deploy=False,
        max_repair_cycles=2,
        max_tasks_per_run=max_tasks_per_run,
        command_timeout_seconds=command_timeout_seconds,
        push_timeout_seconds=push_timeout_seconds,
        pre_push_pytest_timeout_seconds=pre_push_pytest_timeout_seconds,
        ci_pytest_timeout_seconds=ci_pytest_timeout_seconds,
        hook_timeout_buffer_seconds=hook_timeout_buffer_seconds,
        codex_timeout_seconds=3600,
        closure_mode="pull_request",
    )


def _build_pipeline(
    tmp_path: Path,
    repo: FakeRepository,
    github: FakeGitHubAdapter,
    receipt: FakeReviewReceipt,
    task_outcomes: dict[str, dict[str, object]],
    *,
    base_sha: str = "b" * 40,
    config: AutopilotConfig | None = None,
) -> tuple[EpicPipeline, FakeTaskPipeline, FakeProcessRunner]:
    process = FakeProcessRunner(repo, base_sha=base_sha)
    task_factory_calls: list[tuple[Path, AutopilotConfig]] = []

    def factory(root: Path, config: AutopilotConfig, process_runner_fn):
        task_factory_calls.append((root, config))
        return FakeTaskPipeline(root, repo, outcomes=task_outcomes)

    pipeline = EpicPipeline(
        tmp_path,
        config=config,
        repository=repo,
        task_pipeline_factory=factory,
        github_adapter=github,
        process_runner_fn=process,
        review_receipt_writer=receipt.write,
        review_receipt_validator=receipt.validate,
    )
    task_pipeline = factory(tmp_path, pipeline.config, process)
    return pipeline, task_pipeline, process


def _record_task_evidence(
    repo: FakeRepository,
    *,
    task_id: str,
    sha: str,
    title: str,
    files: tuple[str, ...] | None = None,
) -> None:
    if files is None:
        tasks_path = repo.root / "specs" / "001-ai-content-studio" / "tasks.md"
        try:
            snapshot = _load_task_snapshot(task_id, tasks_path)
        except Exception:
            files = ()
        else:
            files = snapshot.allowlist
    repo.record_commit(sha, f"feat({task_id}): {title}", files=files or ())


def _write_committed_task_state(
    tmp_path: Path,
    *,
    task_id: str,
    sha: str,
    run_id: str = "run-epic-002",
    branch: str = "feature/E002",
) -> None:
    state = TaskStateRecord(
        schema_version=1,
        run_id=run_id,
        task_id=task_id,
        state=TaskLifecycleState.COMMITTED,
        updated_at="2026-07-23T12:00:00Z",
        branch=branch,
        head_sha=sha,
        tasks_path=str(tmp_path / "specs" / "001-ai-content-studio" / "tasks.md"),
        feature_dir=str(tmp_path / "specs" / "001-ai-content-studio"),
        allowlist=("backend/app/tooling/local_autopilot/epic_pipeline.py",),
        validation_commands=("python -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py",),
        task_line=1,
    )
    receipt = TaskReceiptRecord(
        schema_version=1,
        run_id=run_id,
        task_id=task_id,
        updated_at="2026-07-23T12:00:00Z",
        state=TaskLifecycleState.COMMITTED,
        commit_sha=sha,
        summary="done",
        files_touched=("backend/app/tooling/local_autopilot/epic_pipeline.py",),
        notes=("done",),
        validation=(
            {
                "command": "python -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py",
                "status": "PASS",
            },
        ),
        stages=(
            TaskReceiptStage(name="validated", status="PASS", updated_at="2026-07-23T12:00:00Z"),
            TaskReceiptStage(name="reviewed", status="PASS", updated_at="2026-07-23T12:00:00Z"),
            TaskReceiptStage(name="closed", status="PASS", updated_at="2026-07-23T12:00:00Z"),
            TaskReceiptStage(name="committed", status="PASS", updated_at="2026-07-23T12:00:00Z"),
        ),
        review_verdict="PASS",
        safe_to_close=True,
        closure_checkbox_before=" ",
        closure_checkbox_after="X",
        closure_task_line=1,
    )
    save_task_state(state, root=tmp_path)
    save_task_receipt(receipt, root=tmp_path)


def _write_incomplete_task_state(
    tmp_path: Path,
    *,
    task_id: str,
    sha: str,
    state: TaskLifecycleState = TaskLifecycleState.IMPLEMENTED,
    run_id: str = "run-epic-002",
    branch: str = "feature/E002",
) -> Path:
    baseline_dir = tmp_path / ".specify" / "runtime" / "task-runs" / task_id
    baseline_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = baseline_dir / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "task": task_id,
                "epic": "E002",
                "branch": branch,
                "head_sha": sha,
                "tracked": [],
                "staged": [],
                "untracked": [],
                "deleted": [],
                "renamed": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    task_state = TaskStateRecord(
        schema_version=1,
        run_id=run_id,
        task_id=task_id,
        state=state,
        updated_at="2026-07-23T12:00:00Z",
        branch=branch,
        head_sha=sha,
        baseline_path=str(baseline_path),
        baseline_branch=branch,
        baseline_head_sha=sha,
        tasks_path=str(tmp_path / "specs" / "001-ai-content-studio" / "tasks.md"),
        feature_dir=str(tmp_path / "specs" / "001-ai-content-studio"),
        allowlist=("backend/app/tooling/local_autopilot/epic_pipeline.py",),
        validation_commands=("python -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py",),
        task_line=1,
        reason="stale baseline",
    )
    task_receipt = TaskReceiptRecord(
        schema_version=1,
        run_id=run_id,
        task_id=task_id,
        updated_at="2026-07-23T12:00:00Z",
        state=state,
        summary="stale baseline",
        files_touched=("backend/app/tooling/local_autopilot/epic_pipeline.py",),
        notes=("stale baseline",),
    )
    save_task_state(task_state, root=tmp_path)
    save_task_receipt(task_receipt, root=tmp_path)
    return baseline_path


def _seed_closure_validation_data(
    tmp_path: Path,
    repo: FakeRepository,
    *,
    task7_sha: str = "1" * 40,
    task8_sha: str = "2" * 40,
    master_sha: str = "9" * 40,
) -> None:
    _write_committed_task_state(tmp_path, task_id="T007", sha=task7_sha)
    _write_committed_task_state(tmp_path, task_id="T008", sha=task8_sha)
    repo.record_commit(task7_sha, "feat(T007): Task 7")
    repo.record_commit(task8_sha, "feat(T008): Task 8")
    repo.record_commit(master_sha, "merge implementation")
    repo.head_sha_value = master_sha
    repo.master_head_sha_value = master_sha


def _write_e003_fixture(tmp_path: Path) -> tuple[Path, Path]:
    workstreams = tmp_path / ".specify" / "workstreams"
    feature_dir = tmp_path / "specs" / "001-ai-content-studio"
    workstreams.mkdir(parents=True, exist_ok=True)
    feature_dir.mkdir(parents=True, exist_ok=True)

    _write(
        workstreams / "E003.yml",
        "\n".join(
            [
                "id: E003",
                "title: Epic E003",
                "milestone: M001",
                "feature: specs/001-ai-content-studio",
                "base_branch: master",
                "branch: feature/E003",
                "status: active",
                "risk: high",
                "depends_on: []",
                "tasks:",
                "  - T009",
                "  - T010",
                "  - T021",
                "  - T022",
                "  - T023",
                "  - T024",
                "required_checks:",
                "  - python -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py",
                "  - git --no-pager diff --check",
                "",
            ]
        ),
    )

    blocks = [
        (
            "T009",
            "Implement artifact storage abstraction and local store",
            "backend/app/storage/artifact_store.py, backend/app/storage/local_store.py, backend/app/storage/manifest.py",
            "backend/tests/unit/test_t009.py",
        ),
        (
            "T010",
            "Add deterministic task state machine hardening",
            "backend/app/tooling/local_autopilot/task_state_machine.py, backend/app/tooling/local_autopilot/epic_pipeline.py",
            "backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py",
        ),
        (
            "T021",
            "Implement canonical WorkflowConfig schema and enum validation",
            "backend/app/domain/workflow_config.py, backend/app/domain/enums.py",
            "backend/tests/unit/test_workflow_config_validation.py",
        ),
        (
            "T022",
            "Implement provider registry and mock provider registration",
            "backend/app/providers/registry.py, backend/app/providers/interfaces.py, backend/app/providers/mocks.py",
            "backend/tests/unit/test_t022.py",
        ),
        (
            "T023",
            "Implement ProviderConfig validation before workflow execution",
            "backend/app/providers/validation.py, backend/app/workflow/engine.py, backend/app/domain/workflow_config.py",
            "backend/tests/unit/test_t023.py",
        ),
        (
            "T024",
            "Add security and secret hygiene foundation",
            ".gitignore, .env.example, README.md",
            "backend/tests/static/test_secret_hygiene.py",
        ),
    ]
    lines: list[str] = ["## Phase 12: Remediation - deterministic evidence", ""]
    for task_id, title, implementation_files, test_files in blocks:
        lines.extend(
            [
                f"- [X] {task_id} {title}",
                "Milestone: M001",
                "Epic: E003",
                "Risk: high",
                f"Implementation files: {implementation_files}",
                f"Test files: {test_files}",
                "Validation commands: python -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py; git --no-pager diff --check",
                "Final PR review required: yes",
                "Goal: deterministic evidence verification.",
                "Dependencies: none",
                "Acceptance criteria: deterministic task evidence is available.",
                "Test requirements: evidence verification passes.",
                "Parallelizable: no",
                "Notes: regression fixture.",
                "",
            ]
    )
    _write(feature_dir / "tasks.md", "\n".join(lines))
    _write(tmp_path / "backend" / "app" / "domain" / "workflow_config.py", "WORKFLOW_CONFIG = True\n")
    _write(tmp_path / "backend" / "app" / "domain" / "enums.py", "ENUMS = True\n")
    _write(tmp_path / "backend" / "tests" / "unit" / "test_workflow_config_validation.py", "def test_workflow_config_validation():\n    assert True\n")
    _write(tmp_path / "backend" / "tests" / "static" / "test_secret_hygiene.py", "def test_secret_hygiene():\n    assert True\n")
    _write(tmp_path / ".gitignore", "*.env\n")
    _write(tmp_path / ".env.example", "API_KEY=placeholder\n")
    _write(tmp_path / "README.md", "# AI Content Studio\n")
    return workstreams / "E003.yml", feature_dir / "tasks.md"


def _seed_e003_supporting_task_evidence(tmp_path: Path, repo: FakeRepository) -> None:
    _write_committed_task_state(tmp_path, task_id="T009", sha="9" * 40, run_id="run-epic-003", branch="feature/E003")
    _write_committed_task_state(tmp_path, task_id="T010", sha="a" * 40, run_id="run-epic-003", branch="feature/E003")
    _write_committed_task_state(tmp_path, task_id="T022", sha="b" * 40, run_id="run-epic-003", branch="feature/E003")
    _write_committed_task_state(tmp_path, task_id="T023", sha="c" * 40, run_id="run-epic-003", branch="feature/E003")
    _record_task_evidence(repo, task_id="T009", sha="9" * 40, title="artifact storage follow-up")
    _record_task_evidence(repo, task_id="T010", sha="a" * 40, title="state machine follow-up")
    _record_task_evidence(repo, task_id="T022", sha="b" * 40, title="registry follow-up")
    _record_task_evidence(repo, task_id="T023", sha="c" * 40, title="provider config follow-up")


def _seed_e003_legacy_bundle(
    repo: FakeRepository,
    *,
    activation_sha: str,
    bundle_sha: str,
    tail_sha: str,
    bundle_subject: str,
    bundle_files: tuple[str, ...],
    activation_before_bundle: bool = False,
) -> None:
    if activation_before_bundle:
        repo.record_commit(activation_sha, "feat(E003): activate epic")
        repo.record_commit(bundle_sha, bundle_subject, files=bundle_files)
    else:
        repo.record_commit(bundle_sha, bundle_subject, files=bundle_files)
        repo.record_commit(activation_sha, "feat(E003): activate epic")
    repo.record_commit(tail_sha, "fix(autopilot): tooling follow-up")
    repo.head_sha_value = tail_sha


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
    written_checks = receipt.writes[0]["required_checks"]
    assert [check["command"] for check in written_checks] == ["python -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py", "git --no-pager diff --check"]
    assert written_checks[0]["executed_command"] == f"{sys.executable} -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py"
    assert written_checks[1]["executed_command"] == "git --no-pager diff --check"
    assert not repo.pushed_branches
    assert not github.calls
    assert result.pull_request is None


def test_run_epic_resumes_from_next_ready_task(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="b" * 40, master_head_sha_value="b" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {"T008": {"status": RunStatus.COMPLETED, "commit_sha": "2" * 40, "title": "Task 8"}},
    )
    _write_committed_task_state(tmp_path, task_id="T007", sha="1" * 40)
    _record_task_evidence(repo, task_id="T007", sha="1" * 40, title="Implement epic task 1")

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH))

    assert result.status == RunStatus.COMPLETED
    assert task_pipeline.calls == []


def test_run_epic_syncs_existing_branch_before_tasks(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed")
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="a" * 40, master_head_sha_value="b" * 40)
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

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH))

    assert result.status == RunStatus.COMPLETED
    assert ("merge_base_into_active_branch", "feature/E002", "master", "180") in repo.calls
    assert ("merge_ff_only", "master") in repo.calls
    assert task_pipeline.calls == []


def test_run_epic_does_not_merge_when_worktree_is_dirty(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed")
    repo = FakeRepository(tmp_path, current_branch="feature/E002", clean=False)
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

    assert result.status == RunStatus.BLOCKED
    assert result.reason == "active epic must recover or commit the current task before syncing with base"
    assert not any(call[0] == "merge_base_into_active_branch" for call in repo.calls)
    assert task_pipeline.calls == []


def test_run_epic_merges_diverged_clean_branch_and_refreshes_unfinished_task_baseline(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=False)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="d" * 40, master_head_sha_value="b" * 40, diverged=True)
    pipeline, _, _ = _build_pipeline(
        tmp_path,
        repo,
        FakeGitHubAdapter(),
        FakeReviewReceipt(tmp_path),
        {
            "T007": {"status": RunStatus.COMPLETED, "commit_sha": "1" * 40, "title": "Task 7"},
            "T008": {"status": RunStatus.COMPLETED, "commit_sha": "2" * 40, "title": "Task 8"},
        },
    )
    stale_baseline = _write_incomplete_task_state(tmp_path, task_id="T008", sha="a" * 40)
    epic_manifest = epic_module.get_epic("E002", tmp_path / ".specify" / "workstreams")

    refreshed = pipeline._refresh_task_baselines_after_head_change(
        "E002",
        epic_manifest,
        old_head_sha="a" * 40,
        new_head_sha="d" * 40,
    )

    assert refreshed == ("T008",)
    backup_root = tmp_path / ".specify" / "runtime" / "recovery-backups"
    assert any(path.name.startswith("T008-") for path in backup_root.iterdir())
    refreshed_state = load_task_state("T008", root=tmp_path)
    assert refreshed_state.state == TaskLifecycleState.PENDING
    assert refreshed_state.baseline_path == ""
    assert not stale_baseline.exists()


def test_run_epic_merge_conflict_reports_reason_without_reset(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path, current_branch="feature/E002")

    def failing_merge(branch: str, base_branch: str, *, timeout_seconds: int = 20) -> None:
        repo.calls.append(("merge_base_into_active_branch", branch, base_branch, str(timeout_seconds)))
        raise RuntimeError("CONFLICT (content): Merge conflict in specs/001-ai-content-studio/tasks.md")

    repo.merge_base_into_active_branch = failing_merge  # type: ignore[method-assign]
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

    assert result.status == RunStatus.FAILED
    assert "CONFLICT" in (result.reason or "")
    assert not any(call[0] == "reset" for call in repo.calls)
    assert task_pipeline.calls == []


def test_run_epic_reloads_manifest_after_branch_switch_and_skips_activation_when_branch_is_already_active(tmp_path, monkeypatch):
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
    get_epic_calls: list[str] = []

    def fake_get_epic(epic_id: str, directory: Path):
        get_epic_calls.append(repo.current_branch)
        status = "planned" if repo.current_branch == "master" else "active"
        return {
            "id": epic_id,
            "title": "Epic E002",
            "milestone": "M001",
            "feature": "specs/001-ai-content-studio",
            "base_branch": "master",
            "branch": "feature/E002",
            "status": status,
            "risk": "medium",
            "depends_on": ["E001"],
            "tasks": ["T007", "T008"],
            "required_checks": [
                "python -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py",
                "git --no-pager diff --check",
            ],
        }

    monkeypatch.setattr(epic_module, "get_epic", fake_get_epic)

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH))

    assert result.status == RunStatus.COMPLETED
    assert ("create_branch", "feature/E002", "master") in repo.calls
    assert repo.commit_messages == []
    assert get_epic_calls[0] == "master"
    assert "feature/E002" in get_epic_calls
    assert result.task_ids == ("T007", "T008")
    assert task_pipeline.calls == []


def test_verify_task_evidence_passes_for_persisted_commits_without_current_run_results(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="c" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, _, _ = _build_pipeline(tmp_path, repo, github, receipt, {})
    _write_committed_task_state(tmp_path, task_id="T007", sha="1" * 40)
    _write_committed_task_state(tmp_path, task_id="T008", sha="2" * 40)
    _record_task_evidence(repo, task_id="T007", sha="1" * 40, title="storage follow-up")
    _record_task_evidence(repo, task_id="T008", sha="2" * 40, title="manifest follow-up")
    repo.record_commit("c" * 40, "fix(autopilot): tooling follow-up")

    epic_manifest = epic_module.get_epic("E002", tmp_path / ".specify" / "workstreams")

    assert pipeline._verify_task_evidence("E002", epic_manifest, []) == []
    assert [call for call in repo.calls if call[:1] == ("list_commit_history",)] == [("list_commit_history", "HEAD")]


def test_verify_task_evidence_accepts_legacy_commit_history_without_runtime_state(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="c" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, _, _ = _build_pipeline(tmp_path, repo, github, receipt, {})
    _record_task_evidence(repo, task_id="T007", sha="1" * 40, title="task evidence link")
    _record_task_evidence(repo, task_id="T008", sha="2" * 40, title="another evidence link")
    repo.record_commit("c" * 40, "fix(autopilot): tooling follow-up")

    epic_manifest = epic_module.get_epic("E002", tmp_path / ".specify" / "workstreams")

    assert pipeline._verify_task_evidence("E002", epic_manifest, []) == []


def test_verify_task_evidence_rejects_missing_task_commit(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="c" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, _, _ = _build_pipeline(tmp_path, repo, github, receipt, {})
    _write_committed_task_state(tmp_path, task_id="T007", sha="1" * 40)
    _record_task_evidence(repo, task_id="T007", sha="1" * 40, title="Implement epic task 1")
    repo.record_commit("c" * 40, "fix(autopilot): tooling follow-up")

    epic_manifest = epic_module.get_epic("E002", tmp_path / ".specify" / "workstreams")
    errors = pipeline._verify_task_evidence("E002", epic_manifest, [])

    assert any("T008" in error for error in errors)
    assert any("no task evidence found" in error for error in errors)


def test_verify_task_evidence_rejects_commit_outside_head_history(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="c" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, _, _ = _build_pipeline(tmp_path, repo, github, receipt, {})
    _write_committed_task_state(tmp_path, task_id="T007", sha="1" * 40)
    _write_committed_task_state(tmp_path, task_id="T008", sha="9" * 40)
    _record_task_evidence(repo, task_id="T007", sha="1" * 40, title="Implement epic task 1")
    repo.record_commit("c" * 40, "fix(autopilot): tooling follow-up")

    epic_manifest = epic_module.get_epic("E002", tmp_path / ".specify" / "workstreams")
    errors = pipeline._verify_task_evidence("E002", epic_manifest, [])

    assert any("T008" in error for error in errors)
    assert any("missing from HEAD history" in error for error in errors)


def test_verify_task_evidence_rejects_state_and_receipt_sha_mismatch(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="c" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, _, _ = _build_pipeline(tmp_path, repo, github, receipt, {})
    _write_committed_task_state(tmp_path, task_id="T007", sha="1" * 40)
    _write_committed_task_state(tmp_path, task_id="T008", sha="2" * 40)
    receipt_path = tmp_path / ".specify" / "runtime" / "task-receipts" / "T008.json"
    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt_payload["commit_sha"] = "3" * 40
    receipt_path.write_text(json.dumps(receipt_payload, indent=2), encoding="utf-8")
    _record_task_evidence(repo, task_id="T007", sha="1" * 40, title="Implement epic task 1")
    _record_task_evidence(repo, task_id="T008", sha="2" * 40, title="Implement epic task 2")
    repo.record_commit("c" * 40, "fix(autopilot): tooling follow-up")

    epic_manifest = epic_module.get_epic("E002", tmp_path / ".specify" / "workstreams")
    errors = pipeline._verify_task_evidence("E002", epic_manifest, [])

    assert any("T008" in error for error in errors)
    assert any("state.head_sha and receipt.commit_sha differ" in error for error in errors)


def test_verify_task_evidence_rejects_duplicate_task_sha(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="c" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, _, _ = _build_pipeline(tmp_path, repo, github, receipt, {})
    _write_committed_task_state(tmp_path, task_id="T007", sha="1" * 40)
    _write_committed_task_state(tmp_path, task_id="T008", sha="1" * 40)
    repo.record_commit("1" * 40, "feat(T007): Implement epic task 1", files=("backend/app/tooling/local_autopilot/task_pipeline.py",))
    repo.record_commit("1" * 40, "feat(T008): Implement epic task 2", files=("backend/app/tooling/local_autopilot/task_pipeline.py",))
    repo.record_commit("c" * 40, "fix(autopilot): tooling follow-up")

    epic_manifest = epic_module.get_epic("E002", tmp_path / ".specify" / "workstreams")
    errors = pipeline._verify_task_evidence("E002", epic_manifest, [])

    assert any("shared with" in error for error in errors)


def test_verify_task_evidence_accepts_legacy_bundle_with_disjoint_evidence_paths(tmp_path):
    _write_e003_fixture(tmp_path)
    repo = FakeRepository(tmp_path, current_branch="feature/E003", head_sha_value="f" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, _, _ = _build_pipeline(tmp_path, repo, github, receipt, {})
    _seed_e003_supporting_task_evidence(tmp_path, repo)
    _seed_e003_legacy_bundle(
        repo,
        activation_sha="1" * 40,
        bundle_sha="2" * 40,
        tail_sha="f" * 40,
        bundle_subject="docs: add shared legacy evidence",
        bundle_files=(
            "backend/app/domain/workflow_config.py",
            "backend/app/domain/enums.py",
            "backend/tests/unit/test_workflow_config_validation.py",
            ".gitignore",
            ".env.example",
            "README.md",
            "backend/tests/static/test_secret_hygiene.py",
        ),
    )

    epic_manifest = epic_module.get_epic("E003", tmp_path / ".specify" / "workstreams")

    assert pipeline._verify_task_evidence("E003", epic_manifest, []) == []
    assert [call[0] for call in repo.calls].count("find_commits_adding_path") >= 2


def test_verify_task_evidence_rejects_overlapping_legacy_bundle_paths(tmp_path):
    _write_e003_fixture(tmp_path)
    repo = FakeRepository(tmp_path, current_branch="feature/E003", head_sha_value="f" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, _, _ = _build_pipeline(tmp_path, repo, github, receipt, {})
    _seed_e003_supporting_task_evidence(tmp_path, repo)
    tasks_path = tmp_path / "specs" / "001-ai-content-studio" / "tasks.md"
    tasks_text = tasks_path.read_text(encoding="utf-8").replace(
        "backend/tests/static/test_secret_hygiene.py",
        "backend/tests/unit/test_workflow_config_validation.py",
        1,
    )
    tasks_path.write_text(tasks_text, encoding="utf-8")
    _seed_e003_legacy_bundle(
        repo,
        activation_sha="1" * 40,
        bundle_sha="2" * 40,
        tail_sha="f" * 40,
        bundle_subject="docs: add shared legacy evidence",
        bundle_files=(
            "backend/app/domain/workflow_config.py",
            "backend/app/domain/enums.py",
            "backend/tests/unit/test_workflow_config_validation.py",
            ".gitignore",
            ".env.example",
            "README.md",
        ),
    )

    epic_manifest = epic_module.get_epic("E003", tmp_path / ".specify" / "workstreams")
    errors = pipeline._verify_task_evidence("E003", epic_manifest, [])

    assert any("T024" in error for error in errors)
    assert any("legacy evidence paths overlap" in error for error in errors)


def test_verify_task_evidence_rejects_shared_persisted_sha(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="c" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, _, _ = _build_pipeline(tmp_path, repo, github, receipt, {})
    _write_committed_task_state(tmp_path, task_id="T007", sha="1" * 40)
    _write_committed_task_state(tmp_path, task_id="T008", sha="1" * 40)
    repo.record_commit("1" * 40, "fix: shared persisted evidence", files=("specs/001-ai-content-studio/main.py",))
    repo.record_commit("c" * 40, "fix(autopilot): tooling follow-up")

    epic_manifest = epic_module.get_epic("E002", tmp_path / ".specify" / "workstreams")
    errors = pipeline._verify_task_evidence("E002", epic_manifest, [])

    assert any("shared with" in error for error in errors)


def test_verify_task_evidence_rejects_shared_task_id_subject_sha(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="c" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, _, _ = _build_pipeline(tmp_path, repo, github, receipt, {})
    _write_committed_task_state(tmp_path, task_id="T008", sha="1" * 40)
    repo.record_commit("1" * 40, "feat(T007): Implement epic task 1", files=("specs/001-ai-content-studio/main.py",))
    repo.record_commit("c" * 40, "fix(autopilot): tooling follow-up")

    epic_manifest = epic_module.get_epic("E002", tmp_path / ".specify" / "workstreams")
    errors = pipeline._verify_task_evidence("E002", epic_manifest, [])

    assert any("shared with" in error for error in errors)


def test_verify_task_evidence_rejects_legacy_bundle_after_e003_activation(tmp_path):
    _write_e003_fixture(tmp_path)
    repo = FakeRepository(tmp_path, current_branch="feature/E003", head_sha_value="f" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, _, _ = _build_pipeline(tmp_path, repo, github, receipt, {})
    _seed_e003_supporting_task_evidence(tmp_path, repo)
    _seed_e003_legacy_bundle(
        repo,
        activation_sha="1" * 40,
        bundle_sha="2" * 40,
        tail_sha="f" * 40,
        bundle_subject="docs: add shared legacy evidence",
        bundle_files=(
            "backend/app/domain/workflow_config.py",
            "backend/app/domain/enums.py",
            "backend/tests/unit/test_workflow_config_validation.py",
            ".gitignore",
            ".env.example",
            "README.md",
            "backend/tests/static/test_secret_hygiene.py",
        ),
        activation_before_bundle=True,
    )
    repo.record_commit("0" * 40, "docs: post-activation follow-up")
    repo.head_sha_value = "0" * 40

    epic_manifest = epic_module.get_epic("E003", tmp_path / ".specify" / "workstreams")
    errors = pipeline._verify_task_evidence("E003", epic_manifest, [])

    assert any("not older than the E003 activation commit" in error for error in errors)


def test_run_epic_e003_completed_from_persisted_and_legacy_evidence_without_task_pipeline(tmp_path, monkeypatch):
    _write_e003_fixture(tmp_path)
    repo = FakeRepository(tmp_path, current_branch="feature/E003", head_sha_value="f" * 40, master_head_sha_value="0" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {},
        base_sha="0" * 40,
    )
    _write_committed_task_state(tmp_path, task_id="T009", sha="9" * 40, run_id="run-epic-003", branch="feature/E003")
    _write_committed_task_state(tmp_path, task_id="T010", sha="a" * 40, run_id="run-epic-003", branch="feature/E003")
    _write_committed_task_state(tmp_path, task_id="T022", sha="b" * 40, run_id="run-epic-003", branch="feature/E003")
    _write_committed_task_state(tmp_path, task_id="T023", sha="c" * 40, run_id="run-epic-003", branch="feature/E003")
    _record_task_evidence(repo, task_id="T009", sha="9" * 40, title="artifact storage follow-up")
    _record_task_evidence(repo, task_id="T010", sha="a" * 40, title="state machine follow-up")
    _record_task_evidence(repo, task_id="T022", sha="b" * 40, title="registry follow-up")
    _record_task_evidence(repo, task_id="T023", sha="c" * 40, title="provider config follow-up")
    _seed_e003_legacy_bundle(
        repo,
        activation_sha="1" * 40,
        bundle_sha="2" * 40,
        tail_sha="f" * 40,
        bundle_subject="docs: add shared legacy evidence",
        bundle_files=(
            "backend/app/domain/workflow_config.py",
            "backend/app/domain/enums.py",
            "backend/tests/unit/test_workflow_config_validation.py",
            ".gitignore",
            ".env.example",
            "README.md",
            "backend/tests/static/test_secret_hygiene.py",
        ),
    )

    monkeypatch.setattr(repo, "create_branch", lambda branch, *, base_branch="master": repo.calls.append(("create_branch", branch, base_branch)) or setattr(repo, "current_branch", branch))
    monkeypatch.setattr(repo, "merge_base_into_active_branch", lambda branch, base_branch, *, timeout_seconds=20: repo.calls.append(("merge_base_into_active_branch", branch, base_branch, str(timeout_seconds))))

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH, epic_id="E003", branch_name="feature/E003"))

    assert result.status == RunStatus.COMPLETED
    assert result.task_ids == ()
    assert task_pipeline.calls == []
    assert any(call[:1] == ("list_commit_history",) for call in repo.calls)
    assert [call for call in repo.calls if call[:1] == ("list_commit_history",)] == [("list_commit_history", "HEAD")]


def test_run_epic_completed_epic_with_persisted_evidence_skips_task_pipeline_and_completes(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="c" * 40, master_head_sha_value="c" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {},
        base_sha="b" * 40,
    )
    _write_committed_task_state(tmp_path, task_id="T007", sha="1" * 40)
    _write_committed_task_state(tmp_path, task_id="T008", sha="2" * 40)
    _record_task_evidence(repo, task_id="T007", sha="1" * 40, title="Implement epic task 1")
    _record_task_evidence(repo, task_id="T008", sha="2" * 40, title="Implement epic task 2")
    repo.record_commit("c" * 40, "fix(autopilot): tooling follow-up")

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH))

    assert result.status == RunStatus.COMPLETED
    assert result.task_ids == ()
    assert task_pipeline.calls == []
    assert receipt.writes and receipt.validations


def test_run_epic_mixed_persisted_and_current_run_evidence_passes(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=False)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="a" * 40, master_head_sha_value="b" * 40)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {
            "T008": {"status": RunStatus.COMPLETED, "commit_sha": "2" * 40, "title": "Task 8"},
        },
        base_sha="b" * 40,
    )
    _write_committed_task_state(tmp_path, task_id="T007", sha="1" * 40)
    _record_task_evidence(repo, task_id="T007", sha="1" * 40, title="Implement epic task 1")
    repo.record_commit("b" * 40, "base: master sync")

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH))

    assert result.status == RunStatus.COMPLETED
    assert result.task_ids == ("T008",)
    assert task_pipeline.calls == []


def test_run_epic_creates_activation_commit_once_for_existing_planned_branch(tmp_path, monkeypatch):
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

    def fake_get_epic(epic_id: str, directory: Path):
        status = "planned" if repo.current_branch == "master" else "planned"
        return {
            "id": epic_id,
            "title": "Epic E002",
            "milestone": "M001",
            "feature": "specs/001-ai-content-studio",
            "base_branch": "master",
            "branch": "feature/E002",
            "status": status,
            "risk": "medium",
            "depends_on": ["E001"],
            "tasks": ["T007", "T008"],
            "required_checks": [
                "python -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py",
                "git --no-pager diff --check",
            ],
        }

    monkeypatch.setattr(epic_module, "get_epic", fake_get_epic)

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH), human_authorized=True)

    assert result.status == RunStatus.COMPLETED
    assert repo.commit_messages == ["feat(E002): activate epic"]
    assert ("create_branch", "feature/E002", "master") in repo.calls
    assert result.activation_commit_sha == f"{1:040x}"[-40:]
    assert task_pipeline.calls == []


def test_run_epic_rejects_completed_branch_after_switch(tmp_path, monkeypatch):
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

    def fake_get_epic(epic_id: str, directory: Path):
        status = "planned" if repo.current_branch == "master" else "completed"
        return {
            "id": epic_id,
            "title": "Epic E002",
            "milestone": "M001",
            "feature": "specs/001-ai-content-studio",
            "base_branch": "master",
            "branch": "feature/E002",
            "status": status,
            "risk": "medium",
            "depends_on": ["E001"],
            "tasks": ["T007", "T008"],
            "required_checks": [
                "python -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py",
                "git --no-pager diff --check",
            ],
        }

    monkeypatch.setattr(epic_module, "get_epic", fake_get_epic)

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH), human_authorized=True)

    assert result.status == RunStatus.COMPLETED
    assert result.task_ids == ("T007", "T008")
    assert repo.commit_messages == []
    assert task_pipeline.calls == []
    assert github.calls == []


def test_run_epic_propagates_activation_commit_failure(tmp_path, monkeypatch):
    _setup_repo(tmp_path, epic_status="planned", dependency_status="completed")
    repo = FakeRepository(tmp_path, commit_should_fail=True)
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

    def fake_get_epic(epic_id: str, directory: Path):
        status = "planned" if repo.current_branch == "master" else "planned"
        return {
            "id": epic_id,
            "title": "Epic E002",
            "milestone": "M001",
            "feature": "specs/001-ai-content-studio",
            "base_branch": "master",
            "branch": "feature/E002",
            "status": status,
            "risk": "medium",
            "depends_on": ["E001"],
            "tasks": ["T007", "T008"],
            "required_checks": [
                "python -m pytest backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py",
                "git --no-pager diff --check",
            ],
        }

    monkeypatch.setattr(epic_module, "get_epic", fake_get_epic)

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH), human_authorized=True)

    assert result.status == RunStatus.FAILED
    assert "activation commit failed" in (result.reason or "")
    assert repo.commit_messages == ["feat(E002): activate epic"]
    assert task_pipeline.calls == []


def test_run_epic_rejects_diverged_existing_branch(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed")
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="a" * 40, master_head_sha_value="b" * 40, diverged=True)
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

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH))

    assert result.status == RunStatus.COMPLETED
    assert ("merge_base_into_active_branch", "feature/E002", "master", "180") in repo.calls
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
    assert github.calls == [("validate_auth", "180"), ("find_pr", "master", "feature/E002")]
    assert "review failed" in (result.reason or "")
    assert not validation_receipt_module.validation_receipt_path("2" * 40, tmp_path).exists()


def test_run_epic_reports_required_checks_failed_when_first_check_fails(tmp_path):
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

    def fake_run_required_checks(epic_manifest, command_results, *, cancel_event):
        return (
            CommandResult(
                command=("python", "-m", "pytest", "backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py"),
                status="FAIL",
                exit_code=1,
                duration_ms=1,
                timed_out=False,
            ),
            CommandResult(
                command=("git", "--no-pager", "diff", "--check"),
                status="PASS",
                exit_code=0,
                duration_ms=1,
                timed_out=False,
            ),
        )

    pipeline._run_required_checks = fake_run_required_checks  # type: ignore[method-assign]

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH), human_authorized=True)

    assert result.status == RunStatus.FAILED
    assert "required checks failed" in (result.reason or "")
    assert "declared=2 actual=1" not in (result.reason or "")
    assert any(command_result.status == "FAIL" for command_result in result.command_results)
    assert any(command_result.command == ("python", "-m", "pytest", "backend/tests/unit/tooling/local_autopilot/test_epic_pipeline.py") for command_result in result.command_results)
    assert not receipt.writes
    assert task_pipeline.calls == []


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
    assert repo.validate_remote_calls == []
    assert result.pull_request is None


def test_run_epic_full_fails_early_when_gh_is_missing_before_tasks(tmp_path):
    _setup_repo(tmp_path, epic_status="planned", dependency_status="completed")
    repo = FakeRepository(tmp_path)
    github = FakeGitHubAdapter(auth_available=False, auth_authenticated=False)
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
    assert "gh" in (result.reason or "").lower()
    assert task_pipeline.calls == []
    assert github.calls == [("validate_auth", "180")]


def test_run_epic_full_fails_before_tasks_when_origin_is_missing(tmp_path):
    _setup_repo(tmp_path, epic_status="planned", dependency_status="completed")
    repo = FakeRepository(tmp_path, remote_should_fail=True)
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
    assert "origin" in (result.reason or "").lower()
    assert task_pipeline.calls == []
    assert github.calls == [("validate_auth", "180")]
    assert repo.validate_remote_calls == [("origin", 180, 20)]


def test_run_epic_passes_configured_push_timeout_to_repository(tmp_path):
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
        config=_config(push_timeout_seconds=777),
    )

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)

    assert result.status == RunStatus.WAITING_FOR_MERGE
    assert repo.push_calls == [("feature/E002", "origin", 777)]
    assert github.find_pr_calls == [("master", "feature/E002", 180), ("master", "feature/E002", 180)]
    receipt_path = validation_receipt_module.validation_receipt_path("2" * 40, tmp_path)
    assert receipt_path.is_file()
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["head_sha"] == "2" * 40
    assert payload["branch"] == "feature/E002"
    assert payload["status"] == "PASS"
    assert [check["name"] for check in payload["checks"]] == ["pytest_full", "git_diff_check"]
    assert payload["checks"][0]["status"] == "PASS"
    assert payload["checks"][0]["exit_code"] == 0


def test_run_epic_pauses_when_auto_push_is_disabled(tmp_path):
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
        config=_config(auto_push=False),
    )

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)

    assert result.status == RunStatus.PAUSED
    assert result.run.status == RunStatus.PAUSED
    assert result.reason == "epic validated; automatic push disabled"
    assert repo.push_calls == []
    assert all(call[0] != "create_pr" for call in github.calls)
    receipt_path = validation_receipt_module.validation_receipt_path("2" * 40, tmp_path)
    assert receipt_path.is_file()


def test_run_epic_pauses_when_draft_pr_creation_is_disabled(tmp_path):
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
        config=_config(create_draft_pr=False),
    )

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)

    assert result.status == RunStatus.PAUSED
    assert result.run.status == RunStatus.PAUSED
    assert result.reason == "branch pushed; automatic PR creation disabled"
    assert repo.push_calls == [("feature/E002", "origin", 1200)]
    assert all(call[0] != "create_pr" for call in github.calls)


def test_run_epic_pauses_after_reaching_max_tasks_per_run(tmp_path):
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
        config=_config(auto_push=False, max_tasks_per_run=1),
    )
    run = _make_run(tmp_path, run_mode=RunMode.FULL)
    run = AutopilotRun(
        run_id=run.run_id,
        request=run.request,
        status=run.status,
        created_at=run.created_at,
        updated_at=run.updated_at,
        epic_id=run.epic_id,
        branch_name=run.branch_name,
        task_results=(
            TaskResult(
                task_id="T007",
                status=RunStatus.COMPLETED,
                commit_sha="1" * 40,
                title="Task 7",
            ),
        ),
    )

    result = pipeline.run_epic(run, human_authorized=True)

    assert result.status == RunStatus.PAUSED
    assert result.run.status == RunStatus.PAUSED
    assert result.reason == "max_tasks_per_run reached: 1"
    assert task_pipeline.calls == []
    assert repo.push_calls == []
    assert all(call[0] != "create_pr" for call in github.calls)


def test_run_epic_push_failure_blocks_pr_creation(tmp_path):
    _setup_repo(tmp_path, epic_status="planned", dependency_status="completed")
    repo = FakeRepository(tmp_path, push_result=ProcessResult(
        command=("git", "push", "-u", "origin", "feature/E002"),
        status="TIMEOUT",
        exit_code=None,
        duration_ms=1200,
        timed_out=True,
        cancelled=False,
        stdout_lines=(),
        stderr_lines=("pre-push pytest_full timed out after 900s",),
        output_truncated=False,
        process_tree_killed=True,
        pid=1234,
    ))
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
    assert github.calls == [("validate_auth", "180"), ("find_pr", "master", "feature/E002")]
    assert "status=TIMEOUT" in (result.reason or "")
    assert "timeout=1200s" in (result.reason or "")
    assert "pre-push pytest_full timed out after 900s" in (result.reason or "")
    push_result_path = tmp_path / ".specify" / "runtime" / "runs" / "run-epic-e002" / "push-result.json"
    payload = json.loads(push_result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "TIMEOUT"
    assert payload["timed_out"] is True
    assert payload["head_sha"] == "2" * 40
    assert payload["branch"] == "feature/E002"


def test_build_push_failure_reason_masks_token_like_values():
    result = ProcessResult(
        command=("git", "push", "-u", "origin", "feature/E002"),
        status="FAIL",
        exit_code=1,
        duration_ms=12,
        timed_out=False,
        cancelled=False,
        stdout_lines=(),
        stderr_lines=("remote rejected branch token=ghp_secret_value",),
        output_truncated=False,
        process_tree_killed=False,
        pid=1234,
    )

    reason = epic_module.build_push_failure_reason(result, timeout_seconds=1200)

    assert "ghp_secret_value" not in reason
    assert "[REDACTED]" in reason


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
    assert github.find_pr_calls == [("master", "feature/E002", 180)]
    assert all(call[0] != "create_draft_pr" for call in github.calls)


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
    assert github.find_pr_calls == [("master", "feature/E002", 180), ("master", "feature/E002", 180)]
    assert github.calls[-1][:2] == ("create_pr", "master")


def test_run_epic_full_returns_waiting_for_merge_when_implementation_pr_is_open(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed")
    repo = FakeRepository(tmp_path)
    open_pr = PullRequestInfo(
        number=17,
        url="https://example.invalid/pr/17",
        title="E002: Epic E002",
        base_branch="master",
        head_branch="feature/E002",
        draft=True,
        merged=False,
    )
    github = FakeGitHubAdapter(prs_by_branch={("master", "feature/E002"): open_pr})
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(tmp_path, repo, github, receipt, {})

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)

    assert result.status == RunStatus.WAITING_FOR_MERGE
    assert result.pull_request == open_pr
    assert task_pipeline.calls == []
    assert repo.pushed_branches == []
    assert github.find_pr_calls == [("master", "feature/E002", 180)]


def test_run_epic_full_creates_closure_branch_and_pr_after_implementation_pr_is_merged(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path)
    _seed_closure_validation_data(tmp_path, repo)
    implementation_pr = PullRequestInfo(
        number=17,
        url="https://example.invalid/pr/17",
        title="E002: Epic E002",
        base_branch="master",
        head_branch="feature/E002",
        draft=True,
        merged=True,
    )
    github = FakeGitHubAdapter(prs_by_branch={("master", "feature/E002"): implementation_pr})
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(tmp_path, repo, github, receipt, {})
    manifest_path = tmp_path / ".specify" / "workstreams" / "E002.yml"
    tasks_path = tmp_path / "specs" / "001-ai-content-studio" / "tasks.md"
    tasks_before = tasks_path.read_text(encoding="utf-8")

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)

    assert result.status == RunStatus.WAITING_FOR_MERGE
    assert result.branch_name == "chore/close-E002"
    assert result.pull_request is not None
    assert result.pull_request.head_branch == "chore/close-E002"
    assert result.implementation_pull_request == implementation_pr
    assert task_pipeline.calls == []
    assert repo.commit_messages[-1] == "chore(E002): mark epic completed"
    assert repo.pushed_branches == ["chore/close-E002"]
    assert repo.staged_paths[-1] == (".specify/workstreams/E002.yml",)
    assert "status: completed" in manifest_path.read_text(encoding="utf-8")
    assert tasks_path.read_text(encoding="utf-8") == tasks_before
    assert github.find_pr_calls == [("master", "feature/E002", 180)]
    assert github.create_pr_calls == [("master", "chore/close-E002", False, 180)]


def test_run_epic_existing_closure_pr_is_reused_without_duplicate_commit(tmp_path):
    _setup_repo(tmp_path, epic_status="completed", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path)
    _seed_closure_validation_data(tmp_path, repo)
    closure_pr = PullRequestInfo(
        number=33,
        url="https://example.invalid/pr/33",
        title="chore(E002): mark epic completed",
        base_branch="master",
        head_branch="chore/close-E002",
        draft=False,
        merged=False,
    )
    github = FakeGitHubAdapter(prs_by_branch={("master", "chore/close-E002"): closure_pr})
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(tmp_path, repo, github, receipt, {})

    first = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)
    second = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)

    assert first.status == RunStatus.WAITING_FOR_MERGE
    assert second.status == RunStatus.WAITING_FOR_MERGE
    assert first.pull_request == closure_pr
    assert second.pull_request == closure_pr
    assert task_pipeline.calls == []
    assert repo.commit_messages == []
    assert github.create_pr_calls == []


def test_run_epic_merged_closure_pr_completes_and_clears_active_epic(tmp_path):
    _setup_repo(tmp_path, epic_status="completed", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path)
    _seed_closure_validation_data(tmp_path, repo)
    closure_pr = PullRequestInfo(
        number=34,
        url="https://example.invalid/pr/34",
        title="chore(E002): mark epic completed",
        base_branch="master",
        head_branch="chore/close-E002",
        draft=False,
        merged=True,
    )
    github = FakeGitHubAdapter(prs_by_branch={("master", "chore/close-E002"): closure_pr})
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(tmp_path, repo, github, receipt, {})

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)

    assert result.status == RunStatus.COMPLETED
    assert result.pull_request == closure_pr
    assert task_pipeline.calls == []
    assert repo.commit_messages == []
    assert not (tmp_path / ".specify" / "runtime" / "active-epic").exists()
    archive_root = tmp_path / ".specify" / "runtime" / "archive" / "E002"
    assert archive_root.exists()
    archived_dirs = sorted(path.name for path in archive_root.iterdir() if path.is_dir())
    assert archived_dirs
    assert (tmp_path / ".specify" / "runtime" / "task-receipts" / "T007.json").is_file()
    assert (tmp_path / ".specify" / "runtime" / "task-receipts" / "T008.json").is_file()


def test_run_epic_closure_blocks_when_task_sha_is_not_in_master_history(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path)
    _write_committed_task_state(tmp_path, task_id="T007", sha="1" * 40)
    _write_committed_task_state(tmp_path, task_id="T008", sha="2" * 40)
    repo.record_commit("9" * 40, "merge implementation")
    repo.head_sha_value = "9" * 40
    repo.master_head_sha_value = "9" * 40
    implementation_pr = PullRequestInfo(
        number=17,
        url="https://example.invalid/pr/17",
        title="E002: Epic E002",
        base_branch="master",
        head_branch="feature/E002",
        draft=True,
        merged=True,
    )
    github = FakeGitHubAdapter(prs_by_branch={("master", "feature/E002"): implementation_pr})
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(tmp_path, repo, github, receipt, {})

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)

    assert result.status == RunStatus.FAILED
    assert task_pipeline.calls == []
    assert "not an ancestor" in (result.reason or "")
    assert not repo.pushed_branches


def test_run_epic_closure_blocks_when_checkboxes_are_incomplete(tmp_path):
    _setup_repo(tmp_path, epic_status="active", dependency_status="completed", task7_checked=False, task8_checked=True)
    repo = FakeRepository(tmp_path)
    _seed_closure_validation_data(tmp_path, repo)
    implementation_pr = PullRequestInfo(
        number=17,
        url="https://example.invalid/pr/17",
        title="E002: Epic E002",
        base_branch="master",
        head_branch="feature/E002",
        draft=True,
        merged=True,
    )
    github = FakeGitHubAdapter(prs_by_branch={("master", "feature/E002"): implementation_pr})
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(tmp_path, repo, github, receipt, {})

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.FULL), human_authorized=True)

    assert result.status == RunStatus.FAILED
    assert task_pipeline.calls == []
    assert "checkbox" in (result.reason or "").lower()
    assert not repo.pushed_branches


def test_run_epic_completed_epic_returns_completed_without_tasks(tmp_path):
    _setup_repo(tmp_path, epic_status="completed", dependency_status="completed")
    repo = FakeRepository(tmp_path)
    github = FakeGitHubAdapter()
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, _ = _build_pipeline(tmp_path, repo, github, receipt, {})

    result = pipeline.run_epic(_make_run(tmp_path, run_mode=RunMode.STOP_BEFORE_PUSH), human_authorized=True)

    assert result.status == RunStatus.COMPLETED
    assert result.task_ids == ("T007", "T008")
    assert task_pipeline.calls == []
    assert repo.commit_messages == []
    assert github.calls == []


def test_retry_push_uses_validation_receipt_without_rerunning_tasks_or_codex(tmp_path):
    _setup_repo(tmp_path, epic_status="planned", dependency_status="completed", task7_checked=True, task8_checked=True)
    repo = FakeRepository(tmp_path, current_branch="feature/E002", head_sha_value="2" * 40)
    existing_pr = PullRequestInfo(
        number=17,
        url="https://example.invalid/pr/17",
        title="E002: Epic E002",
        base_branch="master",
        head_branch="feature/E002",
    )
    github = FakeGitHubAdapter(existing_pr=existing_pr)
    receipt = FakeReviewReceipt(tmp_path)
    pipeline, task_pipeline, process = _build_pipeline(
        tmp_path,
        repo,
        github,
        receipt,
        {},
    )
    validation_receipt_module.write_validation_receipt(
        head_sha="2" * 40,
        branch="feature/E002",
        python_executable=sys.executable,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        status="PASS",
        checks=[
            {"name": "pytest_full", "command": ["python", "-m", "pytest"], "status": "PASS", "exit_code": 0, "duration_ms": 123},
            {"name": "git_diff_check", "command": ["git", "--no-pager", "diff", "--check"], "status": "PASS", "exit_code": 0, "duration_ms": 12},
        ],
        root=tmp_path,
    )
    run = _make_run(tmp_path, run_mode=RunMode.FULL)
    run = AutopilotRun(
        run_id=run.run_id,
        request=run.request,
        status=RunStatus.FAILED,
        created_at=run.created_at,
        updated_at=run.updated_at,
        epic_id=run.epic_id,
        branch_name=run.branch_name,
        task_results=(
            TaskResult(
                task_id="T007",
                status=RunStatus.COMPLETED,
                commit_sha="2" * 40,
                title="Task 7",
            ),
        ),
        command_results=(),
        pull_request=None,
        last_error="push failed: status=TIMEOUT timeout=1200s command=git push -u origin feature/E002",
    )

    result = pipeline.retry_push(run)

    assert result.status == RunStatus.WAITING_FOR_MERGE
    assert task_pipeline.calls == []
    assert process.calls == []
    assert github.find_pr_calls == [("master", "feature/E002", 180)]
    assert all(call[0] != "create_draft_pr" for call in github.calls)
    assert result.pull_request == existing_pr


def test_validation_receipt_rejects_stale_head_after_sync(tmp_path):
    receipt_path = validation_receipt_module.write_validation_receipt(
        head_sha="1" * 40,
        branch="feature/E002",
        python_executable=sys.executable,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        status="PASS",
        checks=[
            {"name": "pytest_full", "command": ["python", "-m", "pytest"], "status": "PASS", "exit_code": 0, "duration_ms": 123},
        ],
        root=tmp_path,
    )

    errors = validation_receipt_module.validate_receipt_for_head(
        receipt_path,
        current_head_sha="2" * 40,
        current_branch="feature/E002",
        repo_clean=True,
        current_python_version=(sys.version_info.major, sys.version_info.minor),
    )

    assert errors
    assert any("head_sha" in error for error in errors)


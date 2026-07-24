from __future__ import annotations

import builtins
import importlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.tooling.local_autopilot.config import AutopilotConfig
from app.tooling.local_autopilot.epic_pipeline import EpicPipeline
from app.tooling.local_autopilot.github_adapter import GitHubAdapter, GitHubAuthResult
from app.tooling.local_autopilot.main import validate_startup_environment
from app.tooling.local_autopilot.milestone_pipeline import MilestonePipeline
from app.tooling.local_autopilot.models import AutopilotRequest, AutopilotRun, PullRequestInfo, RunMode, RunStatus, ScopeType
from app.tooling.local_autopilot.process_runner import ProcessResult
from app.tooling.local_autopilot.repository import Repository
from app.tooling.local_autopilot.state_store import load_run_state, save_run_state
from app.tooling.local_autopilot.task_pipeline import TaskPipeline


@dataclass
class SimState:
    branch: str = "master"
    head_sha: str = "a" * 40
    base_sha: str = "a" * 40
    clean: bool = True
    dirty_paths: tuple[str, ...] = ()
    branch_exists: set[str] | None = None
    commit_fail: bool = False
    push_fail: bool = False
    pull_fail: bool = False
    agent_python_ok: bool = True
    codex_cli_ok: bool = True
    codex_exec_ok: bool = True
    gh_ok: bool = True
    gh_auth_ok: bool = True
    codex_mode: str = "pass"
    invalid_codex_json: bool = False
    scope_drift_after_codex: bool = False
    cancel_during_codex: bool = False
    timeout_during_codex: bool = False
    codex_prompt_task: str | None = None
    codex_attempts: int = 0
    commit_index: int = 0
    diff_checks: list[str] | None = None
    prs_by_branch: dict[tuple[str, str], PullRequestInfo] | None = None
    prs_by_number: dict[int, dict[str, object]] | None = None
    next_pr_number: int = 500

    def __post_init__(self) -> None:
        self.branch_exists = set(self.branch_exists or ())
        self.diff_checks = list(self.diff_checks or [])
        self.prs_by_branch = dict(self.prs_by_branch or {})
        self.prs_by_number = dict(self.prs_by_number or {})


class SimulatedShell:
    def __init__(self, root: Path, state: SimState, *, python_executable: str) -> None:
        self.root = root
        self.state = state
        self.python_executable = python_executable
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, **kwargs):
        command = tuple(str(part) for part in argv)
        self.calls.append(command)

        if command == ("git", "config", "--local", "--get", "agent.python"):
            if not self.state.agent_python_ok:
                return self._result(command, status="FAIL", exit_code=1)
            return self._result(command, stdout=(self.python_executable,))

        if command == ("git", "status", "--porcelain=v1", "--branch", "--untracked-files=all"):
            return self._status_result(command)

        if command == ("git", "rev-parse", "HEAD"):
            return self._result(command, stdout=(self.state.head_sha,))

        if command[:2] == ("git", "rev-parse") and command != ("git", "rev-parse", "HEAD"):
            ref = command[2] if len(command) > 2 else "HEAD"
            sha = self.state.head_sha if ref == "HEAD" else self.state.base_sha
            return self._result(command, stdout=(sha,))

        if command[:4] == ("git", "show-ref", "--verify", "--quiet"):
            branch = command[-1].removeprefix("refs/heads/")
            return self._result(command, status="PASS" if branch in self.state.branch_exists else "FAIL", exit_code=0 if branch in self.state.branch_exists else 1)

        if command[:2] == ("git", "switch"):
            if command == ("git", "switch", "master"):
                self.state.branch = "master"
                return self._result(command)
            if len(command) == 5 and command[2] == "-c":
                branch = command[3]
                self.state.branch = branch
                self.state.branch_exists.add(branch)
                return self._result(command)
            if len(command) == 3:
                self.state.branch = command[2]
                return self._result(command)

        if command == ("git", "pull", "--ff-only", "origin", "master"):
            if self.state.pull_fail:
                return self._result(command, status="FAIL", exit_code=1)
            return self._result(command)

        if command[:3] == ("git", "add", "--"):
            return self._result(command)

        if command == ("git", "--no-pager", "diff", "--check"):
            cached = False
            return self._diff_result(command, cached=cached)

        if command == ("git", "--no-pager", "diff", "--cached", "--check"):
            cached = True
            return self._diff_result(command, cached=cached)

        if command[:2] == ("git", "commit"):
            if self.state.commit_fail:
                return self._result(command, status="FAIL", exit_code=1)
            self.state.clean = True
            self.state.commit_index += 1
            self.state.head_sha = f"{self.state.commit_index:040x}"[-40:]
            return self._result(command)

        if command[:2] == ("git", "push"):
            if self.state.push_fail:
                return self._result(command, status="FAIL", exit_code=1)
            return self._result(command)

        if command[:3] == ("git", "--no-pager", "diff") and "--check" in command:
            return self._result(command)

        if command == ("git", "--no-pager", "diff", "--cached", "--check"):
            return self._result(command)

        if command == ("codex", "--help"):
            if not self.state.codex_cli_ok:
                return self._result(command, status="MISSING", exit_code=None)
            return self._result(command, stdout=("Codex CLI",))

        if command == ("codex", "exec", "--help"):
            if not self.state.codex_exec_ok:
                return self._result(command, status="FAIL", exit_code=1)
            return self._result(command, stdout=("Run Codex non-interactively",))

        if command[:2] == ("codex", "exec") and "--help" not in command:
            self.state.codex_attempts += 1
            prompt = command[-1]
            match = re.search(r"Selected task:\s*(T\d{3}[A-Z]?)", prompt)
            self.state.codex_prompt_task = match.group(1) if match else None
            if self.state.timeout_during_codex:
                return self._result(command, status="TIMEOUT", exit_code=None)
            if self.state.cancel_during_codex:
                cancel_event = kwargs.get("cancel_event")
                if cancel_event is not None:
                    cancel_event.set()
                return self._result(command, status="CANCELLED", exit_code=None, timed_out=False, cancelled=True)
            if self.state.invalid_codex_json:
                return self._result(command, stdout=("noise", "AUTOPILOT_RESULT_JSON", "{bad-json"), status="PASS")
            payload = {"status": "PASS", "task_id": self.state.codex_prompt_task}
            return self._result(command, stdout=("working", "AUTOPILOT_RESULT_JSON", json.dumps(payload)))

        if command == ("gh", "auth", "status"):
            if not self.state.gh_ok:
                return self._result(command, status="MISSING", exit_code=None)
            if not self.state.gh_auth_ok:
                return self._result(command, status="FAIL", exit_code=1, stdout=("403 Forbidden",))
            return self._result(command, stdout=("github.com", "logged in as tester"))

        if command[:3] == ("gh", "pr", "list"):
            if not self.state.gh_ok:
                return self._result(command, status="MISSING", exit_code=None)
            base = command[command.index("--base") + 1]
            head = command[command.index("--head") + 1]
            items = []
            pr = self.state.prs_by_branch.get((base, head))
            if pr is not None:
                items.append(
                    {
                        "number": pr.number,
                        "url": pr.url,
                        "title": pr.title,
                        "baseRefName": pr.base_branch,
                        "headRefName": pr.head_branch,
                        "isDraft": pr.draft,
                        "state": "OPEN",
                        "mergedAt": "2026-07-23T10:00:00Z" if pr.merged else None,
                    }
                )
            return self._result(command, stdout=(json.dumps(items),))

        if command[:3] == ("gh", "pr", "create"):
            if not self.state.gh_ok:
                return self._result(command, status="MISSING", exit_code=None)
            base = command[command.index("--base") + 1]
            head = command[command.index("--head") + 1]
            title = command[command.index("--title") + 1]
            existing = self.state.prs_by_branch.get((base, head))
            if existing is not None:
                return self._result(command, stdout=(existing.url,))
            number = self.state.next_pr_number
            self.state.next_pr_number += 1
            created = PullRequestInfo(
                number=number,
                url=f"https://example.invalid/pr/{number}",
                title=title,
                base_branch=base,
                head_branch=head,
                draft=True,
                merged=False,
            )
            self.state.prs_by_branch[(base, head)] = created
            self.state.prs_by_number[number] = {
                "number": number,
                "url": created.url,
                "title": title,
                "baseRefName": base,
                "headRefName": head,
                "isDraft": True,
                "state": "OPEN",
                "mergedAt": None,
                "headRefOid": self.state.head_sha,
                "baseRefOid": "a" * 40,
            }
            return self._result(command, stdout=(created.url,))

        if command[:3] == ("gh", "pr", "view"):
            if not self.state.gh_ok:
                return self._result(command, status="MISSING", exit_code=None)
            number = int(command[3])
            payload = self.state.prs_by_number.get(number)
            if payload is None:
                return self._result(command, status="FAIL", exit_code=1, stdout=("not found",))
            return self._result(command, stdout=(json.dumps(payload),))

        if command[:2] == (self.python_executable, "-m") and "backend.app.tooling.agent_task_preflight" in command:
            selector = "T007"
            if "--selector" in command:
                selector = command[command.index("--selector") + 1]
            baseline_path = self.root / ".specify" / "runtime" / "task-runs" / "T900" / "baseline.json"
            baseline_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "task": "T900",
                        "epic": "E002",
                        "branch": self.state.branch,
                        "head_sha": self.state.head_sha,
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
            payload = {
                "status": "PASS",
                "exit_code": 0,
                "task_id": selector,
                "epic_id": "E002",
                "branch": self.state.branch,
                "baseline_path": str(baseline_path),
                "duration_ms": 1,
            }
            return self._result(command, stdout=(json.dumps(payload),))

        if command[:2] == (self.python_executable, "-m") and "pytest" in command:
            return self._result(command, stdout=("ok",))

        raise AssertionError(f"unexpected command: {command}")

    def _status_result(self, command: tuple[str, ...]) -> ProcessResult:
        lines = [f"## {self.state.branch}"]
        if self.state.clean:
            if self.state.codex_attempts and self.state.scope_drift_after_codex:
                lines.extend(f" M {path}" for path in self.state.dirty_paths or ("backend/other.py",))
        else:
            lines.extend(f" M {path}" for path in self.state.dirty_paths or ("backend/dirty.py",))
        return self._result(command, stdout=tuple(lines))

    def _diff_result(self, command: tuple[str, ...], *, cached: bool) -> ProcessResult:
        if self.state.diff_checks:
            status = self.state.diff_checks.pop(0)
            return self._result(command, status=status, exit_code=0 if status == "PASS" else 1)
        return self._result(command)

    def _result(
        self,
        command: tuple[str, ...],
        *,
        status: str = "PASS",
        exit_code: int | None = 0,
        stdout: tuple[str, ...] = (),
        stderr: tuple[str, ...] = (),
        timed_out: bool = False,
        cancelled: bool = False,
    ) -> ProcessResult:
        return ProcessResult(
            command=command,
            status=status,
            exit_code=exit_code,
            duration_ms=5,
            timed_out=timed_out,
            cancelled=cancelled,
            stdout_lines=stdout,
            stderr_lines=stderr,
            output_truncated=False,
            process_tree_killed=timed_out or cancelled,
            pid=4321,
        )


def _write(path: Path, text: str, *, newline: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if newline and not text.endswith("\n"):
        text = f"{text}\n"
    path.write_text(text, encoding="utf-8")


def _write_repo(tmp_path: Path, *, implementation_newline: bool = True, epic2_status: str = "planned", epic1_status: str = "completed") -> None:
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
                "goal: deliver",
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
                f"status: {epic1_status}",
                "risk: low",
                "depends_on: []",
                "tasks:",
                "  - T001",
                "required_checks:",
                r"  - D:\Projects\ai-content-generation\.venv\Scripts\python.exe -m pytest backend/tests/unit/tooling/local_autopilot/test_autopilot_hardening.py",
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
                f"status: {epic2_status}",
                "risk: medium",
                "depends_on:",
                "  - E001",
                "tasks:",
                "  - T007",
                "  - T008",
                "required_checks:",
                r"  - D:\Projects\ai-content-generation\.venv\Scripts\python.exe -m pytest backend/tests/unit/tooling/local_autopilot/test_autopilot_hardening.py",
                "  - git --no-pager diff --check",
                "pr_policy:",
                "  one_pr_per_epic: true",
                "merge_requires_human: true",
                "auto_merge: false",
                "commit_policy:",
                "  one_commit_per_task: true",
                "commit_requires_human: true",
                "auto_commit: false",
                "",
            ]
        ),
    )
    _write(
        feature_dir / "tasks.md",
        "\n".join(
            [
                "- [ ] T001 E001 task",
                "Milestone: M001",
                "Epic: E001",
                "Risk: low",
                "Implementation files: specs/001-ai-content-studio/main.py",
                "Test files: specs/001-ai-content-studio/test_autopilot_hardening.py",
                "Validation commands: python -m pytest backend/tests/unit/tooling/local_autopilot/test_autopilot_hardening.py",
                "Acceptance criteria: complete",
                "Dependencies: None",
                "",
                "- [ ] T007 E002 task one",
                "Milestone: M001",
                "Epic: E002",
                "Risk: medium",
                "Implementation files: specs/001-ai-content-studio/main.py",
                "Test files: specs/001-ai-content-studio/test_autopilot_hardening.py",
                "Validation commands: python -m pytest backend/tests/unit/tooling/local_autopilot/test_autopilot_hardening.py",
                "Acceptance criteria: complete",
                "Dependencies: None",
                "",
                "- [ ] T008 E002 task two",
                "Milestone: M001",
                "Epic: E002",
                "Risk: medium",
                "Implementation files: specs/001-ai-content-studio/main.py",
                "Test files: specs/001-ai-content-studio/test_autopilot_hardening.py",
                "Validation commands: python -m pytest backend/tests/unit/tooling/local_autopilot/test_autopilot_hardening.py",
                "Acceptance criteria: complete",
                "Dependencies: T007",
                "",
            ]
        ),
    )
    _write(
        feature_dir / "main.py",
        "print('autopilot')\n",
        newline=implementation_newline,
    )
    _write(
        feature_dir / "test_autopilot_hardening.py",
        "print('autopilot-tests')\n",
    )
    _write(
        runtime / "baseline.json",
        json.dumps(
            {
                "schema_version": 1,
                "task": "T900",
                "epic": "E002",
                "branch": "epic/E002",
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


def _config() -> AutopilotConfig:
    return AutopilotConfig(
        auto_commit=True,
        auto_push=True,
        create_draft_pr=True,
        auto_merge=False,
        deploy=False,
        max_repair_cycles=2,
        max_tasks_per_run=20,
        command_timeout_seconds=180,
        codex_timeout_seconds=3600,
        closure_mode="pull_request",
    )


def _make_run(
    tmp_path: Path,
    *,
    scope_type: ScopeType = ScopeType.EPIC,
    scope_id: str = "E002",
    status: RunStatus = RunStatus.PREFLIGHT,
    epic_id: str | None = "E002",
    milestone_id: str | None = "M001",
    branch_name: str = "epic/E002",
    pull_request: PullRequestInfo | None = None,
) -> AutopilotRun:
    request = AutopilotRequest(scope_type=scope_type, scope_id=scope_id, run_mode=RunMode.FULL, repo_path=str(tmp_path))
    return AutopilotRun(
        run_id="run-hardening",
        request=request,
        status=status,
        created_at="2026-07-23T12:00:00Z",
        updated_at="2026-07-23T12:00:00Z",
        epic_id=epic_id if scope_type is ScopeType.EPIC else epic_id,
        milestone_id=milestone_id if scope_type is ScopeType.MILESTONE else milestone_id,
        branch_name=branch_name,
        pull_request=pull_request,
    )


def test_end_to_end_epic_full_flow_creates_draft_pr(tmp_path):
    _write_repo(tmp_path, epic2_status="planned")
    state = SimState(branch="epic/E002")
    shell = SimulatedShell(tmp_path, state, python_executable=r"D:\Projects\ai-content-generation\.venv\Scripts\python.exe")
    repo = Repository(tmp_path, process_runner_fn=shell)
    task_pipeline = TaskPipeline(tmp_path, config=_config(), process_runner_fn=shell, repository=repo)
    github = GitHubAdapter(tmp_path, process_runner_fn=shell)
    pipeline = EpicPipeline(tmp_path, config=_config(), repository=repo, task_pipeline_factory=lambda root, config, runner: task_pipeline, github_adapter=github, process_runner_fn=shell)

    result = pipeline.run_epic(_make_run(tmp_path), human_authorized=True)

    assert result.status == RunStatus.WAITING_FOR_MERGE
    assert result.task_ids == ("T007", "T008")
    assert result.pull_request is not None
    assert result.pull_request.draft is True
    assert state.branch == "epic/E002"
    assert load_run_state("run-hardening", root=tmp_path).status == RunStatus.WAITING_FOR_MERGE


def test_end_to_end_resume_after_restart_and_milestone_closure(tmp_path):
    _write_repo(tmp_path, epic2_status="planned")
    state = SimState(branch="epic/E002")
    shell = SimulatedShell(tmp_path, state, python_executable=r"D:\Projects\ai-content-generation\.venv\Scripts\python.exe")
    repo = Repository(tmp_path, process_runner_fn=shell)
    task_pipeline = TaskPipeline(tmp_path, config=_config(), process_runner_fn=shell, repository=repo)
    github = GitHubAdapter(tmp_path, process_runner_fn=shell)
    epic_pipeline = EpicPipeline(tmp_path, config=_config(), repository=repo, task_pipeline_factory=lambda root, config, runner: task_pipeline, github_adapter=github, process_runner_fn=shell)
    milestone_pipeline = MilestonePipeline(tmp_path, config=_config(), repository=repo, github_adapter=github, process_runner_fn=shell, epic_pipeline_factory=lambda *args, **kwargs: epic_pipeline)

    epic_result = epic_pipeline.run_epic(_make_run(tmp_path), human_authorized=True)
    assert epic_result.status == RunStatus.WAITING_FOR_MERGE
    epic_pr = epic_result.pull_request
    assert epic_pr is not None
    state.prs_by_number[epic_pr.number] = {
        "number": epic_pr.number,
        "url": epic_pr.url,
        "title": epic_pr.title,
        "baseRefName": epic_pr.base_branch,
        "headRefName": epic_pr.head_branch,
        "isDraft": True,
        "state": "MERGED",
        "mergedAt": "2026-07-23T10:00:00Z",
        "headRefOid": state.head_sha,
        "baseRefOid": "a" * 40,
    }
    saved = load_run_state("run-hardening", root=tmp_path)
    resumed = milestone_pipeline.run_milestone(
        AutopilotRun(
            run_id=saved.run_id,
            request=saved.request,
            status=RunStatus.WAITING_FOR_MERGE,
            created_at=saved.created_at,
            updated_at=saved.updated_at,
            milestone_id="M001",
            epic_id="E002",
            branch_name="epic/E002",
            pull_request=epic_pr,
        )
    )
    assert resumed.status == RunStatus.WAITING_FOR_MERGE
    assert resumed.branch_name == "bookkeeping/M001/E002"

    closure_pr = resumed.pull_request
    assert closure_pr is not None
    state.prs_by_number[closure_pr.number] = {
        "number": closure_pr.number,
        "url": closure_pr.url,
        "title": closure_pr.title,
        "baseRefName": closure_pr.base_branch,
        "headRefName": closure_pr.head_branch,
        "isDraft": True,
        "state": "MERGED",
        "mergedAt": "2026-07-23T11:00:00Z",
        "headRefOid": state.head_sha,
        "baseRefOid": "a" * 40,
    }
    tasks_path = tmp_path / "specs" / "001-ai-content-studio" / "tasks.md"
    tasks_text = tasks_path.read_text(encoding="utf-8").replace("- [ ] T001", "- [X] T001", 1)
    tasks_path.write_text(tasks_text, encoding="utf-8")

    milestone_waiting = milestone_pipeline.run_milestone(resumed.run)
    assert milestone_waiting.status == RunStatus.WAITING_FOR_MERGE
    assert milestone_waiting.branch_name == "bookkeeping/M001/close"

    milestone_pr = milestone_waiting.pull_request
    assert milestone_pr is not None
    state.prs_by_number[milestone_pr.number] = {
        "number": milestone_pr.number,
        "url": milestone_pr.url,
        "title": milestone_pr.title,
        "baseRefName": milestone_pr.base_branch,
        "headRefName": milestone_pr.head_branch,
        "isDraft": True,
        "state": "MERGED",
        "mergedAt": "2026-07-23T12:00:00Z",
        "headRefOid": state.head_sha,
        "baseRefOid": "a" * 40,
    }

    final = milestone_pipeline.run_milestone(milestone_waiting.run)
    assert final.status == RunStatus.COMPLETED
    assert load_run_state("run-hardening", root=tmp_path).status == RunStatus.COMPLETED


def test_epic_pipeline_reuses_existing_pr(tmp_path):
    _write_repo(tmp_path, epic2_status="planned")
    state = SimState(branch="epic/E002")
    existing = PullRequestInfo(
        number=42,
        url="https://example.invalid/pr/42",
        title="E002: Epic E002",
        base_branch="master",
        head_branch="epic/E002",
        draft=True,
        merged=False,
    )
    state.prs_by_branch[("master", "epic/E002")] = existing
    state.prs_by_number[42] = {
        "number": 42,
        "url": existing.url,
        "title": existing.title,
        "baseRefName": "master",
        "headRefName": "epic/E002",
        "isDraft": True,
        "state": "OPEN",
        "mergedAt": None,
        "headRefOid": state.head_sha,
        "baseRefOid": state.base_sha,
    }
    shell = SimulatedShell(tmp_path, state, python_executable=r"D:\Projects\ai-content-generation\.venv\Scripts\python.exe")
    repo = Repository(tmp_path, process_runner_fn=shell)
    task_pipeline = TaskPipeline(tmp_path, config=_config(), process_runner_fn=shell, repository=repo)
    github = GitHubAdapter(tmp_path, process_runner_fn=shell)
    pipeline = EpicPipeline(tmp_path, config=_config(), repository=repo, task_pipeline_factory=lambda root, config, runner: task_pipeline, github_adapter=github, process_runner_fn=shell)

    result = pipeline.run_epic(_make_run(tmp_path), human_authorized=True)

    assert result.status == RunStatus.WAITING_FOR_MERGE
    assert result.pull_request == existing
    assert not any(command[:3] == ("gh", "pr", "create") for command in shell.calls)


def test_milestone_pipeline_fails_when_waiting_pr_is_closed_without_merge(tmp_path):
    _write_repo(tmp_path, epic2_status="completed", epic1_status="completed")
    state = SimState(branch="epic/E002")
    shell = SimulatedShell(tmp_path, state, python_executable=r"D:\Projects\ai-content-generation\.venv\Scripts\python.exe")
    repo = Repository(tmp_path, process_runner_fn=shell)
    github = GitHubAdapter(tmp_path, process_runner_fn=shell)
    closed_pr = PullRequestInfo(
        number=99,
        url="https://example.invalid/pr/99",
        title="E002: close",
        base_branch="master",
        head_branch="epic/E002",
        draft=True,
        merged=False,
    )
    state.prs_by_number[99] = {
        "number": 99,
        "url": closed_pr.url,
        "title": closed_pr.title,
        "baseRefName": "master",
        "headRefName": "epic/E002",
        "isDraft": True,
        "state": "CLOSED",
        "mergedAt": None,
        "headRefOid": state.head_sha,
        "baseRefOid": state.base_sha,
    }
    pipeline = MilestonePipeline(tmp_path, config=_config(), repository=repo, github_adapter=github, process_runner_fn=shell)
    run = _make_run(
        tmp_path,
        scope_type=ScopeType.MILESTONE,
        scope_id="M001",
        status=RunStatus.WAITING_FOR_MERGE,
        epic_id="E002",
        branch_name="epic/E002",
        pull_request=closed_pr,
    )

    result = pipeline.run_milestone(run)

    assert result.status == RunStatus.FAILED
    assert "closed" in (result.reason or "").lower()
    assert load_run_state("run-hardening", root=tmp_path).status == RunStatus.FAILED


@pytest.mark.parametrize(
    "scenario, expected_status, expected_reason",
    [
        ({"invalid_codex_json": True}, RunStatus.FAILED, "AUTOPILOT_RESULT_JSON"),
        ({"scope_drift_after_codex": True, "dirty_paths": ("backend/other.py",)}, RunStatus.FAILED, "unexpected paths"),
        ({"diff_checks": ["FAIL", "PASS"]}, RunStatus.COMPLETED, None),
        ({"commit_fail": True}, RunStatus.FAILED, "git commit failed"),
        ({"codex_cli_ok": False}, RunStatus.FAILED, "codex CLI is missing"),
        ({"agent_python_ok": False}, RunStatus.FAILED, "agent.python"),
        ({"clean": False, "dirty_paths": ("backend/dirty.py",)}, RunStatus.FAILED, "working tree must be clean"),
        ({"timeout_during_codex": True}, RunStatus.FAILED, "AUTOPILOT_RESULT_JSON block not found"),
        ({"cancel_during_codex": True}, RunStatus.CANCELLED, "cancelled"),
    ],
)
def test_task_pipeline_hardening_matrix(tmp_path, scenario, expected_status, expected_reason):
    implementation_newline = scenario.get("diff_checks") != ["FAIL", "PASS"]
    _write_repo(tmp_path, implementation_newline=implementation_newline, epic2_status="active")
    state = SimState(branch="epic/E002", **scenario)
    shell = SimulatedShell(tmp_path, state, python_executable=r"D:\Projects\ai-content-generation\.venv\Scripts\python.exe")
    repo = Repository(tmp_path, process_runner_fn=shell)
    pipeline = TaskPipeline(tmp_path, config=_config(), process_runner_fn=shell, repository=repo)
    cancel_event = None
    if scenario.get("cancel_during_codex"):
        import threading

        cancel_event = threading.Event()

    result = pipeline.run_task(_make_run(tmp_path), task_id="T007", cancel_event=cancel_event)

    assert result.status == expected_status
    if expected_reason is not None:
        assert expected_reason.lower() in (result.reason or "").lower()
    assert load_run_state("run-hardening", root=tmp_path).status == expected_status


def test_task_pipeline_repairs_blank_eof_then_commits(tmp_path):
    _write_repo(tmp_path, implementation_newline=False, epic2_status="active")
    state = SimState(branch="epic/E002", diff_checks=["FAIL", "PASS"])
    shell = SimulatedShell(tmp_path, state, python_executable=r"D:\Projects\ai-content-generation\.venv\Scripts\python.exe")
    repo = Repository(tmp_path, process_runner_fn=shell)
    pipeline = TaskPipeline(tmp_path, config=_config(), process_runner_fn=shell, repository=repo)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T007")

    assert result.status == RunStatus.COMPLETED
    assert (tmp_path / "specs" / "001-ai-content-studio" / "main.py").read_text(encoding="utf-8").endswith("\n")


def test_task_pipeline_fails_on_cached_diff_check(tmp_path):
    _write_repo(tmp_path, epic2_status="active")
    state = SimState(branch="epic/E002", diff_checks=["PASS", "FAIL"])
    shell = SimulatedShell(tmp_path, state, python_executable=r"D:\Projects\ai-content-generation\.venv\Scripts\python.exe")
    repo = Repository(tmp_path, process_runner_fn=shell)
    pipeline = TaskPipeline(tmp_path, config=_config(), process_runner_fn=shell, repository=repo)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T007")

    assert result.status == RunStatus.FAILED
    assert "cached" in (result.reason or "").lower()


def test_epic_pipeline_fails_when_master_pull_is_not_ff_only(tmp_path):
    _write_repo(tmp_path, epic2_status="planned")
    state = SimState(branch="epic/E002", pull_fail=True)
    shell = SimulatedShell(tmp_path, state, python_executable=r"D:\Projects\ai-content-generation\.venv\Scripts\python.exe")
    repo = Repository(tmp_path, process_runner_fn=shell)
    task_pipeline = TaskPipeline(tmp_path, config=_config(), process_runner_fn=shell, repository=repo)
    github = GitHubAdapter(tmp_path, process_runner_fn=shell)
    pipeline = EpicPipeline(tmp_path, config=_config(), repository=repo, task_pipeline_factory=lambda root, config, runner: task_pipeline, github_adapter=github, process_runner_fn=shell)

    result = pipeline.run_epic(_make_run(tmp_path), human_authorized=True)

    assert result.status == RunStatus.FAILED
    assert "pull" in (result.reason or "").lower()


def test_epic_pipeline_fails_when_push_fails(tmp_path):
    _write_repo(tmp_path, epic2_status="planned")
    state = SimState(branch="epic/E002", push_fail=True)
    shell = SimulatedShell(tmp_path, state, python_executable=r"D:\Projects\ai-content-generation\.venv\Scripts\python.exe")
    repo = Repository(tmp_path, process_runner_fn=shell)
    task_pipeline = TaskPipeline(tmp_path, config=_config(), process_runner_fn=shell, repository=repo)
    github = GitHubAdapter(tmp_path, process_runner_fn=shell)
    pipeline = EpicPipeline(tmp_path, config=_config(), repository=repo, task_pipeline_factory=lambda root, config, runner: task_pipeline, github_adapter=github, process_runner_fn=shell)

    result = pipeline.run_epic(_make_run(tmp_path), human_authorized=True)

    assert result.status == RunStatus.FAILED
    assert "push failed" in (result.reason or "").lower()


def test_epic_pipeline_handles_gh_auth_failure_and_missing_cli(tmp_path):
    _write_repo(tmp_path, epic2_status="planned")
    github_cases = [
        ({"gh_auth_ok": False}, "403"),
        ({"gh_ok": False}, "gh cli is missing"),
    ]
    for index, (scenario, expected_reason) in enumerate(github_cases):
        case_root = tmp_path / f"case-{index}"
        _write_repo(case_root, epic2_status="planned")
        state = SimState(branch="epic/E002", **scenario)
        shell = SimulatedShell(case_root, state, python_executable=r"D:\Projects\ai-content-generation\.venv\Scripts\python.exe")
        repo = Repository(case_root, process_runner_fn=shell)
        task_pipeline = TaskPipeline(case_root, config=_config(), process_runner_fn=shell, repository=repo)
        github = GitHubAdapter(case_root, process_runner_fn=shell)
        pipeline = EpicPipeline(case_root, config=_config(), repository=repo, task_pipeline_factory=lambda root, config, runner: task_pipeline, github_adapter=github, process_runner_fn=shell)

        result = pipeline.run_epic(_make_run(case_root), human_authorized=True)

        assert result.status == RunStatus.FAILED
        assert expected_reason in (result.reason or "").lower()


def test_task_pipeline_fails_on_codex_missing(tmp_path):
    _write_repo(tmp_path, epic2_status="active")
    state = SimState(branch="epic/E002", codex_cli_ok=False)
    shell = SimulatedShell(tmp_path, state, python_executable=r"D:\Projects\ai-content-generation\.venv\Scripts\python.exe")
    repo = Repository(tmp_path, process_runner_fn=shell)
    pipeline = TaskPipeline(tmp_path, config=_config(), process_runner_fn=shell, repository=repo)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T007")

    assert result.status == RunStatus.FAILED
    assert "codex CLI is missing".lower() in (result.reason or "").lower()


def test_main_validates_python_and_tkinter():
    ok = validate_startup_environment(version_info=(3, 11, 0))
    assert ok.python_ok is True
    assert ok.tkinter_ok is True

    broken = validate_startup_environment(version_info=(3, 10, 0), tkinter_importer=lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name)))
    assert broken.python_ok is False
    assert broken.tkinter_ok is False
    assert any("Python 3.11" in issue for issue in broken.issues)
    assert any("tkinter" in issue for issue in broken.issues)


def test_ui_module_requires_tkinter(monkeypatch):
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "tkinter" or name.startswith("tkinter."):
            raise ModuleNotFoundError(name)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.delitem(sys.modules, "app.tooling.local_autopilot.ui", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.tooling.local_autopilot.ui")

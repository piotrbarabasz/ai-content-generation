from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.tooling import agent_task_preflight as preflight
from app.tooling.local_autopilot.config import AutopilotConfig
from app.tooling.local_autopilot.models import AutopilotRequest, AutopilotRun, RunMode, RunStatus, ScopeType
from app.tooling.local_autopilot.process_runner import ProcessResult
from app.tooling.local_autopilot.state_store import load_run_state
from app.tooling.local_autopilot.task_pipeline import TaskPipeline, TaskPipelineError, _parse_preflight_json_document


@dataclass
class ScenarioRunner:
    root: Path
    python_executable: str = r"D:\Python\python.exe"
    initial_dirty_paths: tuple[str, ...] = ()
    scope_drift_paths: tuple[str, ...] = ()
    validation_results: tuple[str, ...] = ("PASS",)
    diff_results: tuple[str, ...] = ("PASS",)
    commit_result: str = "PASS"
    preflight_status: str = "PASS"
    codex_result_status: str = "PASS"
    codex_json: dict[str, object] | None = None
    codex_api_error_lines: tuple[str, ...] = ()
    dirty_after_codex: bool = True
    sandbox_banner: str = "sandbox: workspace-write"

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._validation_index = 0
        self._diff_index = 0
        self._codex_attempts = 0
        self._committed = False
        self._head_sha = "a" * 40

    def __call__(self, argv, **kwargs):
        command = tuple(str(part) for part in argv)
        self.calls.append(command)

        if command == ("git", "config", "--local", "--get", "agent.python"):
            return self._result(command, stdout=(self.python_executable,))

        if command[:2] == ("codex", "--help"):
            return self._result(command, stdout=("Codex CLI",))

        if command[:3] == ("codex", "exec", "--help"):
            return self._result(command, stdout=("Run Codex non-interactively",))

        if command[:3] == ("git", "status", "--porcelain=v1"):
            return self._status_result(command)

        if command[:3] == ("git", "rev-parse", "HEAD"):
            return self._result(command, stdout=(self._head_sha,))

        if command[:3] == ("git", "add", "--"):
            return self._result(command)

        if command[:3] == ("git", "--no-pager", "diff") and "--cached" in command:
            return self._result(command)

        if command[:3] == ("git", "--no-pager", "diff"):
            if self._diff_index < len(self.diff_results):
                status = self.diff_results[self._diff_index]
                self._diff_index += 1
            else:
                status = "PASS"
            return self._result(command, status=status, exit_code=0 if status == "PASS" else 1)

        if command[:2] == ("git", "commit"):
            status = self.commit_result
            if status == "PASS":
                self._committed = True
                self._head_sha = "b" * 40
            return self._result(command, status=status, exit_code=0 if status == "PASS" else 1)

        if command[:3] == ("python", "-m", "backend.app.tooling.agent_task_preflight"):
            raise AssertionError("preflight should be invoked with pinned python, not literal python")

        if command[:3] == (self.python_executable, "-m", "backend.app.tooling.agent_task_preflight"):
            selector = "T045"
            if "--selector" in command:
                selector = command[command.index("--selector") + 1]
            payload = {
                "status": self.preflight_status,
                "exit_code": 0 if self.preflight_status == "PASS" else 1,
                "task": selector,
                "epic": "E001",
                "branch": "feat/local-autopilot-ui",
                "baseline_path": str(self.root / ".specify" / "runtime" / "task-runs" / selector / "baseline.json"),
                "feature_dir": str(self.root / "specs" / "001-ai-content-studio"),
                "tasks_path": str(self.root / "specs" / "001-ai-content-studio" / "tasks.md"),
                "duration_ms": 1,
                "checks": [
                    {
                        "name": "selected_task_metadata",
                        "status": "PASS",
                        "details": {
                            "task": selector,
                            "nested": [
                                {"name": "first", "details": {"value": 1}},
                                {"name": "second", "details": {"value": 2}},
                            ],
                        },
                    },
                    {
                        "name": "baseline_capture",
                        "status": "PASS",
                        "details": {
                            "baseline_path": str(self.root / ".specify" / "runtime" / "task-runs" / selector / "baseline.json"),
                            "nested": [
                                {"name": "outer", "details": {"value": 3}},
                                {"name": "inner", "details": {"value": 4}},
                            ],
                        },
                    },
                ],
            }
            return self._result(
                command,
                stdout=tuple(json.dumps(payload, indent=2).splitlines()),
                status=self.preflight_status,
                exit_code=0 if self.preflight_status == "PASS" else 1,
            )

        if command[:2] == (self.python_executable, "-m") and "pytest" in command:
            if self._validation_index < len(self.validation_results):
                status = self.validation_results[self._validation_index]
                self._validation_index += 1
            else:
                status = "PASS"
            return self._result(command, status=status, exit_code=0 if status == "PASS" else 1)

        if Path(command[0]).name.lower().startswith("codex") and "exec" in command and "--help" not in command:
            self._codex_attempts += 1
            prompt = kwargs.get("stdin_text", "")
            match = re.search(r"Selected task:\s*(T\d{3}[A-Z]?)", prompt)
            task_id = match.group(1) if match else None
            if self.codex_api_error_lines:
                return self._result(
                    command,
                    status="FAIL",
                    exit_code=1,
                    stdout=(),
                    stderr=self.codex_api_error_lines,
                )
            payload = self.codex_json or {
                "task_id": task_id,
                "final_status": "COMPLETED",
                "review_verdict": "PASS",
                "reason": None,
                "files_changed": ["backend/app/tooling/local_autopilot/task_pipeline.py"],
                "validation": [],
                "tasks_md_change": f"- [X] {task_id} Implement deterministic pipeline for single task",
                "repair_cycles_used": 0,
                "safe_to_commit": True,
                "next_task_started": False,
                "retryable": False,
            }
            output_path = None
            if "--output-last-message" in command:
                output_path = Path(command[command.index("--output-last-message") + 1])
            if (
                self.codex_result_status == "PASS"
                and isinstance(payload, dict)
                and str(payload.get("final_status") or "").upper() == "COMPLETED"
                and bool(payload.get("safe_to_commit", False))
                and task_id is not None
            ):
                self._mark_complete(task_id)
            if output_path is not None:
                output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            stdout = (
                self.sandbox_banner,
                "Codex working",
                json.dumps(payload),
            )
            return self._result(command, stdout=stdout, status=self.codex_result_status, exit_code=0 if self.codex_result_status == "PASS" else 1)

        raise AssertionError(f"unexpected command: {command}")

    def _status_result(self, command: tuple[str, ...]) -> ProcessResult:
        if self._committed:
            stdout = ("## feat/local-autopilot-ui",)
            return self._result(command, stdout=stdout)
        if self._codex_attempts and self.dirty_after_codex:
            dirty_paths = self.scope_drift_paths or ("specs/001-ai-content-studio/tasks.md",)
            stdout = ("## feat/local-autopilot-ui", *[f" M {path}" for path in dirty_paths])
            return self._result(command, stdout=stdout)
        if self.initial_dirty_paths:
            stdout = ("## feat/local-autopilot-ui", *[f" M {path}" for path in self.initial_dirty_paths])
            return self._result(command, stdout=stdout)
        return self._result(command, stdout=("## feat/local-autopilot-ui",))

    def _result(
        self,
        command: tuple[str, ...],
        *,
        status: str = "PASS",
        exit_code: int | None = 0,
        stdout: tuple[str, ...] = (),
        stderr: tuple[str, ...] = (),
    ) -> ProcessResult:
        return ProcessResult(
            command=command,
            status=status,
            exit_code=exit_code,
            duration_ms=5,
            timed_out=False,
            cancelled=False,
            stdout_lines=stdout,
            stderr_lines=stderr,
            output_truncated=False,
            process_tree_killed=False,
            pid=4321,
        )

    def _mark_complete(self, task_id: str) -> None:
        tasks_path = self.root / "specs" / "001-ai-content-studio" / "tasks.md"
        text = tasks_path.read_text(encoding="utf-8")
        text = text.replace(f"- [ ] {task_id}", f"- [X] {task_id}", 1)
        tasks_path.write_text(text, encoding="utf-8")


def _write(path: Path, text: str, *, newline: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if newline and not text.endswith("\n"):
        text = f"{text}\n"
    path.write_text(text, encoding="utf-8")


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _setup_repo(
    tmp_path: Path,
    *,
    checkbox: str = " ",
    validation_commands: str = "python -m pytest backend/tests/unit/tooling/local_autopilot/test_task_pipeline.py",
    implementation_newline: bool = True,
    checklists: dict[str, str] | None = None,
) -> tuple[Path, Path, Path]:
    workstreams = tmp_path / ".specify" / "workstreams"
    runtime = tmp_path / ".specify" / "runtime" / "task-runs" / "T045"
    feature_dir = tmp_path / "specs" / "001-ai-content-studio"
    implementation_file = tmp_path / "backend" / "app" / "tooling" / "local_autopilot" / "task_pipeline.py"
    test_file = tmp_path / "backend" / "tests" / "unit" / "tooling" / "local_autopilot" / "test_task_pipeline.py"
    workstreams.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    feature_dir.mkdir(parents=True, exist_ok=True)
    checklist_dir = feature_dir / "checklists"
    checklist_dir.mkdir(parents=True, exist_ok=True)

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
                "branch: feat/local-autopilot-ui",
                "status: active",
                "risk: medium",
                "depends_on: []",
                "tasks:",
                "  - T045",
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
                f"- [{checkbox}] T045 Implement deterministic pipeline for single task",
                "Milestone: M001",
                "Epic: E001",
                "Risk: medium",
                "Implementation files: backend/app/tooling/local_autopilot/task_pipeline.py",
                "Test files: backend/tests/unit/tooling/local_autopilot/test_task_pipeline.py",
                f"Validation commands: {validation_commands}",
                "Acceptance criteria: done",
                "Dependencies: None",
                "",
            ]
        ),
    )
    _write(implementation_file, "print('placeholder')\n", newline=implementation_newline)
    _write(test_file, "print('placeholder')\n")
    _write(
        runtime / "baseline.json",
        json.dumps(
            {
                "schema_version": 1,
                "task": "T045",
                "epic": "E001",
                "branch": "feat/local-autopilot-ui",
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
    for filename, content in (checklists or {}).items():
        _write(feature_dir / "checklists" / filename, content)
    (tmp_path / ".specify" / "runtime" / "active-epic").write_text("E001\n", encoding="utf-8")
    return implementation_file, test_file, feature_dir / "tasks.md"


def _make_run(tmp_path: Path) -> AutopilotRun:
    request = AutopilotRequest(
        scope_type=ScopeType.EPIC,
        scope_id="E001",
        run_mode=RunMode.FULL,
        repo_path=str(tmp_path),
    )
    return AutopilotRun(
        run_id="run-045",
        request=request,
        status=RunStatus.PREFLIGHT,
        created_at="2026-07-23T12:00:00Z",
        updated_at="2026-07-23T12:00:00Z",
        epic_id="E001",
        branch_name="feat/local-autopilot-ui",
    )


def _config(max_repair_cycles: int = 2) -> AutopilotConfig:
    return AutopilotConfig(
        auto_commit=True,
        auto_push=True,
        create_draft_pr=True,
        auto_merge=False,
        deploy=False,
        max_repair_cycles=max_repair_cycles,
        max_tasks_per_run=20,
        command_timeout_seconds=180,
        codex_timeout_seconds=3600,
        closure_mode="pull_request",
    )


def _build_pipeline(tmp_path: Path, runner: ScenarioRunner, *, max_repair_cycles: int = 2) -> TaskPipeline:
    return TaskPipeline(tmp_path, config=_config(max_repair_cycles), process_runner_fn=runner)


def test_run_task_happy_path_commits_and_saves_state(tmp_path):
    implementation_file, test_file, tasks_file = _setup_repo(tmp_path)
    runner = ScenarioRunner(tmp_path)
    pipeline = _build_pipeline(tmp_path, runner)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.COMPLETED
    assert result.task_result.status == RunStatus.COMPLETED
    assert result.task_result.commit_sha == "b" * 40
    assert result.attempts == 1
    assert result.run.status == RunStatus.COMPLETED
    assert tasks_file.read_text(encoding="utf-8").splitlines()[0].startswith("- [X] T045")
    assert load_run_state("run-045", root=tmp_path) == result.run
    assert any(command[:3] == ("git", "add", "--") for command in runner.calls)
    assert any(command[:2] == ("git", "commit") for command in runner.calls)
    assert any(Path(command[0]).name.lower().startswith("codex") and "exec" in command and "--help" not in command for command in runner.calls)


def test_run_task_fails_on_scope_drift(tmp_path):
    _setup_repo(tmp_path)
    runner = ScenarioRunner(
        tmp_path,
        scope_drift_paths=("backend/other.py",),
    )
    pipeline = _build_pipeline(tmp_path, runner)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.FAILED
    assert "unexpected paths outside allowlist" in (result.reason or "")
    assert not any(command[:2] == ("git", "commit") for command in runner.calls)
    assert load_run_state("run-045", root=tmp_path).status == RunStatus.FAILED


def test_run_task_rejects_checked_checkbox(tmp_path):
    _setup_repo(tmp_path, checkbox="X")
    runner = ScenarioRunner(tmp_path)
    pipeline = _build_pipeline(tmp_path, runner)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.FAILED
    assert "must be unchecked" in (result.reason or "")
    assert not any(Path(command[0]).name.lower().startswith("codex") and "exec" in command and "--help" not in command for command in runner.calls)


def test_run_task_blocks_on_incomplete_required_checklist(tmp_path):
    _setup_repo(
        tmp_path,
        checklists={
            "required-checklist.md": "\n".join(
                [
                    "# Required checklist",
                    "- [ ] Item one",
                    "",
                ]
            ),
        },
    )
    runner = ScenarioRunner(tmp_path)
    pipeline = _build_pipeline(tmp_path, runner)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.BLOCKED
    assert result.attempts == 0
    assert "required checklist items are incomplete" in (result.reason or "")
    assert "required-checklist.md" in (result.reason or "")
    assert not any(Path(command[0]).name.lower().startswith("codex") and "exec" in command and "--help" not in command for command in runner.calls)
    assert load_run_state("run-045", root=tmp_path).status == RunStatus.BLOCKED


def test_run_task_ignores_optional_checklist(tmp_path):
    _setup_repo(
        tmp_path,
        checklists={
            "optional-checklist.md": "\n".join(
                [
                    "optional: true",
                    "- [ ] Item one",
                    "",
                ]
            ),
        },
    )
    runner = ScenarioRunner(tmp_path)
    pipeline = _build_pipeline(tmp_path, runner)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.COMPLETED
    assert any(Path(command[0]).name.lower().startswith("codex") and "exec" in command and "--help" not in command for command in runner.calls)


def test_run_task_fails_on_validation_failure_without_retries(tmp_path):
    _setup_repo(tmp_path, validation_commands="python -m pytest backend/tests/unit/tooling/local_autopilot/test_task_pipeline.py")
    runner = ScenarioRunner(
        tmp_path,
        validation_results=("FAIL", "PASS"),
    )
    pipeline = _build_pipeline(tmp_path, runner, max_repair_cycles=1)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.FAILED
    assert result.attempts == 1
    assert "validation failed" in (result.reason or "")
    assert sum(1 for command in runner.calls if Path(command[0]).name.lower().startswith("codex") and "exec" in command and "--help" not in command) == 1
    assert sum(1 for command in runner.calls if command[:2] == (runner.python_executable, "-m") and "pytest" in command) == 1


def test_run_task_preserves_quoted_validation_arguments_as_single_argv(tmp_path):
    _setup_repo(
        tmp_path,
        validation_commands='python -m pytest "backend/tests/unit/tooling/local_autopilot/test task pipeline.py"',
    )
    runner = ScenarioRunner(tmp_path)
    pipeline = _build_pipeline(tmp_path, runner)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.COMPLETED
    assert any(
        command[:3] == (runner.python_executable, "-m", "pytest")
        and len(command) == 4
        and command[3] == "backend/tests/unit/tooling/local_autopilot/test task pipeline.py"
        for command in runner.calls
    )


def test_run_task_repairs_whitespace_once(tmp_path):
    implementation_file, _, _ = _setup_repo(tmp_path, implementation_newline=False)
    runner = ScenarioRunner(
        tmp_path,
        diff_results=("FAIL", "PASS"),
    )
    pipeline = _build_pipeline(tmp_path, runner)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.COMPLETED
    assert implementation_file.read_bytes().endswith(b"\n")
    assert any(command == ("normalize_allowlist_eof",) for command in (tuple(item.command) for item in result.command_results))


def test_run_task_fails_when_commit_fails(tmp_path):
    _setup_repo(tmp_path)
    runner = ScenarioRunner(
        tmp_path,
        commit_result="FAIL",
    )
    pipeline = _build_pipeline(tmp_path, runner)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.FAILED
    assert "git commit failed" in (result.reason or "")
    assert any(command[:2] == ("git", "commit") for command in runner.calls)


def test_run_task_fails_fast_on_dirty_tree(tmp_path):
    _setup_repo(tmp_path)
    runner = ScenarioRunner(
        tmp_path,
        initial_dirty_paths=("backend/other.py",),
    )
    pipeline = _build_pipeline(tmp_path, runner)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.FAILED
    assert "working tree must be clean" in (result.reason or "")
    assert not any(Path(command[0]).name.lower().startswith("codex") and "exec" in command and "--help" not in command for command in runner.calls)


def test_run_task_cancels_before_work(tmp_path):
    _setup_repo(tmp_path)
    runner = ScenarioRunner(tmp_path)
    pipeline = _build_pipeline(tmp_path, runner)
    cancel_event = threading.Event()
    cancel_event.set()

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045", cancel_event=cancel_event)

    assert result.status == RunStatus.CANCELLED
    assert result.reason == "cancelled"
    assert runner.calls == []
    assert load_run_state("run-045", root=tmp_path).status == RunStatus.CANCELLED


def test_run_task_honors_repair_limit(tmp_path):
    _setup_repo(tmp_path)
    runner = ScenarioRunner(
        tmp_path,
        validation_results=("FAIL", "FAIL"),
    )
    pipeline = _build_pipeline(tmp_path, runner, max_repair_cycles=1)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.FAILED
    assert result.attempts == 1
    assert "validation failed" in (result.reason or "")
    assert sum(1 for command in runner.calls if Path(command[0]).name.lower().startswith("codex") and "exec" in command and "--help" not in command) == 1


def test_run_task_does_not_retry_nonretryable_codex_failure(tmp_path):
    _setup_repo(tmp_path)
    runner = ScenarioRunner(
        tmp_path,
        codex_json={
            "task_id": "T045",
            "final_status": "FAILED",
            "review_verdict": "FAIL",
            "reason": "codex reported failure",
            "files_changed": [],
            "validation": [],
            "tasks_md_change": "",
            "repair_cycles_used": 0,
            "safe_to_commit": False,
            "next_task_started": False,
            "retryable": False,
        },
    )
    pipeline = _build_pipeline(tmp_path, runner, max_repair_cycles=1)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.FAILED
    assert result.attempts == 1
    assert "codex exited with FAIL" in (result.reason or "")
    assert sum(1 for command in runner.calls if Path(command[0]).name.lower().startswith("codex") and "exec" in command and "--help" not in command) == 1
    assert not any(command[:2] == (runner.python_executable, "-m") and "pytest" in command for command in runner.calls)


def test_run_task_retries_retryable_codex_failure(tmp_path):
    _setup_repo(tmp_path)
    runner = ScenarioRunner(
        tmp_path,
        codex_json={
            "task_id": "T045",
            "final_status": "FAILED",
            "review_verdict": "FAIL",
            "reason": "codex reported failure",
            "files_changed": [],
            "validation": [],
            "tasks_md_change": "",
            "repair_cycles_used": 0,
            "safe_to_commit": False,
            "next_task_started": False,
            "retryable": True,
        },
    )
    pipeline = _build_pipeline(tmp_path, runner, max_repair_cycles=1)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.FAILED
    assert result.attempts == 1
    assert "codex exited with FAIL" in (result.reason or "")
    assert sum(1 for command in runner.calls if Path(command[0]).name.lower().startswith("codex") and "exec" in command and "--help" not in command) == 1
    assert not any(command[:2] == (runner.python_executable, "-m") and "pytest" in command for command in runner.calls)


def test_run_task_fails_fast_on_read_only_sandbox(tmp_path):
    _setup_repo(tmp_path)
    runner = ScenarioRunner(
        tmp_path,
        sandbox_banner="sandbox: read-only",
    )
    pipeline = _build_pipeline(tmp_path, runner)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.FAILED
    assert "workspace-write was requested" in (result.reason or "")
    assert result.attempts == 1
    assert not any(command[:2] == (runner.python_executable, "-m") and "pytest" in command for command in runner.calls)


def test_run_task_stops_before_validation_on_codex_api_error(tmp_path):
    implementation_file, _, tasks_file = _setup_repo(tmp_path)
    original_tasks_text = tasks_file.read_text(encoding="utf-8")
    runner = ScenarioRunner(
        tmp_path,
        codex_api_error_lines=(
            "invalid_request_error",
            "code: invalid_json_schema",
            "message: Invalid schema for response_format 'codex_output_schema': additionalProperties is required to be supplied and to be false.",
        ),
    )
    pipeline = _build_pipeline(tmp_path, runner)

    result = pipeline.run_task(_make_run(tmp_path), task_id="T045")

    assert result.status == RunStatus.FAILED
    assert "invalid_json_schema" in (result.reason or "")
    assert not any(command[:2] == (runner.python_executable, "-m") and "pytest" in command for command in runner.calls)
    assert not any(command[:2] == ("git", "commit") for command in runner.calls)
    assert tasks_file.read_text(encoding="utf-8") == original_tasks_text
    assert implementation_file.read_text(encoding="utf-8").endswith("\n")


def test_parse_preflight_json_document_uses_root_payload(tmp_path):
    baseline_path = tmp_path / ".specify" / "runtime" / "task-runs" / "T045" / "baseline.json"
    payload = preflight._payload(
        preflight.PreflightResult(
            status="PASS",
            exit_code=0,
            selector="T045",
            task_id="T045",
            epic_id="E001",
            branch="feat/local-autopilot-ui",
            head_sha="a" * 40,
            duration_ms=12,
            checks=(
                preflight.CheckResult(
                    name="selected_task_metadata",
                    status="PASS",
                    details={
                        "task": "T045",
                        "nested": [
                            {"name": "first", "details": {"value": 1}},
                            {"name": "second", "details": {"value": 2}},
                        ],
                    },
                ),
                preflight.CheckResult(
                    name="baseline_capture",
                    status="PASS",
                    details={
                        "baseline_path": str(baseline_path),
                        "nested": [
                            {"name": "outer", "details": {"value": 3}},
                            {"name": "inner", "details": {"value": 4}},
                        ],
                    },
                ),
            ),
            baseline_path=str(baseline_path),
        )
    )
    text = json.dumps(payload, indent=2)

    parsed = _parse_preflight_json_document(text)
    assert parsed["task"] == "T045"
    assert parsed["epic"] == "E001"
    assert parsed["baseline_path"] == str(baseline_path)
    assert parsed["checks"][-1]["details"]["nested"][-1]["name"] == "inner"
    assert "baseline_path" not in parsed["checks"][-1]

    with pytest.raises(TaskPipelineError, match="preflight output is not valid JSON"):
        _parse_preflight_json_document(f"{text}\ntrailing garbage")

    with pytest.raises(TaskPipelineError, match="preflight JSON must be an object"):
        _parse_preflight_json_document(json.dumps(["not", "an", "object"]))

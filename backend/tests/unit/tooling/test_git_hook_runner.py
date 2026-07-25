from __future__ import annotations

import itertools
import json
import re
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

from app.tooling import git_hook_runner as runner
from app.tooling.local_autopilot.task_state_machine import (
    TaskLifecycleState,
    TaskReceiptStage,
    TaskReceiptRecord,
    TaskStateRecord,
    save_task_receipt,
    save_task_state,
)


@dataclass
class FakeProcessResult:
    status: str
    exit_code: int | None = 0
    duration_ms: int = 12
    timed_out: bool = False
    stdout_lines: tuple[str, ...] = ()
    stderr_lines: tuple[str, ...] = ()
    output_truncated: bool = False
    process_tree_killed: bool = False
    pid: int | None = 1234


@dataclass
class FakeGitStatus:
    branch: str = "feature/E003"
    head_sha: str = "a" * 40
    tracked: tuple[str, ...] = ()
    staged: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    renamed: tuple[tuple[str, str], ...] = ()

    @property
    def clean(self) -> bool:
        return not (self.tracked or self.staged or self.untracked or self.deleted or self.renamed)


class FakeRepository:
    def __init__(self, status: FakeGitStatus, ancestors: set[tuple[str, str]] | None = None) -> None:
        self._status = status
        self._ancestors = ancestors or set()

    def status(self) -> FakeGitStatus:
        return self._status

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return (ancestor, descendant) in self._ancestors


def _clock(values: list[float]) -> callable:
    iterator = itertools.chain(values, itertools.repeat(values[-1]))
    return lambda: next(iterator)


def _patch_run_process(monkeypatch, responses: list[FakeProcessResult], calls: list[tuple[tuple[str, ...], dict[str, object]]]) -> None:
    iterator = iter(responses)

    def fake_run_process(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        return next(iterator)

    monkeypatch.setattr(runner.process_runner, "run_process", fake_run_process)


def _write_closed_task_fixture(
    tmp_path: Path,
) -> None:
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T009",
                "checkbox": "X",
                "state": TaskLifecycleState.CLOSED,
                "allowlist": (
                    "backend/app/storage/artifact_store.py",
                    "backend/app/storage/local_store.py",
                    "backend/app/storage/manifest.py",
                    "backend/tests/unit/test_t009.py",
                ),
            }
        ],
    )
    (tmp_path / ".specify" / "runtime" / "active-epic").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".specify" / "runtime" / "active-epic").write_text("E003\n", encoding="utf-8")


def _write_task_entries_fixture(tmp_path: Path, entries: list[dict[str, object]]) -> None:
    tasks_path = tmp_path / "specs" / "001-ai-content-studio" / "tasks.md"
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[str] = []
    for entry in entries:
        task_id = str(entry["task_id"])
        checkbox = str(entry.get("checkbox", " "))
        title = str(entry.get("title", "Implement artifact storage abstraction and local store"))
        allowlist = tuple(str(item) for item in entry.get("allowlist", ()))
        branch = str(entry.get("branch", "feature/E003"))
        head_sha = str(entry.get("head_sha", "a" * 40))
        feature_dir = str(entry.get("feature_dir", ""))
        epic = str(entry.get("epic", "E003"))
        milestone = str(entry.get("milestone", "M001"))
        state = entry.get("state", TaskLifecycleState.CLOSED)
        receipt_state = entry.get("receipt_state", state)
        run_id = str(entry.get("run_id", "run-001"))
        updated_at = str(entry.get("updated_at", "2026-07-25T00:00:00Z"))
        validation_commands = tuple(str(item) for item in entry.get("validation_commands", ("python -m pytest backend/tests/unit/tooling/test_git_hook_runner.py",)))
        rows.extend(
            [
                f"- [{checkbox}] {task_id} {title}",
                f"Milestone: {milestone}",
                f"Epic: {epic}",
                "Implementation files: backend/app/storage/artifact_store.py",
                "Test files: backend/tests/unit/tooling/test_git_hook_runner.py",
                "Validation commands: python -m pytest backend/tests/unit/tooling/test_git_hook_runner.py",
                "Acceptance criteria: done",
                "Dependencies: None",
                "",
            ]
        )
        save_task_state(
            TaskStateRecord(
                schema_version=1,
                run_id=run_id,
                task_id=task_id,
                state=state,
                updated_at=updated_at,
                branch=branch,
                head_sha=head_sha,
                tasks_path=tasks_path.as_posix(),
                feature_dir=feature_dir,
                allowlist=allowlist,
                validation_commands=validation_commands,
                task_line=1,
            ),
            root=tmp_path,
        )
        save_task_receipt(
            TaskReceiptRecord(
                schema_version=1,
                run_id=run_id,
                task_id=task_id,
                updated_at=updated_at,
                state=receipt_state if isinstance(receipt_state, TaskLifecycleState) else TaskLifecycleState(str(receipt_state)),
                summary=str(entry.get("summary", "closed")),
                files_touched=tuple(str(item) for item in entry.get("files_touched", ())),
                notes=tuple(str(item) for item in entry.get("notes", ())),
                validation=tuple(dict(item) for item in entry.get("validation", ()) if isinstance(item, dict)),
                commit_sha=str(entry.get("commit_sha", "")),
                stages=tuple(
                    TaskReceiptStage(
                        name=str(stage.get("name", "")),
                        status=str(stage.get("status", "")),
                        updated_at=str(stage.get("updated_at", updated_at)),
                        details=dict(stage.get("details", {})),
                    )
                    for stage in entry.get("stages", ())
                    if isinstance(stage, dict)
                ),
                review_verdict=str(entry.get("review_verdict", "")),
                safe_to_close=bool(entry.get("safe_to_close", False)),
                closure_checkbox_before=str(entry.get("closure_checkbox_before", "")),
                closure_checkbox_after=str(entry.get("closure_checkbox_after", "")),
                closure_task_line=int(entry.get("closure_task_line", 0)),
            ),
            root=tmp_path,
        )
    tasks_path.write_text("\n".join(rows), encoding="utf-8")


def _write_epic_manifest_fixture(
    tmp_path: Path,
    *,
    status: str = "completed",
    branch: str = "epic/E003-artifact-storage",
) -> None:
    workstreams_path = tmp_path / ".specify" / "workstreams"
    workstreams_path.mkdir(parents=True, exist_ok=True)
    (workstreams_path / "E003-artifact-storage.yml").write_text(
        "\n".join(
            [
                "id: E003",
                "title: Artifact Storage",
                "milestone: M001",
                "feature: specs/001-ai-content-studio",
                "base_branch: master",
                f"branch: {branch}",
                f"status: {status}",
                "risk: high",
                "depends_on:",
                "  - E001",
                "  - E002",
                "tasks:",
                "  - T009",
                "  - T010",
                "  - T021",
                "  - T022",
                "  - T023",
                "  - T024",
                "required_checks:",
                "  - python -m pytest",
                "  - git --no-pager diff --check",
                "pr_policy:",
                "  one_pr_per_epic: true",
                "  merge_requires_human: true",
                "  auto_merge: false",
                "commit_policy:",
                "  one_commit_per_task: true",
                "  commit_requires_human: true",
                "  auto_commit: false",
            ]
        ),
        encoding="utf-8",
    )


def _task_checkbox_diff_lines(
    task_id: str = "T009",
    title: str = "Implement artifact storage abstraction and local store",
) -> tuple[str, ...]:
    return (
        "diff --git a/specs/001-ai-content-studio/tasks.md b/specs/001-ai-content-studio/tasks.md",
        "@@ -1,1 +1,1 @@",
        f"-- [ ] {task_id} {title}",
        f"+- [X] {task_id} {title}",
    )


def _bookkeeping_diff_lines() -> tuple[str, ...]:
    return (
        "diff --git a/specs/001-ai-content-studio/tasks.md b/specs/001-ai-content-studio/tasks.md",
        "@@ -1,1 +1,1 @@",
        "-- bookkeeping note",
        "++ bookkeeping note",
    )


def _post_commit_git_runner_factory(
    *,
    task_id: str,
    title: str,
    parent_sha: str,
    head_sha: str,
    tasks_relpath: str = "specs/001-ai-content-studio/tasks.md",
    allowlist_paths: tuple[str, ...] = (),
) -> tuple[list[tuple[tuple[str, ...], dict[str, object]]], Callable[..., FakeProcessResult]]:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_run_process(argv, **kwargs):
        command = tuple(argv)
        calls.append((command, dict(kwargs)))
        if command == ("git", "show", "-s", "--format=%s", "HEAD"):
            return FakeProcessResult(status="PASS", stdout_lines=(f"feat({task_id}): {title}",))
        if command == ("git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"):
            return FakeProcessResult(status="PASS", stdout_lines=(*allowlist_paths, tasks_relpath))
        if command == ("git", "rev-parse", "HEAD^"):
            return FakeProcessResult(status="PASS", stdout_lines=(parent_sha,))
        if command == ("git", "diff", "--unified=0", parent_sha, "HEAD", "--", tasks_relpath):
            return FakeProcessResult(status="PASS", stdout_lines=_task_checkbox_diff_lines(task_id, title))
        raise AssertionError(command)

    return calls, fake_run_process


def _two_task_checkbox_diff_lines() -> tuple[str, ...]:
    return (
        "diff --git a/specs/001-ai-content-studio/tasks.md b/specs/001-ai-content-studio/tasks.md",
        "@@ -1,2 +1,2 @@",
        "-- [ ] T010 Implement artifact storage abstraction and local store",
        "+- [X] T010 Implement artifact storage abstraction and local store",
        "-- [ ] T022 Implement artifact storage abstraction and local store",
        "+- [X] T022 Implement artifact storage abstraction and local store",
    )


def _two_task_checkbox_diff_lines_for_post_commit(
    first_task_id: str = "T023",
    second_task_id: str = "T024",
    title: str = "Implement ProviderConfig validation before workflow execution",
) -> tuple[str, ...]:
    return (
        "diff --git a/specs/001-ai-content-studio/tasks.md b/specs/001-ai-content-studio/tasks.md",
        "@@ -1,2 +1,2 @@",
        f"-- [ ] {first_task_id} {title}",
        f"+- [X] {first_task_id} {title}",
        f"-- [ ] {second_task_id} {title}",
        f"+- [X] {second_task_id} {title}",
    )


def _load_task_state_record(task_id: str, root: Path) -> TaskStateRecord:
    record = runner.load_task_state(task_id, root=root)
    assert record is not None
    return record


def _load_task_receipt_record(task_id: str, root: Path) -> TaskReceiptRecord:
    receipt = runner.load_task_receipt(task_id, root=root)
    assert receipt is not None
    return receipt


def test_pre_commit_uses_process_runner_and_disables_heartbeat(monkeypatch, capsys, tmp_path):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "Repository", lambda _root: FakeRepository(FakeGitStatus()))
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        calls,
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([200.0, 200.01, 201.0, 201.01, 202.0, 202.01]))

    exit_code = runner.main(["pre-commit", "--json", "--no-heartbeat"])

    assert exit_code == 0
    assert "subprocess" not in runner.__dict__
    assert [call[0] for call in calls] == [
        (sys.executable, "-m", "backend.app.tooling.workstream_validation"),
        (sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
        ("git", "--no-pager", "diff", "--cached", "--check"),
    ]
    assert [call[1]["timeout_seconds"] for call in calls] == [20, 20, 20]
    assert all(call[1]["heartbeat_seconds"] == 0 for call in calls)
    assert all(call[1]["cwd"] == runner.ROOT for call in calls)

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["global_timeout"] is False
    assert len(payload["commands"]) == 3
    assert "output" not in payload["commands"][0]


def test_path_allowed_matches_exact_file_and_directory_prefix():
    assert runner._path_allowed("backend/app/storage/file.py", {"backend/app/storage/file.py"})
    assert runner._path_allowed("backend/app/storage/sub/file.py", {"backend/app/storage"})
    assert runner._path_allowed(r"backend\app\storage\sub\file.py", {r"backend\app\storage"})


def test_path_allowed_rejects_similar_prefix_without_separator():
    assert not runner._path_allowed("backend/app/storage_evil/file.py", {"backend/app/storage"})


def test_timestamp_is_utc_without_microseconds():
    value = runner._timestamp()

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert parsed.tzinfo == timezone.utc


def test_pre_commit_validation_allows_bookkeeping_tasks_md_and_allowlist(monkeypatch, tmp_path):
    _write_closed_task_fixture(
        tmp_path,
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    repo = FakeRepository(
        FakeGitStatus(
            staged=(
                "backend/app/storage/artifact_store.py",
                "backend/app/storage/local_store.py",
                "backend/app/storage/manifest.py",
                "backend/tests/unit/test_t009.py",
                "specs/001-ai-content-studio/tasks.md",
            )
        )
    )
    monkeypatch.setattr(
        runner.process_runner,
        "run_process",
        lambda argv, **kwargs: FakeProcessResult(status="PASS", stdout_lines=_bookkeeping_diff_lines() if "diff" in argv else ()),
    )

    assert runner._validate_pre_commit_state(repo) is None


def test_pre_commit_selects_current_closed_task_not_old_committed_task(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T010",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "allowlist": ("backend/app/storage/legacy.py",),
                "head_sha": "1" * 40,
                "commit_sha": "1" * 40,
            },
            {
                "task_id": "T022",
                "checkbox": "X",
                "state": TaskLifecycleState.CLOSED,
                "allowlist": (
                    "backend/app/storage/artifact_store.py",
                    "backend/app/storage/local_store.py",
                    "backend/app/storage/manifest.py",
                    "backend/tests/unit/test_t009.py",
                ),
            },
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(
            FakeGitStatus(
                staged=(
                    "backend/app/storage/artifact_store.py",
                    "backend/app/storage/local_store.py",
                    "backend/app/storage/manifest.py",
                    "backend/tests/unit/test_t009.py",
                    "specs/001-ai-content-studio/tasks.md",
                )
            )
        ),
    )
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS", stdout_lines=_task_checkbox_diff_lines("T022")),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        [],
    )

    result = runner.run_hook("pre-commit", heartbeat_seconds=0)

    assert result.status == "PASS"


def test_pre_commit_rejects_stale_closed_task_allowlist_for_current_task(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T010",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "allowlist": (
                    "backend/app/storage/artifact_store.py",
                    "backend/app/storage/local_store.py",
                    "backend/app/storage/manifest.py",
                    "backend/tests/unit/test_t009.py",
                ),
                "head_sha": "1" * 40,
                "commit_sha": "1" * 40,
            },
            {
                "task_id": "T022",
                "checkbox": "X",
                "state": TaskLifecycleState.CLOSED,
                "allowlist": ("backend/app/storage/local_store.py",),
            },
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(
            FakeGitStatus(
                staged=("backend/app/storage/artifact_store.py", "specs/001-ai-content-studio/tasks.md")
            )
        ),
    )
    monkeypatch.setattr(
        runner.process_runner,
        "run_process",
        lambda argv, **kwargs: FakeProcessResult(status="PASS", stdout_lines=_task_checkbox_diff_lines("T022") if "diff" in argv else ()),
    )

    error = runner._validate_pre_commit_state(runner.Repository(tmp_path))

    assert error is not None
    assert "backend/app/storage/artifact_store.py" in error


def test_pre_commit_rejects_two_task_checkbox_changes(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T010",
                "checkbox": "X",
                "state": TaskLifecycleState.CLOSED,
                "allowlist": ("backend/app/storage/artifact_store.py",),
            },
            {
                "task_id": "T022",
                "checkbox": "X",
                "state": TaskLifecycleState.CLOSED,
                "allowlist": ("backend/app/storage/local_store.py",),
            },
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(
            FakeGitStatus(
                staged=("backend/app/storage/artifact_store.py", "backend/app/storage/local_store.py", "specs/001-ai-content-studio/tasks.md")
            )
        ),
    )
    _patch_run_process(
        monkeypatch,
        [FakeProcessResult(status="PASS", stdout_lines=_two_task_checkbox_diff_lines())],
        [],
    )

    result = runner.run_hook("pre-commit", heartbeat_seconds=0)

    assert result.status == "FAIL"
    assert result.reason is not None
    assert "exactly one checkbox" in result.reason


def test_pre_commit_validation_rejects_path_outside_allowlist(monkeypatch, tmp_path):
    _write_closed_task_fixture(tmp_path)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    repo = FakeRepository(
        FakeGitStatus(
            staged=("backend/app/storage/artifact_store.py", "backend/app/storage_evil/file.py", "specs/001-ai-content-studio/tasks.md")
        )
    )
    monkeypatch.setattr(
        runner.process_runner,
        "run_process",
        lambda argv, **kwargs: FakeProcessResult(status="PASS", stdout_lines=_task_checkbox_diff_lines() if "diff" in argv else ()),
    )

    error = runner._validate_pre_commit_state(repo)

    assert error is not None
    assert "backend/app/storage_evil/file.py" in error


def test_pre_commit_json_failure_includes_reason(monkeypatch, capsys, tmp_path):
    _write_closed_task_fixture(tmp_path)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(FakeGitStatus(staged=("specs/001-ai-content-studio/tasks.md",))),
    )
    monkeypatch.setattr(
        runner.process_runner,
        "run_process",
        lambda argv, **kwargs: FakeProcessResult(status="PASS", stdout_lines=_task_checkbox_diff_lines() if "diff" in argv else ()),
    )
    (tmp_path / ".specify" / "runtime" / "task-state" / "T009.json").unlink()
    (tmp_path / ".specify" / "runtime" / "task-receipts" / "T009.json").unlink()

    exit_code = runner.main(["pre-commit", "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAIL"
    assert payload["reason"]
    assert payload["commands"] == []


def test_pre_commit_validation_rejects_similar_prefix_directory(monkeypatch, tmp_path):
    _write_closed_task_fixture(tmp_path)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    repo = FakeRepository(FakeGitStatus(staged=("backend/app/storage_evil/file.py", "specs/001-ai-content-studio/tasks.md")))
    monkeypatch.setattr(
        runner.process_runner,
        "run_process",
        lambda argv, **kwargs: FakeProcessResult(status="PASS", stdout_lines=_task_checkbox_diff_lines() if "diff" in argv else ()),
    )

    error = runner._validate_pre_commit_state(repo)

    assert error is not None
    assert "backend/app/storage_evil/file.py" in error


def test_pre_commit_validation_normalizes_windows_backslashes(monkeypatch, tmp_path):
    _write_closed_task_fixture(tmp_path)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    repo = FakeRepository(
        FakeGitStatus(staged=(r"backend\app\storage\local_store.py", "specs/001-ai-content-studio/tasks.md"))
    )
    monkeypatch.setattr(
        runner.process_runner,
        "run_process",
        lambda argv, **kwargs: FakeProcessResult(status="PASS", stdout_lines=_task_checkbox_diff_lines() if "diff" in argv else ()),
    )

    assert runner._validate_pre_commit_state(repo) is None


def test_pre_commit_hook_runs_closed_task_without_name_error(monkeypatch, tmp_path):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    _write_closed_task_fixture(tmp_path)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "Repository", lambda _root: FakeRepository(
        FakeGitStatus(
            staged=(
                "backend/app/storage/artifact_store.py",
                "backend/app/storage/local_store.py",
                "backend/app/storage/manifest.py",
                "backend/tests/unit/test_t009.py",
                "specs/001-ai-content-studio/tasks.md",
            )
        )
    ))
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS", stdout_lines=_task_checkbox_diff_lines()),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        calls,
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([200.0, 200.01, 201.0, 201.01, 202.0, 202.01]))

    result = runner.run_hook("pre-commit", heartbeat_seconds=0)

    assert result.status == "PASS"
    assert len(calls) == 4


def test_post_commit_promotes_only_task_in_commit_and_preserves_receipt_evidence(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T010",
                "checkbox": "X",
                "state": TaskLifecycleState.CLOSED,
                "allowlist": ("backend/app/storage/legacy.py",),
                "head_sha": "1" * 40,
            },
            {
                "task_id": "T022",
                "checkbox": "X",
                "state": TaskLifecycleState.CLOSED,
                "allowlist": (
                    "backend/app/storage/artifact_store.py",
                    "backend/app/storage/local_store.py",
                    "backend/app/storage/manifest.py",
                    "backend/tests/unit/test_t009.py",
                ),
                "head_sha": "2" * 40,
                "summary": "closed",
                "files_touched": ("backend/app/storage/artifact_store.py", "backend/tests/unit/test_t009.py"),
                "notes": ("evidence note",),
                "validation": ({"name": "pytest", "status": "PASS"},),
                "review_verdict": "PASS",
                "safe_to_close": True,
                "closure_checkbox_before": " ",
                "closure_checkbox_after": "X",
                "closure_task_line": 1,
                "stages": (
                    {
                        "name": "validated",
                        "status": "PASS",
                        "updated_at": "2026-07-25T00:00:00Z",
                        "details": {"checks": ["pytest"]},
                    },
                ),
            },
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(FakeGitStatus(branch="feature/E003", head_sha="3" * 40)),
    )
    monkeypatch.setattr(runner, "_current_commit_subject", lambda: "feat(T022): Implement artifact storage abstraction and local store")
    monkeypatch.setattr(
        runner,
        "_commit_paths",
        lambda _commit_ref: {
            "backend/app/storage/artifact_store.py",
            "backend/app/storage/local_store.py",
            "backend/app/storage/manifest.py",
            "backend/tests/unit/test_t009.py",
            "specs/001-ai-content-studio/tasks.md",
        },
    )
    monkeypatch.setattr(runner, "_task_commit_task_ids_in_commit", lambda _commit_ref, tasks_path=None: ["T022"])

    result = runner.run_hook("post-commit")

    assert result.status == "PASS"
    state_t022 = _load_task_state_record("T022", tmp_path)
    receipt_t022 = _load_task_receipt_record("T022", tmp_path)
    state_t010 = _load_task_state_record("T010", tmp_path)
    receipt_t010 = _load_task_receipt_record("T010", tmp_path)
    assert state_t022.state == TaskLifecycleState.COMMITTED
    assert state_t022.head_sha == "3" * 40
    assert receipt_t022.state == TaskLifecycleState.COMMITTED
    assert receipt_t022.commit_sha == "3" * 40
    assert receipt_t022.review_verdict == "PASS"
    assert receipt_t022.safe_to_close is True
    assert receipt_t022.files_touched == ("backend/app/storage/artifact_store.py", "backend/tests/unit/test_t009.py")
    assert receipt_t022.notes == ("evidence note",)
    assert len(receipt_t022.validation) == 1
    assert receipt_t022.stages[0].name == "validated"
    assert receipt_t010.state == TaskLifecycleState.CLOSED
    assert receipt_t010.commit_sha == ""
    assert receipt_t010.review_verdict == ""


def test_post_commit_idempotent_for_same_task_and_sha(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T022",
                "checkbox": "X",
                "state": TaskLifecycleState.CLOSED,
                "allowlist": (
                    "backend/app/storage/artifact_store.py",
                    "backend/app/storage/local_store.py",
                    "backend/app/storage/manifest.py",
                    "backend/tests/unit/test_t009.py",
                ),
                "head_sha": "2" * 40,
            }
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(FakeGitStatus(branch="feature/E003", head_sha="3" * 40)),
    )
    monkeypatch.setattr(runner, "_current_commit_subject", lambda: "feat(T022): Implement artifact storage abstraction and local store")
    monkeypatch.setattr(
        runner,
        "_commit_paths",
        lambda _commit_ref: {
            "backend/app/storage/artifact_store.py",
            "backend/app/storage/local_store.py",
            "backend/app/storage/manifest.py",
            "backend/tests/unit/test_t009.py",
            "specs/001-ai-content-studio/tasks.md",
        },
    )
    monkeypatch.setattr(runner, "_task_commit_task_ids_in_commit", lambda _commit_ref, tasks_path=None: ["T022"])

    first = runner.run_hook("post-commit")
    second = runner.run_hook("post-commit")

    assert first.status == "PASS"
    assert second.status == "PASS"
    assert _load_task_state_record("T022", tmp_path).state == TaskLifecycleState.COMMITTED
    assert _load_task_receipt_record("T022", tmp_path).state == TaskLifecycleState.COMMITTED


def test_post_commit_tooling_commit_does_not_promote_tasks(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T022",
                "checkbox": "X",
                "state": TaskLifecycleState.CLOSED,
                "allowlist": (
                    "backend/app/storage/artifact_store.py",
                    "backend/app/storage/local_store.py",
                    "backend/app/storage/manifest.py",
                    "backend/tests/unit/test_t009.py",
                ),
            }
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "Repository", lambda _root: FakeRepository(FakeGitStatus(branch="feature/E003", head_sha="3" * 40)))
    monkeypatch.setattr(runner, "_current_commit_subject", lambda: "chore: tooling")

    result = runner.run_hook("post-commit")

    assert result.status == "PASS"
    assert _load_task_state_record("T022", tmp_path).state == TaskLifecycleState.CLOSED
    assert _load_task_receipt_record("T022", tmp_path).state == TaskLifecycleState.CLOSED


def test_post_commit_promotes_t023_from_head_diff_without_cached_index(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T023",
                "title": "Implement ProviderConfig validation before workflow execution",
                "checkbox": "X",
                "state": TaskLifecycleState.CLOSED,
                "receipt_state": TaskLifecycleState.CLOSED,
                "allowlist": (
                    "backend/app/domain/workflow_config.py",
                    "backend/app/workflow/engine.py",
                    "backend/app/providers/validation.py",
                    "backend/tests/unit/test_t023.py",
                ),
                "branch": "epic/E003-artifact-storage",
                "head_sha": "6" * 40,
                "summary": "reviewed and validated",
                "files_touched": (
                    "backend/app/domain/workflow_config.py",
                    "backend/app/workflow/engine.py",
                    "backend/app/providers/validation.py",
                    "backend/tests/unit/test_t023.py",
                ),
                "notes": ("validation pass",),
                "validation": ({"name": "pytest", "status": "PASS"},),
                "review_verdict": "PASS",
                "safe_to_close": True,
                "closure_checkbox_before": " ",
                "closure_checkbox_after": "X",
                "closure_task_line": 357,
                "stages": (
                    {
                        "name": "validated",
                        "status": "PASS",
                        "updated_at": "2026-07-25T00:00:00Z",
                        "details": {"checks": ["pytest"]},
                    },
                    {
                        "name": "reviewed",
                        "status": "PASS",
                        "updated_at": "2026-07-25T00:00:01Z",
                        "details": {"verdict": "PASS"},
                    },
                    {
                        "name": "closed",
                        "status": "PASS",
                        "updated_at": "2026-07-25T00:00:02Z",
                        "details": {"checkbox": "X"},
                    },
                ),
            }
        ],
    )
    calls, fake_run_process = _post_commit_git_runner_factory(
        task_id="T023",
        title="Implement ProviderConfig validation before workflow execution",
        parent_sha="5" * 40,
        head_sha="6" * 40,
        allowlist_paths=(
            "backend/app/domain/workflow_config.py",
            "backend/app/workflow/engine.py",
            "backend/app/providers/validation.py",
            "backend/tests/unit/test_t023.py",
        ),
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(FakeGitStatus(branch="epic/E003-artifact-storage", head_sha="6" * 40)),
    )
    monkeypatch.setattr(runner.process_runner, "run_process", fake_run_process)

    result = runner.run_hook("post-commit")

    assert result.status == "PASS"
    assert all("--cached" not in call[0] for call in calls)
    state = _load_task_state_record("T023", tmp_path)
    receipt = _load_task_receipt_record("T023", tmp_path)
    assert state.state == TaskLifecycleState.COMMITTED
    assert state.head_sha == "6" * 40
    assert receipt.state == TaskLifecycleState.COMMITTED
    assert receipt.commit_sha == "6" * 40
    assert receipt.review_verdict == "PASS"
    assert receipt.safe_to_close is True
    assert receipt.files_touched == (
        "backend/app/domain/workflow_config.py",
        "backend/app/workflow/engine.py",
        "backend/app/providers/validation.py",
        "backend/tests/unit/test_t023.py",
    )
    assert receipt.notes == ("validation pass",)
    assert receipt.validation == ({"name": "pytest", "status": "PASS"},)
    assert [stage.name for stage in receipt.stages] == ["validated", "reviewed", "closed"]
    assert _load_task_state_record("T023", tmp_path).state == TaskLifecycleState.COMMITTED


def test_post_commit_rejects_two_checkbox_changes(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T023",
                "title": "Implement ProviderConfig validation before workflow execution",
                "checkbox": "X",
                "state": TaskLifecycleState.CLOSED,
                "receipt_state": TaskLifecycleState.CLOSED,
                "allowlist": ("backend/app/domain/workflow_config.py", "backend/app/workflow/engine.py"),
                "head_sha": "6" * 40,
            },
            {
                "task_id": "T024",
                "title": "Implement ProviderConfig validation before workflow execution",
                "checkbox": "X",
                "state": TaskLifecycleState.CLOSED,
                "receipt_state": TaskLifecycleState.CLOSED,
                "allowlist": ("backend/app/workflow/engine.py",),
                "head_sha": "6" * 40,
            },
        ],
    )
    calls, fake_run_process = _post_commit_git_runner_factory(
        task_id="T023",
        title="Implement ProviderConfig validation before workflow execution",
        parent_sha="5" * 40,
        head_sha="6" * 40,
        allowlist_paths=("backend/app/domain/workflow_config.py", "backend/app/workflow/engine.py"),
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(FakeGitStatus(branch="epic/E003-artifact-storage", head_sha="6" * 40)),
    )
    monkeypatch.setattr(runner.process_runner, "run_process", fake_run_process)
    monkeypatch.setattr(
        runner,
        "_task_commit_task_ids_in_commit",
        lambda _commit_ref, tasks_path=None: ["T023", "T024"],
    )
    monkeypatch.setattr(
        runner,
        "_commit_paths",
        lambda _commit_ref: {"backend/app/domain/workflow_config.py", "backend/app/workflow/engine.py", "specs/001-ai-content-studio/tasks.md"},
    )

    result = runner.run_hook("post-commit")

    assert result.status == "FAIL"
    assert result.reason is not None
    assert "exactly task T023" in result.reason
    assert all("--cached" not in call[0] for call in calls)


def test_post_commit_rejects_path_outside_allowlist(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T023",
                "title": "Implement ProviderConfig validation before workflow execution",
                "checkbox": "X",
                "state": TaskLifecycleState.CLOSED,
                "receipt_state": TaskLifecycleState.CLOSED,
                "allowlist": ("backend/app/domain/workflow_config.py",),
                "head_sha": "6" * 40,
            }
        ],
    )
    calls, fake_run_process = _post_commit_git_runner_factory(
        task_id="T023",
        title="Implement ProviderConfig validation before workflow execution",
        parent_sha="5" * 40,
        head_sha="6" * 40,
        allowlist_paths=("backend/app/domain/workflow_config.py", "backend/app/providers/validation.py"),
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(FakeGitStatus(branch="epic/E003-artifact-storage", head_sha="6" * 40)),
    )
    monkeypatch.setattr(runner.process_runner, "run_process", fake_run_process)

    result = runner.run_hook("post-commit")

    assert result.status == "FAIL"
    assert result.reason is not None
    assert "outside task allowlist" in result.reason
    assert "backend/app/providers/validation.py" in result.reason
    assert all("--cached" not in call[0] for call in calls)


def test_pre_push_removes_tooling_pytest_and_runs_full_pytest_once(monkeypatch, tmp_path):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "Repository", lambda _root: FakeRepository(FakeGitStatus()))
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        calls,
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([300.0, 300.01, 301.0, 301.01, 302.0, 302.01, 303.0, 303.01]))

    result = runner.run_hook("pre-push")

    assert result.status == "PASS"
    assert [call[0] for call in calls] == [
        (sys.executable, "-m", "backend.app.tooling.workstream_validation"),
        (sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
        (sys.executable, "-m", "pytest"),
        ("git", "--no-pager", "diff", "--check"),
    ]
    assert all("backend/tests/unit/tooling" not in call[0] for call in calls)
    assert sum(1 for call in calls if call[0] == (sys.executable, "-m", "pytest")) == 1
    assert [call[1]["timeout_seconds"] for call in calls] == [20, 20, 300, 20]


def test_pre_push_accepts_matching_committed_records_on_epic_branch(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T022",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "branch": "epic/E003-artifact-storage",
                "head_sha": "2" * 40,
                "commit_sha": "2" * 40,
            }
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(
            FakeGitStatus(branch="epic/E003-artifact-storage", head_sha="3" * 40),
            ancestors={("2" * 40, "3" * 40)},
        ),
    )
    monkeypatch.setattr(runner, "_commit_exists", lambda commit_sha: commit_sha == "2" * 40)
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        [],
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([300.0, 300.01, 301.0, 301.01, 302.0, 302.01, 303.0, 303.01]))

    result = runner.run_hook("pre-push")

    assert result.status == "PASS"


def test_pre_push_rejects_matching_committed_record_outside_history_on_epic_branch(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T022",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "branch": "epic/E003-artifact-storage",
                "head_sha": "2" * 40,
                "commit_sha": "2" * 40,
            }
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(FakeGitStatus(branch="epic/E003-artifact-storage", head_sha="3" * 40)),
    )
    monkeypatch.setattr(runner, "_commit_exists", lambda commit_sha: commit_sha == "2" * 40)

    result = runner.run_hook("pre-push")

    assert result.status == "FAIL"
    assert result.reason is not None
    assert "ancestor" in result.reason


def test_pre_push_accepts_fix_branch_with_only_unrelated_committed_records(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T022",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "branch": "epic/E003-artifact-storage",
                "head_sha": "2" * 40,
                "commit_sha": "2" * 40,
            }
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(FakeGitStatus(branch="fix/pre-push-epic-closure", head_sha="3" * 40)),
    )
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        [],
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([110.0, 111.0, 112.0, 113.0, 114.0, 115.0, 116.0, 117.0, 118.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([320.0, 320.01, 321.0, 321.01, 322.0, 322.01, 323.0, 323.01]))

    result = runner.run_hook("pre-push")

    assert result.status == "PASS"


def test_pre_push_accepts_closure_branch_for_completed_epic(monkeypatch, tmp_path):
    _write_epic_manifest_fixture(tmp_path, status="completed", branch="epic/E003-artifact-storage")
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T023",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "branch": "epic/E003-artifact-storage",
                "head_sha": "6" * 40,
                "commit_sha": "6" * 40,
            }
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(
            FakeGitStatus(branch="chore/close-E003", head_sha="7" * 40),
            ancestors={("6" * 40, "7" * 40)},
        ),
    )
    monkeypatch.setattr(runner, "_commit_exists", lambda commit_sha: commit_sha == "6" * 40)
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        [],
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([120.0, 121.0, 122.0, 123.0, 124.0, 125.0, 126.0, 127.0, 128.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([330.0, 330.01, 331.0, 331.01, 332.0, 332.01, 333.0, 333.01]))

    result = runner.run_hook("pre-push")

    assert result.status == "PASS"


def test_pre_push_accepts_ancestor_task_commit(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T022",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "allowlist": (
                    "backend/app/storage/artifact_store.py",
                    "backend/app/storage/local_store.py",
                    "backend/app/storage/manifest.py",
                    "backend/tests/unit/test_t009.py",
                ),
                "head_sha": "2" * 40,
                "commit_sha": "2" * 40,
            }
        ],
    )
    current_head = "3" * 40
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(
            FakeGitStatus(branch="feature/E003", head_sha=current_head),
            ancestors={("2" * 40, current_head)},
        ),
    )
    monkeypatch.setattr(runner, "_commit_exists", lambda commit_sha: commit_sha == "2" * 40)
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        [],
    )

    result = runner.run_hook("pre-push")

    assert result.status == "PASS"


def test_pre_push_rejects_commit_outside_history(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T022",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "allowlist": (
                    "backend/app/storage/artifact_store.py",
                    "backend/app/storage/local_store.py",
                    "backend/app/storage/manifest.py",
                    "backend/tests/unit/test_t009.py",
                ),
                "head_sha": "2" * 40,
                "commit_sha": "2" * 40,
            }
        ],
    )
    current_head = "3" * 40
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(FakeGitStatus(branch="feature/E003", head_sha=current_head)),
    )
    monkeypatch.setattr(runner, "_commit_exists", lambda commit_sha: commit_sha == "2" * 40)

    result = runner.run_hook("pre-push")

    assert result.status == "FAIL"
    assert result.reason is not None
    assert "ancestor" in result.reason


def test_pre_push_rejects_closure_branch_when_epic_is_active(monkeypatch, tmp_path):
    _write_epic_manifest_fixture(tmp_path, status="active", branch="epic/E003-artifact-storage")
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T023",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "branch": "epic/E003-artifact-storage",
                "head_sha": "6" * 40,
                "commit_sha": "6" * 40,
            }
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "Repository", lambda _root: FakeRepository(FakeGitStatus(branch="chore/close-E003", head_sha="7" * 40)))

    result = runner.run_hook("pre-push")

    assert result.status == "FAIL"
    assert result.reason is not None
    assert "completed" in result.reason


def test_pre_push_rejects_closure_branch_when_record_branch_differs_from_manifest(monkeypatch, tmp_path):
    _write_epic_manifest_fixture(tmp_path, status="completed", branch="epic/E003-artifact-storage")
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T023",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "branch": "epic/E003-elsewhere",
                "head_sha": "6" * 40,
                "commit_sha": "6" * 40,
            }
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(
            FakeGitStatus(branch="chore/close-E003", head_sha="7" * 40),
            ancestors={("6" * 40, "7" * 40)},
        ),
    )
    monkeypatch.setattr(runner, "_commit_exists", lambda commit_sha: commit_sha == "6" * 40)

    result = runner.run_hook("pre-push")

    assert result.status == "FAIL"
    assert result.reason is not None
    assert "committed task branch" in result.reason


def test_pre_push_rejects_closure_branch_when_task_commit_is_not_in_history(monkeypatch, tmp_path):
    _write_epic_manifest_fixture(tmp_path, status="completed", branch="epic/E003-artifact-storage")
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T023",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "branch": "epic/E003-artifact-storage",
                "head_sha": "6" * 40,
                "commit_sha": "6" * 40,
            }
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "Repository", lambda _root: FakeRepository(FakeGitStatus(branch="chore/close-E003", head_sha="7" * 40)))
    monkeypatch.setattr(runner, "_commit_exists", lambda commit_sha: commit_sha == "6" * 40)

    result = runner.run_hook("pre-push")

    assert result.status == "FAIL"
    assert result.reason is not None
    assert "ancestor" in result.reason


def test_pre_push_closure_branch_ignores_committed_records_from_e002(monkeypatch, tmp_path):
    _write_epic_manifest_fixture(tmp_path, status="completed", branch="epic/E003-artifact-storage")
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T023",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "branch": "epic/E003-artifact-storage",
                "head_sha": "6" * 40,
                "commit_sha": "6" * 40,
            },
            {
                "task_id": "T031",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "branch": "epic/E002-artifact-queue",
                "head_sha": "5" * 40,
                "commit_sha": "5" * 40,
                "epic": "E002",
                "milestone": "M001",
            },
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(
            FakeGitStatus(branch="chore/close-E003", head_sha="7" * 40),
            ancestors={("6" * 40, "7" * 40)},
        ),
    )
    monkeypatch.setattr(runner, "_commit_exists", lambda commit_sha: commit_sha in {"6" * 40, "5" * 40})
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        [],
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([130.0, 131.0, 132.0, 133.0, 134.0, 135.0, 136.0, 137.0, 138.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([340.0, 340.01, 341.0, 341.01, 342.0, 342.01, 343.0, 343.01]))

    result = runner.run_hook("pre-push")

    assert result.status == "PASS"


def test_pre_push_similarity_branch_is_not_closure_branch(monkeypatch, tmp_path):
    _write_task_entries_fixture(
        tmp_path,
        [
            {
                "task_id": "T022",
                "checkbox": "X",
                "state": TaskLifecycleState.COMMITTED,
                "receipt_state": TaskLifecycleState.COMMITTED,
                "branch": "epic/E003-artifact-storage",
                "head_sha": "2" * 40,
                "commit_sha": "2" * 40,
            }
        ],
    )
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(
        runner,
        "Repository",
        lambda _root: FakeRepository(FakeGitStatus(branch="chore/close-E003-extra", head_sha="3" * 40)),
    )
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        [],
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([140.0, 141.0, 142.0, 143.0, 144.0, 145.0, 146.0, 147.0, 148.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([350.0, 350.01, 351.0, 351.01, 352.0, 352.01, 353.0, 353.01]))

    result = runner.run_hook("pre-push")

    assert result.status == "PASS"


def test_ci_uses_commit_range_and_full_pytest_once(monkeypatch):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="PASS"),
        ],
        calls,
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([500.0, 501.0, 502.0, 503.0, 504.0, 505.0, 506.0, 507.0, 508.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([600.0, 600.01, 601.0, 601.01, 602.0, 602.01, 603.0, 603.01]))

    result = runner.run_hook("ci", base_sha="b" * 40, head_sha="c" * 40)

    assert result.status == "PASS"
    assert [call[0] for call in calls] == [
        (sys.executable, "-m", "backend.app.tooling.workstream_validation"),
        (sys.executable, "-m", "backend.app.tooling.repository_checks", "--mode", "task-metadata"),
        (sys.executable, "-m", "pytest"),
        ("git", "--no-pager", "diff", "--check", "b" * 40 + "..." + "c" * 40),
    ]
    assert sum(1 for call in calls if call[0] == (sys.executable, "-m", "pytest")) == 1
    assert [call[1]["timeout_seconds"] for call in calls] == [30, 30, 600, 30]


def test_global_timeout_pre_commit_prints_global_timeout_and_stops(monkeypatch, capsys, tmp_path):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "Repository", lambda _root: FakeRepository(FakeGitStatus()))
    _patch_run_process(monkeypatch, [FakeProcessResult(status="PASS")], calls)
    monkeypatch.setattr(runner.time, "monotonic", _clock([0.0, 1.0, 2.0, 61.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([700.0, 700.01, 700.02, 700.03]))

    exit_code = runner.main(["pre-commit"])

    assert exit_code == 1
    assert len(calls) == 1
    output = capsys.readouterr().out
    assert "GLOBAL_TIMEOUT" in output
    assert "status: TIMEOUT" in output


def test_global_timeout_pre_push(monkeypatch, tmp_path):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "Repository", lambda _root: FakeRepository(FakeGitStatus()))
    _patch_run_process(monkeypatch, [FakeProcessResult(status="PASS")], calls)
    monkeypatch.setattr(runner.time, "monotonic", _clock([0.0, 1.0, 2.0, 481.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([800.0, 800.01, 800.02, 800.03]))

    result = runner.run_hook("pre-push")

    assert result.status == "TIMEOUT"
    assert result.global_timeout is True
    assert len(calls) == 1


def test_global_timeout_ci(monkeypatch):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    _patch_run_process(monkeypatch, [FakeProcessResult(status="PASS")], calls)
    monkeypatch.setattr(runner.time, "monotonic", _clock([0.0, 1.0, 2.0, 901.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([900.0, 900.01, 900.02, 900.03]))

    result = runner.run_hook("ci", base_sha="b" * 40, head_sha="c" * 40)

    assert result.status == "TIMEOUT"
    assert result.global_timeout is True
    assert len(calls) == 1


def test_timeout_stops_following_commands(monkeypatch, tmp_path):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "Repository", lambda _root: FakeRepository(FakeGitStatus()))
    _patch_run_process(monkeypatch, [FakeProcessResult(status="PASS")], calls)
    monkeypatch.setattr(runner.time, "monotonic", _clock([0.0, 1.0, 2.0, 481.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([1000.0, 1000.01, 1000.02, 1000.03]))

    result = runner.run_hook("pre-push")

    assert result.status == "TIMEOUT"
    assert len(calls) == 1


def test_heartbeat_and_json_output_to_correct_streams(monkeypatch, capsys, tmp_path):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "Repository", lambda _root: FakeRepository(FakeGitStatus()))

    def fake_run_process(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        print("START fake pid=123 timeout=20s", file=sys.stderr)
        print("HEARTBEAT fake elapsed=30s pid=123", file=sys.stderr)
        print("PASS fake duration=42ms", file=sys.stderr)
        return FakeProcessResult(status="PASS")

    monkeypatch.setattr(runner.process_runner, "run_process", fake_run_process)
    monkeypatch.setattr(runner.time, "monotonic", _clock([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([1100.0, 1100.01, 1101.0, 1101.01, 1102.0, 1102.01]))

    exit_code = runner.main(["pre-commit", "--json"])

    assert exit_code == 0
    assert len(calls) == 3
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "PASS"
    assert "START fake" in captured.err
    assert "HEARTBEAT fake" in captured.err
    assert "PASS fake" in captured.err


def test_command_results_include_limited_failure_output(monkeypatch, tmp_path):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "Repository", lambda _root: FakeRepository(FakeGitStatus()))
    _patch_run_process(
        monkeypatch,
        [
            FakeProcessResult(status="PASS"),
            FakeProcessResult(status="FAIL", exit_code=1, stdout_lines=("first line", "second line"), stderr_lines=("stderr line",)),
        ],
        calls,
    )
    monkeypatch.setattr(runner.time, "monotonic", _clock([20.0, 21.0, 22.0, 23.0]))
    monkeypatch.setattr(runner.time, "perf_counter", _clock([1200.0, 1200.01, 1201.0, 1201.01]))

    result = runner.run_hook("pre-commit")

    assert result.status == "FAIL"
    assert len(result.commands) == 2
    assert result.commands[1].output == "first line | stderr line"


def test_invalid_mode_returns_usage_error():
    exit_code = runner.main(["bad-mode"])

    assert exit_code == 2

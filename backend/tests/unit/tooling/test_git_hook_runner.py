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
        state = entry.get("state", TaskLifecycleState.CLOSED)
        receipt_state = entry.get("receipt_state", state)
        run_id = str(entry.get("run_id", "run-001"))
        updated_at = str(entry.get("updated_at", "2026-07-25T00:00:00Z"))
        validation_commands = tuple(str(item) for item in entry.get("validation_commands", ("python -m pytest backend/tests/unit/tooling/test_git_hook_runner.py",)))
        rows.extend(
            [
                f"- [{checkbox}] {task_id} {title}",
                "Milestone: M001",
                "Epic: E003",
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


def _task_checkbox_diff_lines(task_id: str = "T009") -> tuple[str, ...]:
    return (
        "diff --git a/specs/001-ai-content-studio/tasks.md b/specs/001-ai-content-studio/tasks.md",
        "@@ -1,1 +1,1 @@",
        f"-- [ ] {task_id} Implement artifact storage abstraction and local store",
        f"+- [X] {task_id} Implement artifact storage abstraction and local store",
    )


def _bookkeeping_diff_lines() -> tuple[str, ...]:
    return (
        "diff --git a/specs/001-ai-content-studio/tasks.md b/specs/001-ai-content-studio/tasks.md",
        "@@ -1,1 +1,1 @@",
        "-- bookkeeping note",
        "++ bookkeeping note",
    )


def _two_task_checkbox_diff_lines() -> tuple[str, ...]:
    return (
        "diff --git a/specs/001-ai-content-studio/tasks.md b/specs/001-ai-content-studio/tasks.md",
        "@@ -1,2 +1,2 @@",
        "-- [ ] T010 Implement artifact storage abstraction and local store",
        "+- [X] T010 Implement artifact storage abstraction and local store",
        "-- [ ] T022 Implement artifact storage abstraction and local store",
        "+- [X] T022 Implement artifact storage abstraction and local store",
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
    monkeypatch.setattr(runner, "_current_commit_message", lambda: "feat(T022): Implement artifact storage abstraction and local store")
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
    monkeypatch.setattr(runner, "_current_commit_message", lambda: "feat(T022): Implement artifact storage abstraction and local store")
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
    monkeypatch.setattr(runner, "_current_commit_message", lambda: "chore: tooling")

    result = runner.run_hook("post-commit")

    assert result.status == "PASS"
    assert _load_task_state_record("T022", tmp_path).state == TaskLifecycleState.CLOSED
    assert _load_task_receipt_record("T022", tmp_path).state == TaskLifecycleState.CLOSED


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

"""Atomic JSON persistence for local autopilot state."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .models import AutopilotRequest, AutopilotRun, CommandResult, PullRequestInfo, RunMode, RunStatus, ScopeType, TaskResult

ROOT = Path(__file__).resolve().parents[4]
AUTOPILOT_STATE_DIR = ROOT / ".specify" / "runtime" / "autopilot"


def run_state_path(run_id: str, root: Path | str = ROOT) -> Path:
    normalized = _validate_run_id(run_id)
    root_path = Path(root)
    return root_path / ".specify" / "runtime" / "autopilot" / f"{normalized}.json"


def save_run_state(run: AutopilotRun, root: Path | str = ROOT) -> Path:
    path = run_state_path(run.run_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _encode(run)
    _write_atomic_json(path, payload)
    return path


def load_run_state(run_id: str, root: Path | str = ROOT) -> AutopilotRun:
    path = run_state_path(run_id, root)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"run state does not exist: {path}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name}: run state must be a JSON object")
    return _decode_run(payload)


def _validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    normalized = run_id.strip()
    if normalized != Path(normalized).name or any(separator in normalized for separator in ("/", "\\", "..")):
        raise ValueError("run_id must be a safe filename-style identifier")
    return normalized


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _encode(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    return value


def _decode_scope_type(value: Any) -> ScopeType:
    return ScopeType(value)


def _decode_run_mode(value: Any) -> RunMode:
    return RunMode(value)


def _decode_run_status(value: Any) -> RunStatus:
    return RunStatus(value)


def _decode_command_result(value: Any) -> CommandResult:
    if not isinstance(value, dict):
        raise ValueError("command result must be a mapping")
    return CommandResult(
        command=tuple(str(part) for part in value.get("command", ())),
        status=str(value.get("status")),
        exit_code=value.get("exit_code"),
        duration_ms=int(value.get("duration_ms", 0)),
        timed_out=bool(value.get("timed_out", False)),
        stdout_lines=tuple(str(part) for part in value.get("stdout_lines", ()) or ()),
        stderr_lines=tuple(str(part) for part in value.get("stderr_lines", ()) or ()),
        output_truncated=bool(value.get("output_truncated", False)),
    )


def _decode_task_result(value: Any) -> TaskResult:
    if not isinstance(value, dict):
        raise ValueError("task result must be a mapping")
    return TaskResult(
        task_id=str(value.get("task_id")),
        status=_decode_run_status(value.get("status")),
        command_results=tuple(_decode_command_result(item) for item in value.get("command_results", ()) or ()),
        commit_sha=value.get("commit_sha"),
        title=value.get("title"),
    )


def _decode_request(value: Any) -> AutopilotRequest:
    if not isinstance(value, dict):
        raise ValueError("request must be a mapping")
    return AutopilotRequest(
        scope_type=_decode_scope_type(value.get("scope_type")),
        scope_id=str(value.get("scope_id")),
        run_mode=_decode_run_mode(value.get("run_mode")),
        repo_path=str(value.get("repo_path")),
        created_by=str(value.get("created_by", "user")),
        human_authorized=bool(value.get("human_authorized", True)),
    )


def _decode_pull_request(value: Any) -> PullRequestInfo:
    if not isinstance(value, dict):
        raise ValueError("pull_request must be a mapping")
    return PullRequestInfo(
        number=int(value.get("number")),
        url=str(value.get("url")),
        title=str(value.get("title")),
        base_branch=str(value.get("base_branch")),
        head_branch=str(value.get("head_branch")),
        draft=bool(value.get("draft", True)),
        merged=bool(value.get("merged", False)),
    )


def _decode_run(value: dict[str, Any]) -> AutopilotRun:
    return AutopilotRun(
        run_id=str(value.get("run_id")),
        request=_decode_request(value.get("request")),
        status=_decode_run_status(value.get("status")),
        created_at=str(value.get("created_at")),
        updated_at=str(value.get("updated_at")),
        epic_id=value.get("epic_id"),
        milestone_id=value.get("milestone_id"),
        branch_name=value.get("branch_name"),
        current_task_id=value.get("current_task_id"),
        task_results=tuple(_decode_task_result(item) for item in value.get("task_results", ()) or ()),
        command_results=tuple(_decode_command_result(item) for item in value.get("command_results", ()) or ()),
        pull_request=_decode_pull_request(value["pull_request"]) if value.get("pull_request") is not None else None,
        last_error=value.get("last_error"),
    )


def _write_atomic_json(path: Path, payload: Any) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


__all__ = ["AUTOPILOT_STATE_DIR", "load_run_state", "run_state_path", "save_run_state"]

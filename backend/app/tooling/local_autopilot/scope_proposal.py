"""Scope expansion proposals for blocked task attempts."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .models import ScopeExpansionProposal

ROOT = Path(__file__).resolve().parents[4]
SCOPE_PROPOSAL_DIR = ROOT / ".specify" / "runtime" / "scope-proposals"
TASK_ID_PATTERN = re.compile(r"^T\d{3}[A-Z]?$")


def scope_proposal_path(task_id: str, root: Path | str = ROOT) -> Path:
    return Path(root) / ".specify" / "runtime" / "scope-proposals" / f"{_normalize_task_id(task_id)}.json"


def build_scope_expansion_proposal(
    *,
    proposal_id: str,
    run_id: str,
    task_id: str,
    epic_id: str,
    branch: str,
    head_sha: str,
    baseline_head_sha: str,
    current_allowlist: Sequence[str],
    files_touched: Sequence[str],
    unexpected_paths: Sequence[str],
    codex_summary: str,
    codex_notes: Sequence[str],
    created_at: str | None = None,
    status: str = "pending",
) -> ScopeExpansionProposal:
    return ScopeExpansionProposal(
        schema_version=1,
        proposal_id=_normalize_task_id_or_identifier(proposal_id),
        run_id=_normalize_task_id_or_identifier(run_id),
        task_id=_normalize_task_id(task_id),
        epic_id=_normalize_epic_id(epic_id),
        branch=_normalize_text(branch, field_name="branch"),
        head_sha=_normalize_sha(head_sha),
        baseline_head_sha=_normalize_sha(baseline_head_sha),
        current_allowlist=_normalize_paths(current_allowlist),
        files_touched=_normalize_paths(files_touched),
        unexpected_paths=_normalize_paths(unexpected_paths),
        codex_summary=_normalize_text(codex_summary, field_name="codex_summary", allow_blank=True),
        codex_notes=_normalize_note_lines(codex_notes),
        created_at=created_at or _timestamp(),
        status=status,
    )


def save_scope_expansion_proposal(record: ScopeExpansionProposal, root: Path | str = ROOT) -> Path:
    path = scope_proposal_path(record.task_id, root)
    _write_atomic_json(path, record.to_payload())
    return path


def load_scope_expansion_proposal(task_id: str, root: Path | str = ROOT) -> ScopeExpansionProposal | None:
    path = scope_proposal_path(task_id, root)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name}: scope proposal must be a JSON object")
    return ScopeExpansionProposal.from_payload(payload)


def set_scope_expansion_proposal_status(
    task_id: str,
    root: Path | str = ROOT,
    *,
    status: str,
) -> ScopeExpansionProposal | None:
    record = load_scope_expansion_proposal(task_id, root)
    if record is None:
        return None
    updated = ScopeExpansionProposal(
        schema_version=record.schema_version,
        proposal_id=record.proposal_id,
        run_id=record.run_id,
        task_id=record.task_id,
        epic_id=record.epic_id,
        branch=record.branch,
        head_sha=record.head_sha,
        baseline_head_sha=record.baseline_head_sha,
        current_allowlist=record.current_allowlist,
        files_touched=record.files_touched,
        unexpected_paths=record.unexpected_paths,
        codex_summary=record.codex_summary,
        codex_notes=record.codex_notes,
        created_at=record.created_at,
        status=status,
    )
    save_scope_expansion_proposal(updated, root=root)
    return updated


def reject_scope_expansion_proposal(task_id: str, root: Path | str = ROOT) -> ScopeExpansionProposal | None:
    return set_scope_expansion_proposal_status(task_id, root, status="rejected")


def supersede_scope_expansion_proposal(task_id: str, root: Path | str = ROOT) -> ScopeExpansionProposal | None:
    return set_scope_expansion_proposal_status(task_id, root, status="superseded")


def build_suggested_metadata_change(current_allowlist: Sequence[str], files_touched: Sequence[str]) -> str:
    deduped: list[str] = []
    for path in (*current_allowlist, *files_touched):
        normalized = _normalize_path(path)
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    if not deduped:
        return "Implementation files: none"
    return "Implementation files: " + ", ".join(f"`{path}`" for path in deduped)


def _normalize_task_id(task_id: str) -> str:
    return _normalize_task_id_or_identifier(task_id)


def _normalize_epic_id(epic_id: str) -> str:
    if not isinstance(epic_id, str) or not epic_id.strip():
        raise ValueError("epic_id must be a non-empty string")
    normalized = epic_id.strip()
    if not re.fullmatch(r"E\d{3}", normalized):
        raise ValueError("epic_id must match E###")
    return normalized


def _normalize_task_id_or_identifier(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("identifier must be a non-empty string")
    normalized = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", normalized):
        raise ValueError("identifier must be a safe filename-style string")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_blank: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized and not allow_blank:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def _normalize_sha(value: Any) -> str:
    normalized = _normalize_text(value, field_name="sha")
    if not re.fullmatch(r"[0-9a-f]{40}", normalized):
        raise ValueError("sha must be a 40-character lowercase hexadecimal SHA")
    return normalized


def _normalize_path(value: Any) -> str:
    normalized = str(value).replace("\\", "/").strip()
    return normalized


def _normalize_paths(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        path = _normalize_path(value)
        if path and path not in normalized:
            normalized.append(path)
    return tuple(normalized)


def _normalize_note_lines(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        note = _normalize_text(value, field_name="codex_notes[]")
        if note and note not in normalized:
            normalized.append(note)
    return tuple(normalized)


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
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
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "ROOT",
    "SCOPE_PROPOSAL_DIR",
    "build_scope_expansion_proposal",
    "build_suggested_metadata_change",
    "load_scope_expansion_proposal",
    "reject_scope_expansion_proposal",
    "save_scope_expansion_proposal",
    "scope_proposal_path",
    "set_scope_expansion_proposal_status",
    "supersede_scope_expansion_proposal",
]

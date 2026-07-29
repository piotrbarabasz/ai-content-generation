"""Validation receipts for skipping duplicate pre-push work."""

from __future__ import annotations

import json
import os
import re
import tempfile
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[4]
VALIDATION_RECEIPT_DIR = ROOT / ".specify" / "runtime" / "validation-receipts"
RECEIPT_SCHEMA_VERSION = 1
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PYTHON_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def validation_receipt_path(head_sha: str, root: Path | None = None) -> Path:
    normalized = _normalize_sha(head_sha)
    receipt_root = VALIDATION_RECEIPT_DIR if root is None else Path(root) / ".specify" / "runtime" / "validation-receipts"
    return receipt_root / f"{normalized}.json"


def build_validation_receipt(
    *,
    head_sha: str,
    branch: str,
    python_executable: str,
    python_version: str,
    status: str,
    checks: Sequence[Mapping[str, Any]],
    created_at: str | None = None,
) -> dict[str, Any]:
    created_at = created_at or _timestamp()
    normalized_checks = [_normalize_check(item) for item in checks]
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "head_sha": _normalize_sha(head_sha),
        "branch": _normalize_text(branch, field_name="branch"),
        "created_at": created_at,
        "python_executable": _normalize_text(python_executable, field_name="python_executable"),
        "python_version": _normalize_text(python_version, field_name="python_version"),
        "status": _normalize_text(status, field_name="status"),
        "checks": normalized_checks,
    }


def write_validation_receipt(
    *,
    head_sha: str,
    branch: str,
    python_executable: str,
    python_version: str,
    status: str,
    checks: Sequence[Mapping[str, Any]],
    root: Path | None = None,
    created_at: str | None = None,
) -> Path:
    receipt = build_validation_receipt(
        head_sha=head_sha,
        branch=branch,
        python_executable=python_executable,
        python_version=python_version,
        status=status,
        checks=checks,
        created_at=created_at,
    )
    path = validation_receipt_path(head_sha, root)
    _write_atomic_json(path, receipt)
    return path


def load_validation_receipt(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"validation receipt does not exist: {path}") from exc
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name}: validation receipt must be a JSON object")
    return loaded


def validate_receipt_for_head(
    path: Path,
    *,
    current_head_sha: str,
    current_branch: str,
    repo_clean: bool,
    current_python_version: tuple[int, int] | None = None,
) -> list[str]:
    errors: list[str] = []
    try:
        receipt = load_validation_receipt(path)
    except (FileNotFoundError, ValueError) as exc:
        return [str(exc)]

    if current_python_version is None:
        current_python_version = (sys.version_info.major, sys.version_info.minor)

    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {RECEIPT_SCHEMA_VERSION}")
    if receipt.get("status") != "PASS":
        errors.append("status must be PASS")
    if receipt.get("head_sha") != current_head_sha:
        errors.append("head_sha does not match the current HEAD")
    if receipt.get("branch") != current_branch:
        errors.append(f"branch must be {current_branch!r}")
    if not repo_clean:
        errors.append("working tree must be clean")

    python_version = receipt.get("python_version")
    if not isinstance(python_version, str) or not python_version.strip():
        errors.append("python_version must be a non-empty string")
    else:
        match = PYTHON_VERSION_PATTERN.fullmatch(python_version.strip())
        if match is None:
            errors.append("python_version must be in major.minor.micro format")
        else:
            major = int(match.group(1))
            minor = int(match.group(2))
            if (major, minor) != current_python_version:
                errors.append(
                    "python_version major.minor does not match the current interpreter"
                )

    checks = receipt.get("checks")
    if not isinstance(checks, list) or not checks:
        errors.append("checks must be a non-empty list")
        return errors

    has_pytest_full = False
    for index, item in enumerate(checks):
        if not isinstance(item, dict):
            errors.append(f"checks[{index}] must be a mapping")
            continue
        name = item.get("name")
        command = item.get("command")
        status = item.get("status")
        exit_code = item.get("exit_code")
        duration_ms = item.get("duration_ms")

        if not isinstance(name, str) or not name.strip():
            errors.append(f"checks[{index}].name must be a non-empty string")
        if not isinstance(command, list) or not command or not all(isinstance(part, str) and part.strip() for part in command):
            errors.append(f"checks[{index}].command must be a non-empty list of strings")
        if status != "PASS":
            errors.append(f"checks[{index}].status must be PASS")
        if not isinstance(exit_code, int):
            errors.append(f"checks[{index}].exit_code must be an integer")
        elif exit_code != 0:
            errors.append(f"checks[{index}].exit_code must be 0")
        if not isinstance(duration_ms, int) or duration_ms < 0:
            errors.append(f"checks[{index}].duration_ms must be a non-negative integer")
        if status in {"FAIL", "TIMEOUT"}:
            errors.append(f"checks[{index}].status must not be {status}")
        if name == "pytest_full" and status == "PASS" and exit_code == 0:
            has_pytest_full = True

    if not has_pytest_full:
        errors.append("checks must include pytest_full with status PASS and exit_code 0")
    return errors


def _normalize_sha(head_sha: str) -> str:
    if not isinstance(head_sha, str) or not SHA_PATTERN.fullmatch(head_sha.strip()):
        raise ValueError("head_sha must be a 40-character lowercase hex string")
    return head_sha.strip()


def _normalize_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _normalize_check(item: Mapping[str, Any]) -> dict[str, Any]:
    name = _normalize_text(item.get("name"), field_name="checks[].name")
    command = item.get("command")
    if not isinstance(command, Sequence) or isinstance(command, (str, bytes)):
        raise ValueError("checks[].command must be a sequence of strings")
    normalized_command = [str(part).strip() for part in command if str(part).strip()]
    if not normalized_command:
        raise ValueError("checks[].command must be a non-empty sequence of strings")
    status = _normalize_text(item.get("status"), field_name="checks[].status")
    if status not in {"PASS", "FAIL", "TIMEOUT", "SKIP"}:
        raise ValueError("checks[].status must be PASS, FAIL, TIMEOUT, or SKIP")
    exit_code = item.get("exit_code")
    if not isinstance(exit_code, int):
        raise ValueError("checks[].exit_code must be an integer")
    duration_ms = item.get("duration_ms")
    if not isinstance(duration_ms, int) or duration_ms < 0:
        raise ValueError("checks[].duration_ms must be a non-negative integer")
    return {
        "name": name,
        "command": normalized_command,
        "status": status,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
    }


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
    "RECEIPT_SCHEMA_VERSION",
    "ROOT",
    "VALIDATION_RECEIPT_DIR",
    "build_validation_receipt",
    "load_validation_receipt",
    "validate_receipt_for_head",
    "validation_receipt_path",
    "write_validation_receipt",
]

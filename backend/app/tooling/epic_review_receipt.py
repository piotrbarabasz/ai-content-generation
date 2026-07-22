"""Utilities for storing and validating active epic review receipts."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from .workstream_validation import _load_yaml_manifest as _load_workstream_manifest

ROOT = Path(__file__).resolve().parents[3]
ACTIVE_EPIC_FILE = ROOT / ".specify" / "runtime" / "active-epic"
WORKSTREAMS_DIR = ROOT / ".specify" / "workstreams"
RECEIPTS_DIR = ROOT / ".specify" / "runtime" / "reviews"
TIMEOUT_SECONDS = 20
RECEIPT_SCHEMA_VERSION = 1
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
EPIC_PATTERN = re.compile(r"^E\d{3}$")


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({"GIT_PAGER": "cat", "PAGER": "cat", "TERM": "dumb"})
    return env


def _run_git(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=ROOT,
        timeout=TIMEOUT_SECONDS,
        check=False,
        capture_output=True,
        text=True,
        env=_git_env(),
    )


def _run_git_rev_parse(ref: str) -> str:
    result = _run_git(["git", "rev-parse", ref])
    if result.returncode != 0:
        output = (result.stdout or "").strip() or (result.stderr or "").strip()
        raise RuntimeError(f"cannot resolve Git SHA for {ref!r}: {output or 'unknown error'}")
    sha = result.stdout.strip()
    if not SHA_PATTERN.fullmatch(sha):
        raise ValueError(f"git rev-parse {ref!r} returned invalid SHA {sha!r}")
    return sha


def _run_git_branch_show_current() -> str:
    result = _run_git(["git", "branch", "--show-current"])
    if result.returncode != 0:
        output = (result.stdout or "").strip() or (result.stderr or "").strip()
        raise RuntimeError(f"cannot read current Git branch: {output or 'unknown error'}")
    return result.stdout.strip()


def capture_review_shas(base_branch: str) -> tuple[str, str]:
    """Return the current HEAD SHA and the declared base SHA using separate Git commands."""
    head_sha = _run_git_rev_parse("HEAD")
    base_sha = _run_git_rev_parse(base_branch)
    return head_sha, base_sha


def review_receipt_path(epic_id: str, root: Path | None = None) -> Path:
    root = ROOT if root is None else root
    return root / ".specify" / "runtime" / "reviews" / f"{epic_id}.json"


def _normalize_required_checks(required_checks: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in required_checks or []:
        checks.append(dict(item))
    return checks


def build_review_receipt(
    *,
    epic_id: str,
    milestone_id: str,
    head_sha: str,
    base_sha: str,
    branch: str,
    base_branch: str,
    verdict: str,
    safe_to_create_pr: bool,
    required_checks: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Build the canonical receipt payload for an approved epic review."""
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "epic_id": epic_id,
        "milestone_id": milestone_id,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "branch": branch,
        "base_branch": base_branch,
        "verdict": verdict,
        "safe_to_create_pr": safe_to_create_pr,
        "required_checks": _normalize_required_checks(required_checks),
    }


def _validate_required_checks(required_checks: Any, expected_commands: Sequence[str] | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(required_checks, list) or not required_checks:
        errors.append("required_checks must be a non-empty list")
        return errors
    normalized_commands: list[str] = []
    for index, item in enumerate(required_checks):
        if not isinstance(item, dict):
            errors.append(f"required_checks[{index}] must be a mapping")
            continue
        command = item.get("command")
        exit_code = item.get("exit_code")
        if not isinstance(command, str) or not command.strip():
            errors.append(f"required_checks[{index}].command must be a non-empty string")
        else:
            normalized_commands.append(command)
        if not isinstance(exit_code, int):
            errors.append(f"required_checks[{index}].exit_code must be an integer")
        elif exit_code != 0:
            errors.append(f"required_checks[{index}].exit_code must be 0")
    if expected_commands is not None and list(expected_commands) != normalized_commands:
        errors.append("required_checks commands do not match the epic manifest")
    return errors


def _validate_epic_id(epic_id: str) -> str:
    epic_id = epic_id.strip()
    if not EPIC_PATTERN.fullmatch(epic_id):
        raise ValueError("epic must match E###")
    return epic_id


def _epic_argument(value: str) -> str:
    try:
        return _validate_epic_id(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _load_json_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"review JSON does not exist: {path}") from exc
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name}: review JSON must be a JSON object")
    return loaded


def _normalize_command_value(command: Any) -> str | None:
    if isinstance(command, str):
        normalized = command.strip()
        return normalized or None
    if isinstance(command, list) and all(isinstance(item, str) for item in command):
        normalized = " ".join(part.strip() for part in command if part.strip())
        return normalized or None
    return None


def _canonicalize_review_checks(raw_checks: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if not isinstance(raw_checks, list):
        return checks
    for item in raw_checks:
        if not isinstance(item, dict):
            continue
        command = _normalize_command_value(item.get("command"))
        exit_code = item.get("exit_code")
        if command is None or not isinstance(exit_code, int):
            continue
        checks.append({"command": command, "exit_code": exit_code})
    return checks


def _review_payload_from_json(path: Path) -> dict[str, Any]:
    loaded = _load_json_mapping(path)
    verdict = loaded.get("verdict", loaded.get("VERDICT"))
    safe_to_create_pr = loaded.get("safe_to_create_pr", loaded.get("SAFE_TO_CREATE_PR"))
    required_checks = loaded.get("required_checks", loaded.get("REQUIRED_CHECKS"))
    if required_checks is None:
        required_checks = loaded.get("checks", loaded.get("CHECKS"))
    return {
        "verdict": verdict,
        "safe_to_create_pr": safe_to_create_pr,
        "required_checks": _canonicalize_review_checks(required_checks),
    }


def _load_active_epic_id(runtime_file: Path | None = None) -> str:
    runtime_file = ACTIVE_EPIC_FILE if runtime_file is None else runtime_file
    try:
        active_epic = runtime_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"active epic file does not exist: {runtime_file}") from exc
    if not active_epic:
        raise ValueError("active epic file is empty")
    return _validate_epic_id(active_epic)


def _load_epic_manifest(epic_id: str, workstreams_dir: Path | None = None) -> tuple[Path, dict[str, Any]]:
    workstreams_dir = WORKSTREAMS_DIR if workstreams_dir is None else workstreams_dir
    if not workstreams_dir.is_dir():
        raise FileNotFoundError(f"workstreams directory does not exist: {workstreams_dir}")
    for path in sorted(workstreams_dir.glob("*.yml")):
        manifest = _load_workstream_manifest(path)
        if manifest.get("id") == epic_id:
            return path, manifest
    raise FileNotFoundError(f"epic manifest does not exist: {epic_id}")


def _load_milestone_manifest(milestone_id: str, workstreams_dir: Path | None = None) -> tuple[Path, dict[str, Any]]:
    workstreams_dir = WORKSTREAMS_DIR if workstreams_dir is None else workstreams_dir
    if not workstreams_dir.is_dir():
        raise FileNotFoundError(f"workstreams directory does not exist: {workstreams_dir}")
    for path in sorted(workstreams_dir.glob("*.yml")):
        manifest = _load_workstream_manifest(path)
        if manifest.get("id") == milestone_id:
            return path, manifest
    raise FileNotFoundError(f"milestone manifest does not exist: {milestone_id}")


def _required_check_commands(manifest: Mapping[str, Any]) -> list[str]:
    commands: list[str] = []
    for command in manifest.get("required_checks") or []:
        if isinstance(command, str) and command.strip():
            commands.append(command.strip())
    return commands


def _review_input_errors(
    *,
    review_payload: Mapping[str, Any],
    expected_commands: Sequence[str],
) -> list[str]:
    errors: list[str] = []
    if review_payload.get("verdict") != "PASS":
        errors.append("review verdict must be PASS")
    if review_payload.get("safe_to_create_pr") is not True:
        errors.append("safe_to_create_pr must be true")
    errors.extend(_validate_required_checks(review_payload.get("required_checks"), expected_commands))
    return errors


def _current_context(epic_id: str, workstreams_dir: Path | None = None) -> tuple[dict[str, Any], dict[str, Any], Path]:
    epic_path, epic_manifest = _load_epic_manifest(epic_id, workstreams_dir)
    milestone_id = epic_manifest.get("milestone")
    if not isinstance(milestone_id, str) or not milestone_id.strip():
        raise ValueError(f"{epic_path.name}: epic manifest missing milestone")
    milestone_path, milestone_manifest = _load_milestone_manifest(milestone_id, workstreams_dir)
    if milestone_manifest.get("id") != milestone_id:
        raise ValueError(f"{milestone_path.name}: milestone id does not match {milestone_id}")
    return epic_manifest, milestone_manifest, epic_path


def _json_result(status: str, action: str, **payload: Any) -> str:
    data = {"status": status, "action": action, **payload}
    return json.dumps(data, ensure_ascii=False, indent=2)


def _print_result(status: str, action: str, json_mode: bool, **payload: Any) -> None:
    if json_mode:
        print(_json_result(status, action, **payload))
        return
    if status == "PASS":
        for key, value in payload.items():
            print(f"{key.upper()}: {value}")
        print("STATUS: PASS")
        return
    print(f"STATUS: {status}")
    for key, value in payload.items():
        if key == "errors" and isinstance(value, list):
            for item in value:
                print(f"- {item}")
        else:
            print(f"{key.upper()}: {value}")


def _write_cli(epic_id: str, review_json: Path, json_mode: bool) -> int:
    try:
        epic_id = _validate_epic_id(epic_id)
        active_epic = _load_active_epic_id()
        epic_manifest, milestone_manifest, epic_path = _current_context(epic_id)
        branch = _run_git_branch_show_current()
        review_payload = _review_payload_from_json(review_json)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _print_result("FAIL", "write", json_mode, epic_id=epic_id, errors=[str(exc)])
        return 1

    if active_epic != epic_id:
        _print_result(
            "FAIL",
            "write",
            json_mode,
            epic_id=epic_id,
            errors=[f"active epic is {active_epic!r}, expected {epic_id!r}"],
        )
        return 1

    if branch != epic_manifest.get("branch"):
        _print_result(
            "FAIL",
            "write",
            json_mode,
            epic_id=epic_id,
            errors=[f"current branch is {branch!r}, expected {epic_manifest.get('branch')!r}"],
        )
        return 1

    if epic_manifest.get("milestone") != milestone_manifest.get("id"):
        _print_result(
            "FAIL",
            "write",
            json_mode,
            epic_id=epic_id,
            errors=[f"{epic_path.name}: epic milestone does not match milestone manifest"],
        )
        return 1

    expected_commands = _required_check_commands(epic_manifest)
    errors = _review_input_errors(review_payload=review_payload, expected_commands=expected_commands)
    if errors:
        _print_result("FAIL", "write", json_mode, epic_id=epic_id, errors=errors)
        return 1

    try:
        path = write_review_receipt(
            epic_id=epic_id,
            milestone_id=str(epic_manifest.get("milestone")),
            branch=str(epic_manifest.get("branch")),
            base_branch=str(epic_manifest.get("base_branch")),
            verdict="PASS",
            safe_to_create_pr=True,
            required_checks=review_payload["required_checks"],
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        _print_result("FAIL", "write", json_mode, epic_id=epic_id, errors=[str(exc)])
        return 1

    _print_result("PASS", "write", json_mode, epic_id=epic_id, receipt_path=str(path), milestone_id=str(epic_manifest.get("milestone")))
    return 0


def _validate_cli(epic_id: str, json_mode: bool) -> int:
    try:
        epic_id = _validate_epic_id(epic_id)
        epic_manifest, _, _ = _current_context(epic_id)
        branch = _run_git_branch_show_current()
        head_sha, base_sha = capture_review_shas(str(epic_manifest.get("base_branch")))
        receipt_path = review_receipt_path(epic_id)
        errors = validate_review_receipt_file(
            receipt_path,
            epic_id=epic_id,
            milestone_id=str(epic_manifest.get("milestone")),
            branch=str(epic_manifest.get("branch")),
            base_branch=str(epic_manifest.get("base_branch")),
            head_sha=head_sha,
            base_sha=base_sha,
            expected_required_commands=_required_check_commands(epic_manifest),
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _print_result("FAIL", "validate", json_mode, epic_id=epic_id, errors=[str(exc)])
        return 1

    if branch != epic_manifest.get("branch"):
        errors.append(f"current branch is {branch!r}, expected {epic_manifest.get('branch')!r}")
    if errors:
        _print_result("FAIL", "validate", json_mode, epic_id=epic_id, receipt_path=str(review_receipt_path(epic_id)), errors=errors)
        return 1

    _print_result(
        "PASS",
        "validate",
        json_mode,
        epic_id=epic_id,
        receipt_path=str(review_receipt_path(epic_id)),
        head_sha=head_sha,
        base_sha=base_sha,
    )
    return 0


def _delete_cli(epic_id: str, json_mode: bool) -> int:
    try:
        epic_id = _validate_epic_id(epic_id)
        path = review_receipt_path(epic_id)
        if not path.exists():
            raise FileNotFoundError(f"review receipt does not exist: {path}")
        deleted = delete_review_receipt(epic_id)
    except (FileNotFoundError, ValueError) as exc:
        _print_result("FAIL", "delete", json_mode, epic_id=epic_id, errors=[str(exc)])
        return 1

    if not deleted:
        _print_result("FAIL", "delete", json_mode, epic_id=epic_id, errors=[f"review receipt does not exist: {path}"])
        return 1

    _print_result("PASS", "delete", json_mode, epic_id=epic_id, receipt_path=str(path), deleted=True)
    return 0


def validate_review_receipt(
    receipt: Any,
    *,
    epic_id: str,
    milestone_id: str,
    branch: str,
    base_branch: str,
    head_sha: str,
    base_sha: str,
    expected_required_commands: Sequence[str] | None = None,
) -> list[str]:
    """Validate a parsed review receipt against the current epic context."""
    errors: list[str] = []
    if not isinstance(receipt, dict):
        return ["review receipt must be a mapping"]
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {RECEIPT_SCHEMA_VERSION}")
    if receipt.get("epic_id") != epic_id:
        errors.append(f"epic_id must be {epic_id!r}")
    if receipt.get("milestone_id") != milestone_id:
        errors.append(f"milestone_id must be {milestone_id!r}")
    if receipt.get("branch") != branch:
        errors.append(f"branch must be {branch!r}")
    if receipt.get("base_branch") != base_branch:
        errors.append(f"base_branch must be {base_branch!r}")
    if receipt.get("head_sha") != head_sha:
        errors.append("head_sha does not match the current HEAD")
    if receipt.get("base_sha") != base_sha:
        errors.append("base_sha does not match the current base branch")
    if receipt.get("verdict") != "PASS":
        errors.append("verdict must be PASS")
    if receipt.get("safe_to_create_pr") is not True:
        errors.append("safe_to_create_pr must be true")
    errors.extend(_validate_required_checks(receipt.get("required_checks"), expected_required_commands))
    return errors


def load_review_receipt(path: Path) -> dict[str, Any]:
    """Load a receipt from disk and raise a deterministic error for invalid JSON."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"review receipt does not exist: {path}") from exc
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name}: review receipt must be a JSON object")
    return loaded


def validate_review_receipt_file(
    path: Path,
    *,
    epic_id: str,
    milestone_id: str,
    branch: str,
    base_branch: str,
    head_sha: str,
    base_sha: str,
    expected_required_commands: Sequence[str] | None = None,
) -> list[str]:
    """Load and validate a receipt file against the current epic context."""
    try:
        receipt = load_review_receipt(path)
    except (FileNotFoundError, ValueError) as exc:
        return [str(exc)]
    return validate_review_receipt(
        receipt,
        epic_id=epic_id,
        milestone_id=milestone_id,
        branch=branch,
        base_branch=base_branch,
        head_sha=head_sha,
        base_sha=base_sha,
        expected_required_commands=expected_required_commands,
    )


def write_review_receipt(
    *,
    epic_id: str,
    milestone_id: str,
    branch: str,
    base_branch: str,
    verdict: str,
    safe_to_create_pr: bool,
    required_checks: Sequence[Mapping[str, Any]] | None,
    receipt_root: Path | None = None,
    head_sha: str | None = None,
    base_sha: str | None = None,
) -> Path:
    """Persist a PASS review receipt after verifying the review result and Git context."""
    if verdict != "PASS":
        raise ValueError("review receipt can only be written for a PASS verdict")
    if safe_to_create_pr is not True:
        raise ValueError("review receipt can only be written when safe_to_create_pr is true")
    check_errors = _validate_required_checks(required_checks)
    if check_errors:
        raise ValueError("; ".join(check_errors))
    if head_sha is None or base_sha is None:
        captured_head_sha, captured_base_sha = capture_review_shas(base_branch)
        head_sha = head_sha or captured_head_sha
        base_sha = base_sha or captured_base_sha
    receipt = build_review_receipt(
        epic_id=epic_id,
        milestone_id=milestone_id,
        head_sha=head_sha,
        base_sha=base_sha,
        branch=branch,
        base_branch=base_branch,
        verdict=verdict,
        safe_to_create_pr=safe_to_create_pr,
        required_checks=required_checks,
    )
    path = review_receipt_path(epic_id, receipt_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def delete_review_receipt(epic_id: str, receipt_root: Path | None = None) -> bool:
    """Delete a receipt for the selected epic if it exists."""
    path = review_receipt_path(epic_id, receipt_root)
    if not path.exists():
        return False
    path.unlink()
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backend.app.tooling.epic_review_receipt")
    subparsers = parser.add_subparsers(dest="command", required=True)

    write_parser = subparsers.add_parser("write", help="write a validated review receipt")
    write_parser.add_argument("--epic", required=True, type=_epic_argument)
    write_parser.add_argument("--review-json", required=True, type=Path)
    write_parser.add_argument("--json", action="store_true")

    validate_parser = subparsers.add_parser("validate", help="validate an existing review receipt")
    validate_parser.add_argument("--epic", required=True, type=_epic_argument)
    validate_parser.add_argument("--json", action="store_true")

    delete_parser = subparsers.add_parser("delete", help="delete a review receipt")
    delete_parser.add_argument("--epic", required=True, type=_epic_argument)
    delete_parser.add_argument("--json", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code)

    if args.command == "write":
        return _write_cli(args.epic, args.review_json, args.json)
    if args.command == "validate":
        return _validate_cli(args.epic, args.json)
    if args.command == "delete":
        return _delete_cli(args.epic, args.json)
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())

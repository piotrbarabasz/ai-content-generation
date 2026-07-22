from __future__ import annotations

import json
from pathlib import Path

from app.tooling import epic_review_receipt as receipt


def _write_manifest(directory: Path, name: str, text: str) -> None:
    directory.joinpath(name).write_text(text, encoding="utf-8")


def _setup_context(
    tmp_path: Path,
    *,
    epic_id: str = "E001",
    milestone_id: str = "M001",
    branch: str = "epic/E001",
    base_branch: str = "master",
    required_checks: list[str] | None = None,
) -> tuple[Path, Path, Path]:
    if required_checks is None:
        required_checks = ["python -m pytest"]
    workstreams = tmp_path / ".specify" / "workstreams"
    runtime = tmp_path / ".specify" / "runtime"
    receipts = runtime / "reviews"
    workstreams.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    _write_manifest(
        workstreams,
        f"{milestone_id}.yml",
        "\n".join(
            [
                f"id: {milestone_id}",
                f"title: Milestone {milestone_id}",
                "status: active",
                "goal: goal",
                "epics:",
                f"  - {epic_id}",
                "completion_criteria:",
                "  - Tests pass",
                "",
            ]
        ),
    )
    _write_manifest(
        workstreams,
        f"{epic_id}.yml",
        "\n".join(
            [
                f"id: {epic_id}",
                f"title: Epic {epic_id}",
                f"milestone: {milestone_id}",
                "feature: specs/001-ai-content-studio",
                f"base_branch: {base_branch}",
                f"branch: {branch}",
                "status: active",
                "risk: low",
                "depends_on: []",
                "tasks:",
                "  - T001",
                "required_checks:",
                *[f"  - {command}" for command in required_checks],
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
    (runtime / "active-epic").write_text(f"{epic_id}\n", encoding="utf-8")
    return workstreams, runtime, receipts


def _review_payload(*, verdict: str = "PASS", safe_to_create_pr: bool = True, exit_code: int = 0, commands: list[str] | None = None) -> dict:
    if commands is None:
        commands = ["python -m pytest"]
    return {
        "verdict": verdict,
        "safe_to_create_pr": safe_to_create_pr,
        "required_checks": [{"command": command, "exit_code": exit_code} for command in commands],
    }


def _patch_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(receipt, "ROOT", tmp_path)
    monkeypatch.setattr(receipt, "ACTIVE_EPIC_FILE", tmp_path / ".specify" / "runtime" / "active-epic")
    monkeypatch.setattr(receipt, "WORKSTREAMS_DIR", tmp_path / ".specify" / "workstreams")
    monkeypatch.setattr(receipt, "RECEIPTS_DIR", tmp_path / ".specify" / "runtime" / "reviews")


def test_write_pass_creates_receipt_and_json_output(tmp_path, monkeypatch, capsys):
    workstreams, runtime, receipts = _setup_context(tmp_path)
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(_review_payload()), encoding="utf-8")
    _patch_context(monkeypatch, tmp_path)
    monkeypatch.setattr(receipt, "_run_git_branch_show_current", lambda: "epic/E001")
    monkeypatch.setattr(receipt, "capture_review_shas", lambda base_branch: ("a" * 40, "b" * 40))

    exit_code = receipt.main(["write", "--epic", "E001", "--review-json", str(review_path), "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "PASS"
    assert output["action"] == "write"
    assert output["epic_id"] == "E001"
    assert output["receipt_path"] == str(receipts / "E001.json")
    assert json.loads((receipts / "E001.json").read_text(encoding="utf-8"))["head_sha"] == "a" * 40


def test_write_fail_rejects_non_pass_review(tmp_path, monkeypatch):
    _setup_context(tmp_path)
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(_review_payload(verdict="FAIL", safe_to_create_pr=False)), encoding="utf-8")
    _patch_context(monkeypatch, tmp_path)
    monkeypatch.setattr(receipt, "_run_git_branch_show_current", lambda: "epic/E001")

    exit_code = receipt.main(["write", "--epic", "E001", "--review-json", str(review_path)])

    assert exit_code == 1
    assert not receipt.review_receipt_path("E001").exists()


def test_write_rejects_incomplete_checks(tmp_path, monkeypatch):
    _setup_context(tmp_path, required_checks=["python -m pytest", "git --no-pager diff --check"])
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(_review_payload(commands=["python -m pytest"])), encoding="utf-8")
    _patch_context(monkeypatch, tmp_path)
    monkeypatch.setattr(receipt, "_run_git_branch_show_current", lambda: "epic/E001")

    exit_code = receipt.main(["write", "--epic", "E001", "--review-json", str(review_path)])

    assert exit_code == 1


def test_validate_reports_stale_head(tmp_path, monkeypatch):
    _setup_context(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    monkeypatch.setattr(receipt, "_run_git_branch_show_current", lambda: "epic/E001")
    monkeypatch.setattr(receipt, "capture_review_shas", lambda base_branch: ("c" * 40, "b" * 40))
    monkeypatch.setattr(receipt, "review_receipt_path", lambda epic_id, root=None: tmp_path / ".specify" / "runtime" / "reviews" / f"{epic_id}.json")
    receipt.write_review_receipt(
        epic_id="E001",
        milestone_id="M001",
        branch="epic/E001",
        base_branch="master",
        verdict="PASS",
        safe_to_create_pr=True,
        required_checks=[{"command": "python -m pytest", "exit_code": 0}],
        receipt_root=tmp_path,
        head_sha="c" * 40,
        base_sha="b" * 40,
    )
    monkeypatch.setattr(receipt, "capture_review_shas", lambda base_branch: ("d" * 40, "b" * 40))

    exit_code = receipt.main(["validate", "--epic", "E001"])

    assert exit_code == 1


def test_validate_reports_stale_base(tmp_path, monkeypatch):
    _setup_context(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    monkeypatch.setattr(receipt, "_run_git_branch_show_current", lambda: "epic/E001")
    monkeypatch.setattr(receipt, "capture_review_shas", lambda base_branch: ("a" * 40, "b" * 40))
    receipt.write_review_receipt(
        epic_id="E001",
        milestone_id="M001",
        branch="epic/E001",
        base_branch="master",
        verdict="PASS",
        safe_to_create_pr=True,
        required_checks=[{"command": "python -m pytest", "exit_code": 0}],
        receipt_root=tmp_path,
        head_sha="a" * 40,
        base_sha="b" * 40,
    )
    monkeypatch.setattr(receipt, "capture_review_shas", lambda base_branch: ("a" * 40, "d" * 40))

    exit_code = receipt.main(["validate", "--epic", "E001"])

    assert exit_code == 1


def test_validate_json_output_and_pass(tmp_path, monkeypatch, capsys):
    _setup_context(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    monkeypatch.setattr(receipt, "_run_git_branch_show_current", lambda: "epic/E001")
    monkeypatch.setattr(receipt, "capture_review_shas", lambda base_branch: ("a" * 40, "b" * 40))
    receipt.write_review_receipt(
        epic_id="E001",
        milestone_id="M001",
        branch="epic/E001",
        base_branch="master",
        verdict="PASS",
        safe_to_create_pr=True,
        required_checks=[{"command": "python -m pytest", "exit_code": 0}],
        receipt_root=tmp_path,
        head_sha="a" * 40,
        base_sha="b" * 40,
    )

    exit_code = receipt.main(["validate", "--epic", "E001", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "PASS"
    assert output["action"] == "validate"
    assert output["epic_id"] == "E001"


def test_delete_only_selected_receipt(tmp_path, monkeypatch):
    _setup_context(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    selected = receipt.review_receipt_path("E001", tmp_path)
    other = receipt.review_receipt_path("E002", tmp_path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text("{}", encoding="utf-8")
    other.write_text("{}", encoding="utf-8")

    exit_code = receipt.main(["delete", "--epic", "E001"])

    assert exit_code == 0
    assert not selected.exists()
    assert other.exists()


def test_delete_missing_receipt_returns_validation_failure(tmp_path, monkeypatch):
    _setup_context(tmp_path)
    _patch_context(monkeypatch, tmp_path)

    exit_code = receipt.main(["delete", "--epic", "E001"])

    assert exit_code == 1


def test_invalid_epic_id_returns_usage_error(tmp_path, monkeypatch):
    _setup_context(tmp_path)
    _patch_context(monkeypatch, tmp_path)

    exit_code = receipt.main(["write", "--epic", "BAD", "--review-json", str(tmp_path / "review.json")])

    assert exit_code == 2


def test_missing_review_json_returns_validation_failure(tmp_path, monkeypatch):
    _setup_context(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    monkeypatch.setattr(receipt, "_run_git_branch_show_current", lambda: "epic/E001")

    exit_code = receipt.main(["write", "--epic", "E001", "--review-json", str(tmp_path / "missing.json")])

    assert exit_code == 1


def test_invalid_review_json_returns_validation_failure(tmp_path, monkeypatch):
    _setup_context(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    monkeypatch.setattr(receipt, "_run_git_branch_show_current", lambda: "epic/E001")
    review_path = tmp_path / "invalid.json"
    review_path.write_text("{not json", encoding="utf-8")

    exit_code = receipt.main(["write", "--epic", "E001", "--review-json", str(review_path)])

    assert exit_code == 1


def test_help_returns_success():
    assert receipt.main(["--help"]) == 0

from __future__ import annotations

import json
from pathlib import Path

from app.tooling import epic_close_evidence as evidence


class Result:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


def _write_manifest(directory: Path, name: str, text: str) -> None:
    directory.joinpath(name).write_text(text, encoding="utf-8")


def _prepare_workspace(
    tmp_path: Path,
    *,
    epic_id: str = "E001",
    milestone_id: str = "M001",
    branch: str = "epic/E001",
    base_branch: str = "master",
    current_branch: str = "epic/E001",
    head_sha: str = "a" * 40,
    base_sha: str = "b" * 40,
) -> Path:
    workstreams = tmp_path / ".specify" / "workstreams"
    workstreams.mkdir(parents=True, exist_ok=True)
    _write_manifest(
        workstreams,
        f"{epic_id}.yml",
        "\n".join(
            [
                f"id: {epic_id}",
                "title: Epic",
                f"milestone: {milestone_id}",
                "feature: specs/001-ai-content-studio",
                f"base_branch: {base_branch}",
                f"branch: {branch}",
                "status: completed",
                "risk: low",
                "depends_on: []",
                "tasks:",
                "  - T001",
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
    _write_manifest(
        workstreams,
        f"{milestone_id}.yml",
        "\n".join(
            [
                f"id: {milestone_id}",
                "title: Milestone",
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
    monkeypatched_runtime = tmp_path / ".specify" / "runtime"
    monkeypatched_runtime.mkdir(parents=True, exist_ok=True)
    (monkeypatched_runtime / "active-epic").write_text(f"{epic_id}\n", encoding="utf-8")
    return workstreams


def _patch_context(monkeypatch, tmp_path: Path, current_branch: str = "epic/E001", head_sha: str = "a" * 40, base_sha: str = "b" * 40) -> None:
    monkeypatch.setattr(evidence, "ROOT", tmp_path)
    monkeypatch.setattr(evidence, "WORKSTREAMS_DIR", tmp_path / ".specify" / "workstreams")

    def fake_run(command, git_runner=None):
        if command == ["git", "rev-parse", "HEAD"]:
            return Result(stdout=f"{head_sha}\n")
        if command == ["git", "rev-parse", "master"]:
            return Result(stdout=f"{base_sha}\n")
        if command == ["git", "branch", "--show-current"]:
            return Result(stdout=f"{current_branch}\n")
        if command[:3] == ["git", "merge-base", "--is-ancestor"]:
            return Result(returncode=0)
        if command[:4] == ["git", "log", "--oneline", "--first-parent"]:
            return Result(stdout=f"{head_sha} commit\n")
        return Result()

    monkeypatch.setattr(evidence, "_run_git", fake_run)


def _write_pr_metadata(path: Path, **overrides) -> Path:
    payload = {
        "state": "merged",
        "merged": True,
        "headRefName": "epic/E001",
        "baseRefName": "master",
        "mergedAt": "2026-07-22T10:00:00Z",
        "mergeCommit": {"oid": "c" * 40},
        "headRefOid": "a" * 40,
        "baseRefOid": "b" * 40,
    }
    payload.update(overrides)
    pr_path = path / "pr.json"
    pr_path.write_text(json.dumps(payload), encoding="utf-8")
    return pr_path


def test_merge_commit_ancestry_reports_local_support():
    calls = []

    def fake_runner(command, **kwargs):
        calls.append(command)
        if command[1] == "merge-base":
            return Result(returncode=0)
        return Result(stdout="c1\nc2\n")

    result = evidence.evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr=None,
        github_integration_available=False,
        git_runner=fake_runner,
    )
    assert result.valid is True
    assert result.strategy == "local_ancestry"
    assert result.squash_supported is False
    assert result.rebase_supported is False
    assert result.details["history_kind"] == "merge_commit"
    assert calls == [
        ["git", "merge-base", "--is-ancestor", "a" * 40, "master"],
        ["git", "log", "--oneline", "--first-parent", "master"],
    ]


def test_fast_forward_ancestry_reports_local_support():
    def fake_runner(command, **kwargs):
        if command[1] == "merge-base":
            return Result(returncode=0)
        return Result(stdout=f"{'a' * 40}\ncommit\n")

    result = evidence.evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr=None,
        github_integration_available=False,
        git_runner=fake_runner,
    )
    assert result.valid is True
    assert result.details["history_kind"] == "fast_forward"


def test_squash_via_github_metadata_is_valid():
    result = evidence.evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr={
            "state": "merged",
            "merged": True,
            "headRefName": "epic/E001",
            "baseRefName": "master",
            "mergedAt": "2026-07-22T12:00:00Z",
            "mergeCommit": None,
            "headRefOid": "a" * 40,
            "baseRefOid": "b" * 40,
        },
        github_integration_available=True,
    )
    assert result.valid is True
    assert result.strategy == "github_pr_metadata"
    assert result.squash_supported is True
    assert result.rebase_supported is True


def test_rebase_via_github_metadata_is_valid():
    result = evidence.evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr={
            "state": "merged",
            "merged": True,
            "headRefName": "epic/E001",
            "baseRefName": "master",
            "mergedAt": "2026-07-22T12:00:00Z",
            "mergeCommit": {"oid": "c" * 40},
            "headRefOid": "a" * 40,
            "baseRefOid": "b" * 40,
        },
        github_integration_available=True,
    )
    assert result.valid is True
    assert result.strategy == "github_pr_metadata"
    assert result.rebase_supported is True


def test_rebase_without_metadata_is_not_supported():
    result = evidence.evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr=None,
        github_integration_available=False,
        git_runner=lambda *args, **kwargs: Result(returncode=0, stdout="commit\n"),
    )
    assert result.valid is True
    assert result.rebase_supported is False
    assert result.squash_supported is False


def test_closed_not_merged_is_rejected():
    result = evidence.evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr={"state": "closed", "merged": False, "headRefName": "epic/E001", "baseRefName": "master"},
        github_integration_available=True,
    )
    assert result.valid is False
    assert any("closed PR metadata is not merge evidence" in reason for reason in result.reasons)


def test_wrong_branch_is_rejected_by_cli(tmp_path, monkeypatch):
    _prepare_workspace(tmp_path, current_branch="epic/other")
    _patch_context(monkeypatch, tmp_path, current_branch="epic/other")

    exit_code = evidence.main(["--epic", "E001", "--json"])

    assert exit_code == 1


def test_wrong_base_is_rejected_by_cli(tmp_path, monkeypatch):
    _prepare_workspace(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    pr_path = _write_pr_metadata(
        tmp_path,
        baseRefName="develop",
    )

    exit_code = evidence.main(["--epic", "E001", "--pr-metadata-json", str(pr_path), "--json"])

    assert exit_code == 1


def test_missing_metadata_is_rejected_when_requested():
    result = evidence.evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr=None,
        github_integration_available=True,
    )
    assert result.valid is False
    assert result.strategy == "github_pr_metadata"
    assert any("unavailable" in reason for reason in result.reasons)


def test_json_output_reports_fields(tmp_path, monkeypatch, capsys):
    _prepare_workspace(tmp_path)
    _patch_context(monkeypatch, tmp_path)
    pr_path = _write_pr_metadata(tmp_path)

    exit_code = evidence.main(["--epic", "E001", "--pr-metadata-json", str(pr_path), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["strategy"] == "github_pr_metadata"
    assert payload["squash_supported"] is True
    assert payload["rebase_supported"] is True
    assert payload["details"]["epic_id"] == "E001"


def test_invalid_epic_id_returns_usage_error():
    assert evidence.main(["--epic", "bad", "--json"]) == 2


def test_missing_metadata_file_returns_validation_failure(tmp_path, monkeypatch):
    _prepare_workspace(tmp_path)
    _patch_context(monkeypatch, tmp_path)

    exit_code = evidence.main(["--epic", "E001", "--pr-metadata-json", str(tmp_path / "missing.json"), "--json"])

    assert exit_code == 1

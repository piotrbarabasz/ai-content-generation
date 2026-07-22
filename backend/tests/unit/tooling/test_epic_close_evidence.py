from app.tooling.epic_close_evidence import evaluate_merge_evidence


def _github_pr(**overrides):
    payload = {
        "state": "merged",
        "merged": True,
        "headRefName": "epic/E001",
        "baseRefName": "master",
        "mergedAt": "2026-07-22T10:00:00Z",
        "mergeCommit": {"oid": "c" * 40},
    }
    payload.update(overrides)
    return payload


def test_merged_merge_commit_uses_github_metadata():
    result = evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr=_github_pr(),
        github_integration_available=True,
    )
    assert result.valid is True
    assert result.strategy == "github_pr_metadata"
    assert result.squash_supported is True
    assert result.rebase_supported is True
    assert result.local_fallback is False


def test_merged_rebase_uses_github_metadata():
    result = evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr=_github_pr(mergeCommit="c" * 40, mergedAt="2026-07-22T11:00:00Z"),
        github_integration_available=True,
    )
    assert result.valid is True
    assert result.strategy == "github_pr_metadata"
    assert result.squash_supported is True
    assert result.rebase_supported is True


def test_merged_squash_uses_github_metadata_even_if_local_history_is_unavailable():
    result = evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr=_github_pr(mergeCommit=None, mergedAt="2026-07-22T12:00:00Z"),
        github_integration_available=True,
        git_runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local fallback must not run")),
    )
    assert result.valid is True
    assert result.strategy == "github_pr_metadata"
    assert result.squash_supported is True
    assert result.rebase_supported is True
    assert result.local_fallback is False


def test_closed_not_merged_is_rejected():
    result = evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr=_github_pr(state="closed", merged=False),
        github_integration_available=True,
    )
    assert result.valid is False
    assert any("closed PR metadata is not merge evidence" in reason for reason in result.reasons)


def test_wrong_head_is_rejected():
    result = evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr=_github_pr(headRefName="epic/other"),
        github_integration_available=True,
    )
    assert result.valid is False
    assert any("head branch must be 'epic/E001'" in reason for reason in result.reasons)


def test_wrong_base_is_rejected():
    result = evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr=_github_pr(baseRefName="develop"),
        github_integration_available=True,
    )
    assert result.valid is False
    assert any("base branch must be 'master'" in reason for reason in result.reasons)


def test_missing_github_metadata_blocks_when_integration_available():
    result = evaluate_merge_evidence(
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


def test_local_ancestry_pass_uses_fallback():
    calls = []

    class Result:
        def __init__(self, returncode=0, stdout="history\n"):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == "merge-base":
            return Result(returncode=0)
        return Result(returncode=0, stdout="commit\n")

    result = evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr=None,
        github_integration_available=False,
        git_runner=fake_run,
    )
    assert result.valid is True
    assert result.strategy == "local_ancestry"
    assert result.squash_supported is False
    assert result.rebase_supported is False
    assert result.local_fallback is True
    assert result.details["history_kind"] == "merge_commit"
    assert calls == [
        ["git", "merge-base", "--is-ancestor", "a" * 40, "master"],
        ["git", "log", "--oneline", "--first-parent", "master"],
    ]


def test_local_ancestry_fail_rejects_squash_like_history():
    class Result:
        def __init__(self, returncode=1, stdout=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(command, **kwargs):
        if command[1] == "merge-base":
            return Result(returncode=1)
        return Result(returncode=0, stdout="commit\n")

    result = evaluate_merge_evidence(
        epic_id="E001",
        epic_branch="epic/E001",
        base_branch="master",
        epic_head_sha="a" * 40,
        base_sha="b" * 40,
        github_pr=None,
        github_integration_available=False,
        git_runner=fake_run,
    )
    assert result.valid is False
    assert result.strategy == "local_ancestry"
    assert result.squash_supported is False
    assert result.rebase_supported is False
    assert any("squash merges cannot be proven" in reason for reason in result.reasons)

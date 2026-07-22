import json

from app.tooling import epic_review_receipt as receipt


def _receipt(**overrides):
    data = {
        "schema_version": 1,
        "epic_id": "E001",
        "milestone_id": "M001",
        "head_sha": "a" * 40,
        "base_sha": "b" * 40,
        "branch": "epic/E001",
        "base_branch": "master",
        "verdict": "PASS",
        "safe_to_create_pr": True,
        "required_checks": [{"command": "python -m pytest", "exit_code": 0}],
    }
    data.update(overrides)
    return data


def test_capture_review_shas_uses_separate_git_commands(monkeypatch):
    calls = []

    class Result:
        def __init__(self, stdout):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[-1] == "HEAD":
            return Result("a" * 40 + "\n")
        return Result("b" * 40 + "\n")

    monkeypatch.setattr(receipt.subprocess, "run", fake_run)

    head_sha, base_sha = receipt.capture_review_shas("master")

    assert head_sha == "a" * 40
    assert base_sha == "b" * 40
    assert [command for command, _ in calls] == [["git", "rev-parse", "HEAD"], ["git", "rev-parse", "master"]]
    for _, kwargs in calls:
        assert kwargs["timeout"] == 20
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["env"]["GIT_PAGER"] == "cat"
        assert kwargs["env"]["PAGER"] == "cat"
        assert kwargs["env"]["TERM"] == "dumb"


def test_write_and_validate_review_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt, "capture_review_shas", lambda base_branch: ("a" * 40, "b" * 40))

    path = receipt.write_review_receipt(
        epic_id="E001",
        milestone_id="M001",
        branch="epic/E001",
        base_branch="master",
        verdict="PASS",
        safe_to_create_pr=True,
        required_checks=[{"command": "python -m pytest", "exit_code": 0}],
        receipt_root=tmp_path,
    )

    assert path == tmp_path / ".specify" / "runtime" / "reviews" / "E001.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == _receipt()

    errors = receipt.validate_review_receipt_file(
        path,
        epic_id="E001",
        milestone_id="M001",
        branch="epic/E001",
        base_branch="master",
        head_sha="a" * 40,
        base_sha="b" * 40,
        expected_required_commands=["python -m pytest"],
    )
    assert errors == []


def test_missing_receipt_is_reported(tmp_path):
    errors = receipt.validate_review_receipt_file(
        tmp_path / "missing.json",
        epic_id="E001",
        milestone_id="M001",
        branch="epic/E001",
        base_branch="master",
        head_sha="a" * 40,
        base_sha="b" * 40,
    )
    assert any("review receipt does not exist" in error for error in errors)


def test_stale_head_and_base_are_reported():
    errors = receipt.validate_review_receipt(
        _receipt(),
        epic_id="E001",
        milestone_id="M001",
        branch="epic/E001",
        base_branch="master",
        head_sha="c" * 40,
        base_sha="d" * 40,
        expected_required_commands=["python -m pytest"],
    )
    assert any("head_sha does not match" in error for error in errors)
    assert any("base_sha does not match" in error for error in errors)


def test_wrong_epic_and_branch_are_reported():
    errors = receipt.validate_review_receipt(
        _receipt(epic_id="E999", branch="epic/E999"),
        epic_id="E001",
        milestone_id="M001",
        branch="epic/E001",
        base_branch="master",
        head_sha="a" * 40,
        base_sha="b" * 40,
        expected_required_commands=["python -m pytest"],
    )
    assert any("epic_id must be 'E001'" in error for error in errors)
    assert any("branch must be 'epic/E001'" in error for error in errors)


def test_fail_and_safe_flag_are_reported():
    errors = receipt.validate_review_receipt(
        _receipt(verdict="FAIL", safe_to_create_pr=False),
        epic_id="E001",
        milestone_id="M001",
        branch="epic/E001",
        base_branch="master",
        head_sha="a" * 40,
        base_sha="b" * 40,
        expected_required_commands=["python -m pytest"],
    )
    assert any("verdict must be PASS" in error for error in errors)
    assert any("safe_to_create_pr must be true" in error for error in errors)


def test_missing_required_check_is_reported():
    errors = receipt.validate_review_receipt(
        _receipt(required_checks=[]),
        epic_id="E001",
        milestone_id="M001",
        branch="epic/E001",
        base_branch="master",
        head_sha="a" * 40,
        base_sha="b" * 40,
        expected_required_commands=["python -m pytest"],
    )
    assert any("required_checks must be a non-empty list" in error for error in errors)


def test_invalid_json_is_reported(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text("{not json", encoding="utf-8")

    try:
        receipt.load_review_receipt(path)
    except ValueError as exc:
        assert "invalid JSON" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for invalid JSON")


def test_delete_review_receipt_only_removes_selected_epic(tmp_path):
    selected = receipt.review_receipt_path("E001", tmp_path)
    other = receipt.review_receipt_path("E002", tmp_path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text("{}", encoding="utf-8")
    other.write_text("{}", encoding="utf-8")

    assert receipt.delete_review_receipt("E001", tmp_path) is True
    assert not selected.exists()
    assert other.exists()


def test_write_review_receipt_rejects_failed_required_checks(tmp_path):
    try:
        receipt.write_review_receipt(
            epic_id="E001",
            milestone_id="M001",
            branch="epic/E001",
            base_branch="master",
            verdict="PASS",
            safe_to_create_pr=True,
            required_checks=[{"command": "python -m pytest", "exit_code": 1}],
            receipt_root=tmp_path,
            head_sha="a" * 40,
            base_sha="b" * 40,
        )
    except ValueError as exc:
        assert "required_checks[0].exit_code must be 0" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for failed required checks")

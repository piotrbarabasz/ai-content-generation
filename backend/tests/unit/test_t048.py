from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "narrations"
METADATA_PATH = FIXTURE_DIR / "metadata.json"
POLISH_DIACRITICS = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"
PUNCTUATION_MARKS = ".,;:!?—„”\""
WORD_RE = re.compile(r"\b\w+(?:[-']\w+)*\b", re.UNICODE)
ABBREVIATION_RE = re.compile(r"\b[A-ZĄĆĘŁŃÓŚŹŻ]{2,}\b")


def _load_metadata() -> dict[str, object]:
    try:
        return json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:  # pragma: no cover - exercised by fixture corruption
        pytest.fail(f"metadata.json: encoding - not valid UTF-8 ({exc})")


def _read_fixture_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - exercised by fixture corruption
        pytest.fail(f"{path.name}: encoding - not valid UTF-8 ({exc})")


def _word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


FIXTURE_METADATA = _load_metadata()
FIXTURE_ENTRIES = list(FIXTURE_METADATA["fixtures"])


def test_narration_fixture_discovery_matches_metadata() -> None:
    discovered = sorted(FIXTURE_DIR.glob("story_*.txt"))
    expected = [FIXTURE_DIR / entry["file"] for entry in FIXTURE_ENTRIES]

    assert len(discovered) == 4, (
        f"narrations: expected 4 UTF-8 story fixtures, found {len(discovered)} "
        f"({[path.name for path in discovered]})"
    )
    assert [path.name for path in discovered] == [path.name for path in expected], (
        "narrations: discovered files do not match metadata entries; "
        f"discovered={[path.name for path in discovered]} "
        f"metadata={[path.name for path in expected]}"
    )


def test_narration_metadata_has_expected_shape() -> None:
    assert METADATA_PATH.is_file(), f"metadata.json: missing file at {METADATA_PATH}"
    assert FIXTURE_METADATA["schema_version"] == "1.0", (
        f"metadata.json: schema_version expected 1.0, got {FIXTURE_METADATA['schema_version']!r}"
    )
    assert FIXTURE_METADATA["language"] == "pl", (
        f"metadata.json: language expected 'pl', got {FIXTURE_METADATA['language']!r}"
    )
    assert FIXTURE_METADATA["encoding"] == "utf-8", (
        f"metadata.json: encoding expected 'utf-8', got {FIXTURE_METADATA['encoding']!r}"
    )
    assert "Unicode word tokens" in FIXTURE_METADATA["word_count_rule"], (
        "metadata.json: word_count_rule should document the word-token rule"
    )
    assert len(FIXTURE_ENTRIES) == 4, (
        f"metadata.json: expected 4 fixture entries, found {len(FIXTURE_ENTRIES)}"
    )


@pytest.mark.parametrize("entry", FIXTURE_ENTRIES, ids=lambda entry: entry["file"])
def test_narration_metadata_entries_point_to_valid_utf8_fixtures(entry: dict[str, object]) -> None:
    file_name = str(entry["file"])
    path = FIXTURE_DIR / file_name
    text = _read_fixture_text(path)
    payload = path.read_bytes()

    for field in (
        "title",
        "language",
        "target_duration_minutes",
        "actual_word_count",
        "expected_word_count_range",
        "feature_tags",
        "sha256",
    ):
        assert field in entry, f"{file_name}: metadata field {field} is missing"

    assert str(entry["title"]).strip(), f"{file_name}: title must not be blank"
    assert entry["language"] == "pl", f"{file_name}: language expected 'pl', got {entry['language']!r}"
    assert int(entry["target_duration_minutes"]) in {1, 5, 8, 15}, (
        f"{file_name}: target_duration_minutes must be one of 1, 5, 8 or 15, "
        f"got {entry['target_duration_minutes']!r}"
    )

    expected_range = entry["expected_word_count_range"]
    assert isinstance(expected_range, dict), f"{file_name}: expected_word_count_range must be an object"
    assert set(expected_range) == {"min", "max"}, (
        f"{file_name}: expected_word_count_range must define min and max, got {sorted(expected_range)}"
    )
    assert isinstance(entry["feature_tags"], list) and entry["feature_tags"], (
        f"{file_name}: feature_tags must be a non-empty list"
    )
    assert any(str(tag).startswith("abbrev") for tag in entry["feature_tags"]), (
        f"{file_name}: feature_tags must include at least one abbreviation-related tag, "
        f"got {entry['feature_tags']!r}"
    )

    actual_sha256 = hashlib.sha256(payload).hexdigest()
    assert actual_sha256 == entry["sha256"], (
        f"{file_name}: sha256 expected {entry['sha256']!r}, got {actual_sha256!r}"
    )

    expected_word_count = int(entry["actual_word_count"])
    recalculated_word_count = _word_count(text)
    assert recalculated_word_count == expected_word_count, (
        f"{file_name}: actual_word_count expected {expected_word_count}, got {recalculated_word_count}"
    )

    min_words = int(expected_range["min"])
    max_words = int(expected_range["max"])
    assert min_words <= recalculated_word_count <= max_words, (
        f"{file_name}: recalculated word_count {recalculated_word_count} is outside "
        f"the expected range {min_words}-{max_words}"
    )


@pytest.mark.parametrize("entry", FIXTURE_ENTRIES, ids=lambda entry: entry["file"])
def test_narration_fixture_contents_include_required_linguistic_signals(entry: dict[str, object]) -> None:
    file_name = str(entry["file"])
    text = _read_fixture_text(FIXTURE_DIR / file_name)

    assert any(diacritic in text for diacritic in POLISH_DIACRITICS), (
        f"{file_name}: content - missing Polish diacritics"
    )
    punctuation_variety = {mark for mark in PUNCTUATION_MARKS if mark in text}
    assert len(punctuation_variety) >= 4, (
        f"{file_name}: content - expected punctuation variety, found {sorted(punctuation_variety)!r}"
    )
    assert re.search(r"\d", text), f"{file_name}: content - missing number or date"
    assert ABBREVIATION_RE.search(text), f"{file_name}: content - missing abbreviation"
    assert "—" in text or "–" in text, f"{file_name}: content - missing dialogue marker"
    assert "„" in text or '"' in text, f"{file_name}: content - missing quoted dialogue"

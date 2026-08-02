from __future__ import annotations

from pathlib import Path

import pytest

from app.tts import NarrationChunkingSettings, chunk_narration, normalize_narration


def _joined(chunks):
    return " ".join(chunk.text for chunk in chunks)


def test_chunks_preserve_normalized_polish_narration_and_metadata():
    source = "Pierwszy akapit.\n\nDrugi akapit z polską ąęłńóśźż!"
    chunks = chunk_narration(source, max_words=4)
    assert _joined(chunks) == normalize_narration(source)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.text and chunk.text_hash for chunk in chunks)
    assert all(chunk.text == normalize_narration(source)[chunk.source_start:chunk.source_end] for chunk in chunks)


def test_repeated_sentences_have_distinct_ordered_offsets_and_stable_ids():
    source = "To samo zdanie. To samo zdanie."
    first = chunk_narration(source, max_words=3)
    second = chunk_narration(source, max_words=3)
    assert first == second
    assert [chunk.source_start for chunk in first] == [0, 16]


def test_abbreviations_decimals_dates_and_dialogue_do_not_lose_text():
    source = "Dr. Nowak zapłacił 3.14 zł dnia 12.05.2026. „To działa?” — zapytała."
    chunks = chunk_narration(source, max_words=7)
    assert _joined(chunks) == source
    assert chunks[0].text.startswith("Dr. Nowak")
    assert any("3.14" in chunk.text and "12.05.2026" in chunk.text for chunk in chunks)
    assert any("„To działa?”" in chunk.text for chunk in chunks)


def test_oversized_sentence_is_deterministically_split_and_marked():
    source = "jeden dwa trzy cztery pięć sześć siedem"
    chunks = chunk_narration(source, NarrationChunkingSettings(max_words=3))
    assert [chunk.text for chunk in chunks] == ["jeden dwa trzy", "cztery pięć sześć", "siedem"]
    assert all(chunk.is_oversized for chunk in chunks)
    assert _joined(chunks) == source


@pytest.mark.parametrize("source", ["", " \n\t "])
def test_empty_input_emits_no_chunks(source):
    assert chunk_narration(source) == []


def test_all_narration_fixtures_preserve_normalized_text():
    fixture_directory = Path(__file__).resolve().parents[1] / "fixtures" / "narrations"
    for path in sorted(fixture_directory.glob("*.txt")):
        source = path.read_text(encoding="utf-8")
        assert _joined(chunk_narration(source, max_words=80)) == normalize_narration(source)

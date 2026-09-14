"""Exact source/sample coverage and honest tempo quality, without providers."""

from dataclasses import replace
from hashlib import sha256
import json

import pytest

from app.domain.speech_boundary import SpeechBoundaryMap, SpeechChunkBoundary
from app.tts.chunking import chunk_narration, normalize_narration, sentence_chunks


def measured(text, max_words=2, counts=None):
    chunks = sentence_chunks(text, max_words=max_words)
    counts = counts or [101 + index * 73 for index in range(len(chunks))]
    boundaries, cursor = [], 0
    for chunk, count in zip(chunks, counts, strict=True):
        boundaries.append(SpeechChunkBoundary(chunk.id, chunk.source_span, cursor, cursor + count))
        cursor += count
    return SpeechBoundaryMap(sha256(text.encode()).hexdigest(), len(text), "a" * 64, 8000, cursor, tuple(boundaries))


@pytest.mark.parametrize("text", [
    "  Dr. Nowak ma 3.14 zł.\n\n„To działa!”\tTak?  ",
    "One two three four five six seven eight nine ten. Short!",
    "Same sentence. Same sentence.",
    "Bez końcowej kropki\n\nDrugi akapit",
    "Hello... Really?! Yes.",
    "\tZażółć\u00a0gęślą  jaźń.\r\n🙂 Koniec.\t",
])
def test_original_unicode_and_sample_coverage_without_gaps(text):
    chunks = sentence_chunks(text, max_words=2)
    result = measured(text)
    assert " ".join(c.text for c in chunks) == normalize_narration(text)
    assert "".join(text[c.source_span.start:c.source_span.end] for c in chunks) == text
    assert all(c.text == normalize_narration(text)[c.source_start:c.source_end] for c in chunks)
    assert "".join(text[b.source_start:b.source_end] for b in result.blocks) == text
    assert result.blocks[0].start_frame == 0 and result.blocks[-1].end_frame == result.frame_count
    assert all(a.end_frame == b.start_frame for a, b in zip(result.blocks, result.blocks[1:]))
    assert result == SpeechBoundaryMap.from_payload(json.loads(json.dumps(result.to_payload())))
    result.validate_source(text, "a" * 64, 8000, result.frame_count)


def test_sentence_identity_survives_changed_technical_limits_and_repetition():
    text = "One two three four five six. One two three four five six."
    small, large = measured(text, 2), measured(text, 120)
    assert len(small.chunks) == 6 and len(large.chunks) == 2
    assert len(small.blocks) == len(large.blocks) == 2
    assert [b.sentence_id for b in small.blocks] == [b.sentence_id for b in large.blocks]
    assert small.blocks[0].sentence_id != small.blocks[1].sentence_id
    assert all(len(b.chunk_ids) == 3 for b in small.blocks)


def test_desktop_isolates_sentences_without_changing_legacy_packing():
    assert len(chunk_narration("First sentence. Second sentence.", max_words=120)) == 1
    assert len(sentence_chunks("First sentence. Second sentence.", max_words=120)) == 2
    assert sentence_chunks(" \t\n") == []


@pytest.mark.parametrize("frames", [205, 407, 1001])
def test_tempo_uses_measured_ratio_and_shared_integer_boundaries(frames):
    original = measured("First. Second. Third.", counts=[101, 237, 73])
    result = original.retime(checksum="b" * 64, sample_rate=8000, frame_count=frames)
    assert result.frame_count == result.chunks[-1].end_frame == frames
    assert result.chunks[0].end_frame == (202 * frames + 411) // 822
    assert result.chunks[1].start_frame == result.chunks[0].end_frame
    assert [c.source for c in result.chunks] == [c.source for c in original.chunks]
    assert result.quality == "approximate_internal_positions" and result.source_audio_checksum == "a" * 64
    assert original.audio_checksum == "a" * 64 and original.frame_count == 411
    with pytest.raises(ValueError, match="original"):
        result.retime(checksum="c" * 64, sample_rate=8000, frame_count=300)


@pytest.mark.parametrize("mutation", ["text_gap", "audio_gap", "overlap", "empty_chunk", "sentence_span",
                                      "duplicate_chunk", "duplicate_sentence", "incomplete", "rate", "quality", "bool"])
def test_rejects_invalid_or_incomplete_maps(mutation):
    original = measured("First words. Second words. Third words.", max_words=1)
    chunks = list(original.chunks)
    kwargs = {}
    if mutation == "text_gap": chunks[0] = replace(chunks[0], source=replace(chunks[0].source, start=1))
    if mutation == "audio_gap": chunks[1] = replace(chunks[1], start_frame=chunks[1].start_frame + 1)
    if mutation == "overlap": chunks[1] = replace(chunks[1], start_frame=0)
    if mutation == "empty_chunk": chunks[0] = replace(chunks[0], end_frame=0)
    if mutation == "sentence_span": chunks[1] = replace(chunks[1], source=replace(chunks[1].source, sentence_start=1))
    if mutation == "duplicate_chunk": chunks[1] = replace(chunks[1], chunk_id=chunks[0].chunk_id)
    if mutation == "duplicate_sentence": chunks[-1] = replace(chunks[-1], source=replace(chunks[-1].source, sentence_id=chunks[0].source.sentence_id))
    if mutation == "incomplete": kwargs["frame_count"] = original.frame_count + 1
    if mutation == "rate": kwargs["sample_rate"] = 0
    if mutation == "quality": kwargs["quality"] = "forced_alignment"
    if mutation == "bool": chunks[0] = replace(chunks[0], start_frame=False)
    with pytest.raises(ValueError):
        replace(original, chunks=tuple(chunks), **kwargs)


@pytest.mark.parametrize("rate,frames", [(44100, 200), (8000, 0), (8000, 1)])
def test_incompatible_or_collapsing_processed_measurements_are_rejected(rate, frames):
    with pytest.raises(ValueError):
        measured("First. Second.").retime(checksum="b" * 64, sample_rate=rate, frame_count=frames)


def test_map_rejects_other_text_or_audio_identity():
    result = measured("Hello.")
    for args in [("Other.", "a" * 64, 8000, result.frame_count),
                 ("Hello.", "b" * 64, 8000, result.frame_count),
                 ("Hello.", "a" * 64, 44100, result.frame_count)]:
        with pytest.raises(ValueError):
            result.validate_source(*args)

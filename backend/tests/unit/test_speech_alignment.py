"""D031 known-text mapping, quality outcomes and strict artifact validation."""

from dataclasses import replace
from hashlib import sha256
import json

import pytest

from app.domain.speech_alignment import AlignedWord, SpeechAlignment, source_words
from app.providers.whisperx_alignment import WhisperXAlignmentAdapter, WhisperXAlignmentError


TEXT = "One two three. One four."


def backend(audio, text, language):
    assert audio == b"wav" and text == TEXT and language == "en"
    return {"segments": [{"words": [
        {"word": " One", "start": 0.10, "end": 0.20, "score": 0.99},
        {"word": "two", "start": 0.20, "end": 0.30, "score": 0.55},
        {"word": "unexpected", "start": 0.30, "end": 0.35, "score": 0.9},
        {"word": "One", "start": 0.40, "end": 0.50, "score": 0.95},
        {"word": "four.", "start": 0.50, "end": 0.60, "score": 0.98},
    ]}]}


def test_whisperx_adapter_maps_repeated_known_text_and_reports_mismatch():
    adapter = WhisperXAlignmentAdapter(backend, model_name="wav2vec2-pl", runtime_version="3.4.2")
    result = adapter.align(b"wav", TEXT, "en")
    assert [(word.source_start, word.source_end) for word in result.words] == list(source_words(TEXT))
    assert result.words[2].start_seconds is None
    assert result.words[3].start_seconds == 0.4
    assert result.unmatched_observed_count == 1
    assert adapter.effective_alignment_identity() == {
        "provider": "whisperx", "model": "wav2vec2-pl",
        "runtime_version": "3.4.2", "adapter": "known-text-lcs-v1"}


def test_whisperx_adapter_preserves_explicit_untimed_word_as_omission():
    adapter = WhisperXAlignmentAdapter(lambda *_: {"segments": [{"words": [
        {"word": "One", "start": 0.1, "end": 0.2, "score": 0.9},
        {"word": "two", "start": None, "end": None, "score": None},
        {"word": "three", "start": 0.3, "end": 0.4, "score": 0.9},
        {"word": "One", "start": 0.4, "end": 0.5, "score": 0.9},
        {"word": "four", "start": 0.5, "end": 0.6, "score": 0.9},
    ]}]})
    result = adapter.align(b"wav", TEXT, "en")
    assert result.words[1].start_seconds is None
    assert result.unmatched_observed_count == 0


def test_whisperx_missing_score_is_retained_as_unknown_low_confidence_input():
    adapter = WhisperXAlignmentAdapter(lambda *_: {"segments": [{"words": [
        {"word": "One", "start": 0.1, "end": 0.2},
    ]}]})
    result = adapter.align(b"wav", TEXT, "en")
    assert result.words[0].start_seconds == .1 and result.words[0].confidence is None


@pytest.mark.parametrize("payload", [
    {}, {"segments": None}, {"segments": [{}]},
    {"segments": [{"words": [{"word": "One", "start": 0, "end": 0, "score": .9}]}]},
    {"segments": [{"words": [{"word": "One", "start": 0, "end": 1, "score": 2}]}]},
    {"segments": [{"words": [{"word": "One", "start": 0, "end": None, "score": .9}]}]},
])
def test_whisperx_adapter_rejects_malformed_runtime_results(payload):
    with pytest.raises(WhisperXAlignmentError):
        WhisperXAlignmentAdapter(lambda *_: payload).align(b"wav", TEXT, "en")


def alignment():
    spans = source_words(TEXT)
    words = (
        AlignedWord(0, *spans[0], "sentence-1", "aligned", 100, 200, .99),
        AlignedWord(1, *spans[1], "sentence-1", "low_confidence", 200, 300, .55),
        AlignedWord(2, *spans[2], "sentence-1", "omitted"),
        AlignedWord(3, *spans[3], "sentence-2", "aligned", 400, 500, .95),
        AlignedWord(4, *spans[4], "sentence-2", "aligned", 500, 600, .98),
    )
    return SpeechAlignment(
        "alignment-1", "project-1", "section-1", "revision-1",
        sha256(TEXT.encode()).hexdigest(), len(TEXT), "audio-1", "a" * 64,
        1000, 1000, "whisperx", "wav2vec2", "en", .6, words, 1)


def test_alignment_round_trip_keeps_explicit_quality_and_coverage():
    value = alignment()
    assert value.outcome == "omissions_and_low_confidence_and_provider_insertions"
    assert value.coverage == .8
    assert SpeechAlignment.from_payload(json.loads(json.dumps(value.to_payload()))) == value


@pytest.mark.parametrize("mutation", ["frame_overlap", "frame_limit", "bad_status", "gap", "summary"])
def test_alignment_rejects_nonmonotonic_or_incomplete_evidence(mutation):
    value = alignment()
    words = list(value.words)
    if mutation == "frame_overlap":
        words[3] = replace(words[3], start_frame=250)
    if mutation == "frame_limit":
        words[4] = replace(words[4], end_frame=1001)
    if mutation == "bad_status":
        words[1] = replace(words[1], status="aligned")
    if mutation == "gap":
        words[1] = replace(words[1], index=4)
    if mutation == "summary":
        payload = value.to_payload() | {"coverage": 1.0}
        with pytest.raises(ValueError):
            SpeechAlignment.from_payload(payload)
        return
    with pytest.raises(ValueError):
        replace(value, words=tuple(words))

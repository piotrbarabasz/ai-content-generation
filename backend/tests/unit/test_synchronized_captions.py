"""D032 measured grouping, serialization and optional render identity."""

from dataclasses import replace
from hashlib import sha256
import json

import pytest

from app.domain.caption_track import (
    PublishedCaptionTrack,
    SynchronizedCaptionTrack,
    build_synchronized_caption_track,
    serialize_ass,
    serialize_srt,
)
from app.domain.render_result import render_request
from app.domain.speech_alignment import AlignedWord, SpeechAlignment, source_words
from tests.unit.test_timeline import compile_values, media


TEXT = "Hello, bright world. Next line!"


def alignment(timeline, *, status="aligned", audio_id=None):
    span = timeline.clips[0].media.audio
    ranges = source_words(TEXT)
    frames = ((800, 1_500), (1_600, 2_500), (2_600, 3_600),
              (4_000, 4_900), (5_000, 5_800))
    words = tuple(AlignedWord(
        index, *source, "sentence-1" if index < 3 else "sentence-2",
        status if index == 1 else "aligned", *timing,
        .5 if index == 1 and status == "low_confidence" else .99)
        for index, (source, timing) in enumerate(zip(ranges, frames)))
    return SpeechAlignment(
        "alignment-a", timeline.project_id, "section-a", "revision-a",
        sha256(TEXT.encode("utf-8")).hexdigest(), len(TEXT),
        audio_id or span.artifact_id, span.checksum, span.sample_rate,
        span.frame_count, "fixture", "fixture-aligner", "en", .6, words)


def track_and_publication():
    timeline = compile_values(media())
    track = build_synchronized_caption_track(
        timeline, {"section-a": TEXT}, {"section-a": alignment(timeline)})
    published = PublishedCaptionTrack(
        track, "track-artifact", "1" * 64, "srt-artifact", "2" * 64,
        "ass-artifact", "3" * 64)
    return timeline, track, published


def test_measured_words_form_readable_ordered_bounded_segments_and_round_trip():
    timeline, track, _ = track_and_publication()
    assert [value.text for value in track.segments] == [
        "Hello, bright world.", "Next line!"]
    assert [(value.start_ms, value.end_ms) for value in track.segments] == [
        (100, 450), (500, 725)]
    assert track.duration_ms == 1_000
    assert track.segments[-1].end_ms <= track.duration_ms
    assert SynchronizedCaptionTrack.from_payload(
        json.loads(json.dumps(track.to_payload()))) == track
    assert "Hello, bright world." in serialize_srt(track.segments)
    ass = serialize_ass(track.segments)
    assert "Dialogue: 0,0:00:00.10,0:00:00.45" in ass
    assert ass.endswith("\r\n") and timeline.id == track.timeline_id


@pytest.mark.parametrize("change,match", [
    ("low_confidence", "complete measured"),
    ("wrong_audio", "complete measured"),
    ("cut_word", "cuts through"),
])
def test_incomplete_wrong_or_boundary_cut_alignment_is_never_estimated(change, match):
    value = media()
    timeline = compile_values(value)
    current = alignment(timeline, status="low_confidence" if change == "low_confidence" else "aligned",
                        audio_id="different" if change == "wrong_audio" else None)
    if change == "cut_word":
        value = replace(value, audio=replace(value.audio, start_sample=1_000))
        timeline = compile_values(value)
    with pytest.raises(ValueError, match=match):
        build_synchronized_caption_track(
            timeline, {"section-a": TEXT}, {"section-a": current})


def test_caption_burn_in_is_explicit_and_changes_render_inputs():
    timeline, _, published = track_and_publication()
    disabled = render_request(timeline, {"renderer": "fixture"})
    enabled = render_request(timeline, {"renderer": "fixture"}, published)
    assert json.loads(disabled.settings_json)["captions"] is None
    assert not any(edge.name.startswith("caption") for edge in disabled.inputs)
    assert {edge.name for edge in enabled.inputs if edge.name.startswith("caption")} == {
        "caption_track", "caption_ass"}
    assert enabled != disabled

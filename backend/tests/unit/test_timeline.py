"""D018 exact timing arithmetic and immutable snapshot contracts, entirely offline."""

from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import json
import subprocess
import sys

import pytest

from app.application.timeline import TimelineCompiler, TimelineSceneInput
from app.domain.scene_image import SceneImage
from app.domain.timeline import AudioSpan, OutputTimebase, TimelineClip, TimelineMedia, TimelineRevision


def media(scene="a", *, rate=8000, start=0, end=8000, total=None):
    image = SceneImage("image-" + scene, "project", "accepted-" + scene, scene, "revision-" + scene,
                       "fixture.png", "a" * 64, 100, "PNG", 12, 8, "RGB")
    return TimelineMedia("project", "section-" + scene, "revision-" + scene, scene, "plan-" + scene,
                         "accepted-" + scene, "timing-" + scene, "measured_sentence_blocks", "choice-" + scene, image,
                         AudioSpan("audio-" + scene, "b" * 64, "original", rate, total or end, start, end))


class Selected:
    def __init__(self, *values):
        self.values = {value.scene_id: value for value in values}
        self.reads = []

    def resolve(self, source):
        self.reads.append(source.scene_id)
        if source.scene_id not in self.values:
            raise ValueError("Missing artifact")
        return self.values[source.scene_id]


def compile_values(*values, **kwargs):
    port = Selected(*values)
    inputs = tuple(TimelineSceneInput(value.timing_id, value.scene_id, value.audio.variant) for value in values)
    return TimelineCompiler(port).compile("project", inputs, **kwargs)


def test_exact_mixed_sample_rates_nonzero_source_starts_and_total_rounding():
    a = media(rate=44100, start=13, end=23456, total=50000)
    b = media("b", rate=48000, start=777, end=25789, total=60000)
    timeline = compile_values(a, b, timebase=OutputTimebase(1001, 30000), fit_policy="fill")
    exact = Fraction(23443, 44100) + Fraction(25012, 48000)
    assert timeline.duration == exact
    assert timeline.clips[1].audio_offset == Fraction(23443, 44100)
    assert timeline.clips[0].end_frame == timeline.clips[1].start_frame
    assert sum(clip.duration_frames for clip in timeline.clips) == timeline.total_frames
    assert abs(timeline.video_duration - exact) <= Fraction(1001, 60000)
    assert timeline.clips[1].media.audio == b.audio  # No resampling/float trimming.


def test_cumulative_rounding_avoids_per_clip_drift_and_rounds_ties_up():
    values = [media(str(i), rate=100, end=6) for i in range(100)]
    timeline = compile_values(*values)
    assert timeline.duration == 6 and timeline.total_frames == 150
    assert [c.duration_frames for c in timeline.clips[:4]] == [2, 1, 2, 1]
    assert OutputTimebase().frame_at(Fraction(1, 50)) == 1


def test_reorder_preserves_exact_media_and_changes_identity_without_writes():
    a, b = media(), media("b", end=12345)
    before, after = compile_values(a, b), compile_values(b, a)
    assert before.id != after.id and before.duration == after.duration
    assert before.clips[0].media == after.clips[1].media == a
    assert before.clips[1].media == after.clips[0].media == b
    assert after.clips[1].audio_offset == b.audio.duration
    assert compile_values(a, b) == before
    assert compile_values(a, b).id == before.id


@pytest.mark.parametrize("change", ["fit", "timebase", "image", "selection", "audio", "quality"])
def test_identity_pins_settings_choices_measurements_and_quality(change):
    value, kwargs = media(), {}
    before = compile_values(value)
    if change == "fit":
        kwargs["fit_policy"] = "fill"
    elif change == "timebase":
        kwargs["timebase"] = OutputTimebase(1, 30)
    elif change == "image":
        value = replace(value, image=replace(value.image, artifact_id="other", checksum="c" * 64))
    elif change == "selection":
        value = replace(value, image_selection_id="another-choice")
    elif change == "audio":
        value = replace(value, audio=replace(value.audio, artifact_id="other", checksum="d" * 64))
    else:
        value = replace(value, timing_quality="approximate_internal_positions")
    assert compile_values(value, **kwargs).id != before.id


def test_json_roundtrip_is_path_free_independent_and_deeply_immutable():
    original = compile_values(media(), media("b"))
    payload = original.to_payload()
    assert TimelineRevision.from_payload(json.loads(json.dumps(payload))) == original
    assert "storage_key" not in json.dumps(payload)
    payload["clips"][0]["media"]["audio"]["start_sample"] = 1
    assert original.clips[0].media.audio.start_sample == 0
    for obj, field, value in ((original, "fit_policy", "fill"), (original.clips[0], "start_frame", 1),
                               (original.clips[0].media.audio, "end_sample", 10),
                               (original.clips[0].media.image, "width", 42)):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field, value)


@pytest.mark.parametrize("field,value", [("start_sample", -1), ("end_sample", 0), ("end_sample", 8001),
                                         ("start_sample", 8000), ("sample_rate", 0), ("frame_count", 0),
                                         ("sample_rate", True), ("start_sample", 0.0), ("end_sample", 1.5),
                                         ("checksum", "bad"), ("variant", "latest"), ("artifact_id", "")])
def test_invalid_or_out_of_range_audio_fails(field, value):
    with pytest.raises(ValueError):
        replace(media().audio, **{field: value})


@pytest.mark.parametrize("numerator,denominator", [(0, 25), (-1, 25), (1, 0), (1, -25), (2, 50), (True, 25), (1, 25.0)])
def test_invalid_or_noncanonical_timebase_fails(numerator, denominator):
    with pytest.raises(ValueError):
        OutputTimebase(numerator, denominator)


def test_unrepresentably_short_clip_is_rejected_without_padding_or_dropping():
    with pytest.raises(ValueError, match="one video frame"):
        compile_values(media(end=1))


@pytest.mark.parametrize("kind", ["audio_gap", "audio_overlap", "video_gap", "video_overlap", "wrong_rounding",
                                  "duplicate", "foreign", "mutable", "empty", "fit"])
def test_snapshot_rejects_invalid_order_offsets_and_ownership(kind):
    timeline = compile_values(media(), media("b"))
    a, b = timeline.clips
    changes = {}
    if kind == "audio_gap":
        b = replace(b, audio_offset=b.audio_offset + 1)
    elif kind == "audio_overlap":
        b = replace(b, audio_offset=Fraction(0))
    elif kind == "video_gap":
        b = replace(b, start_frame=b.start_frame + 1)
    elif kind == "video_overlap":
        b = replace(b, start_frame=b.start_frame - 1)
    elif kind == "wrong_rounding":
        b = replace(b, end_frame=b.end_frame + 1)
    elif kind == "duplicate":
        b = replace(b, media=a.media)
    elif kind == "foreign":
        changes["project_id"] = "other"
    elif kind == "mutable":
        changes["clips"] = [a, b]
    elif kind == "empty":
        changes["clips"] = ()
    else:
        changes["fit_policy"] = "stretch"
    with pytest.raises(ValueError):
        replace(timeline, **({"clips": (a, b)} | changes))


@pytest.mark.parametrize("kind", ["version", "identity", "duration", "offset", "timebase", "image_owner", "rounding"])
def test_deserialization_validates_structure_measurements_and_fingerprint(kind):
    payload = compile_values(media()).to_payload()
    if kind == "version":
        payload["version"] = True
    elif kind == "identity":
        payload["id"] = "timeline_" + "0" * 64
    elif kind == "duration":
        payload["clips"][0]["audio_duration"] = [2, 1]
    elif kind == "offset":
        payload["clips"][0]["audio_offset"] = [0, 2]
    elif kind == "timebase":
        payload["timebase"]["denominator"] = 25.0
    elif kind == "image_owner":
        payload["clips"][0]["media"]["image"]["scene_id"] = "other"
    else:
        payload["rounding"] = "per_clip_floor"
    with pytest.raises(ValueError):
        TimelineRevision.from_payload(payload)


@pytest.mark.parametrize("kind", ["missing", "project", "scene", "timing", "variant", "duplicate", "empty", "changed"])
def test_compiler_rejects_missing_mismatched_or_changed_selections(kind):
    a = media()
    source = TimelineSceneInput(a.timing_id, a.scene_id, "original")
    port, project = Selected(a), "project"
    sources = [source]
    if kind == "missing":
        port.values.clear()
    elif kind == "project":
        project = "other"
    elif kind in ("scene", "timing", "variant"):
        field = {"scene": "scene_id", "timing": "timing_id", "variant": "audio_variant"}[kind]
        sources = [replace(source, **{field: "processed" if kind == "variant" else "other"})]
    elif kind == "duplicate":
        sources *= 2
    elif kind == "empty":
        sources = []
    else:
        resolve = port.resolve

        def changing(source):
            value = resolve(source)
            return replace(value, image_selection_id="changed") if len(port.reads) > 1 else value

        port.resolve = changing
    with pytest.raises(ValueError):
        TimelineCompiler(port).compile(project, sources)


def test_application_import_does_not_load_storage_providers_tts_or_ui():
    command = """
import sys
from app.application.timeline import TimelineCompiler
for name in ('app.storage', 'app.providers', 'app.tts', 'sqlite3', 'PIL', 'PySide6'):
    assert not any(m == name or m.startswith(name + '.') for m in sys.modules), name
"""
    result = subprocess.run([sys.executable, "-c", command], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

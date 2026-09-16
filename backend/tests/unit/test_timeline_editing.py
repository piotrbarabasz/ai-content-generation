"""Bounded edits with mixed rates, pinned sources and optimistic concurrency."""

from dataclasses import replace
from fractions import Fraction

import pytest

from app.application.timeline import TimelineSceneInput
from app.application.timeline_editing import TimelineEdit, TimelineEditingService
from tests.unit.test_timeline import Selected, media


class History:
    def __init__(self):
        self.events = []

    def current(self):
        return self.events[-1] if self.events else None

    def save(self, timeline, expected):
        assert (self.current().id if self.current() else None) == expected
        event = TimelineEdit(str(len(self.events)), timeline)
        self.events.append(event)
        return event


class Sources(Selected):
    def boundaries(self, value):
        return (0, value.audio.sample_rate // 2, value.audio.sample_rate)


@pytest.fixture
def service():
    return TimelineEditingService("project", History(), Sources(media(rate=44100), media("b", rate=48000)))


def append(service, scene):
    source = service.media.values[scene]
    current = service.current()
    return service.append(TimelineSceneInput(source.timing_id, scene, "original"),
                          expected=current.id if current else None)


def test_trim_move_and_aba_preserve_pinned_media(service):
    # Use one second at each rate and trim on the measured half-second edge.
    service.media.values = {"a": media(rate=44100, end=44100), "b": media("b", rate=48000, end=48000)}
    append(service, "a")
    old = append(service, "b")
    trimmed = service.set_range("a", 22050, 44100, expected=old.id)
    assert trimmed.timeline.duration == Fraction(3, 2)
    pinned = trimmed.timeline.clips[0].media
    service.media.values["a"] = replace(pinned, image_selection_id="new-choice")
    moved = service.move("a", 1, expected=trimmed.id)
    assert moved.timeline.clips[1].media == pinned
    assert moved.timeline.clips[1].audio_offset == 1
    restored = service.move("a", 0, expected=moved.id)
    assert restored.timeline == trimmed.timeline and restored.id != trimmed.id
    with pytest.raises(ValueError, match="changed"):
        service.move("a", 1, expected=trimmed.id)


@pytest.mark.parametrize("start,end", [(1, 8000), (0, 9000), (-1, 8000), (8000, 0), (0, 0), (True, 8000)])
def test_invalid_boundaries_write_nothing(service, start, end):
    old = append(service, "a")
    with pytest.raises(ValueError):
        service.set_range("a", start, end, expected=old.id)
    assert service.current() == old and len(service.history.events) == 1


def test_invalid_moves_duplicates_and_last_removal_preserve_history(service):
    old = append(service, "a")
    for position in (-1, 1, True):
        with pytest.raises(ValueError):
            service.move("a", position, expected=old.id)
    with pytest.raises(ValueError):
        append(service, "a")
    with pytest.raises(ValueError):
        service.remove("a", expected=old.id)
    second = append(service, "b")
    removed = service.remove("a", expected=second.id)
    assert removed.timeline.clips[0].media.scene_id == "b"
    assert removed.timeline.clips[0].audio_offset == 0

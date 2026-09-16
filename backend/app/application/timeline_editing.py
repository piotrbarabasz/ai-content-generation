"""Bounded edits of pinned media; persistence and boundary measurements are ports."""

from dataclasses import dataclass, replace
from fractions import Fraction

from app.application.timeline import TimelineCompiler
from app.domain.timeline import OutputTimebase, TimelineClip, TimelineRevision


@dataclass(frozen=True)
class TimelineEdit:
    id: str
    timeline: TimelineRevision


class TimelineEditingService:
    def __init__(self, project_id, history, media):
        self.project_id, self.history, self.media = project_id, history, media

    def current(self):
        return self.history.current()

    def _expected(self, expected):
        current = self.current()
        if (current.id if current else None) != expected:
            raise ValueError("Timeline changed; refresh before editing.")
        return current

    def _save(self, values, previous):
        template = previous.timeline if previous else None
        timebase = template.timebase if template else OutputTimebase()
        cursor, clips = Fraction(0), []
        for media in values:
            end = cursor + media.audio.duration
            clips.append(TimelineClip(media, cursor, timebase.frame_at(cursor), timebase.frame_at(end)))
            cursor = end
        timeline = TimelineRevision(self.project_id, timebase, template.fit_policy if template else "fit", tuple(clips))
        return self.history.save(timeline, previous.id if previous else None)

    def append(self, source, *, expected):
        previous = self._expected(expected)
        compiled = TimelineCompiler(self.media).compile(self.project_id, [source])
        values = [clip.media for clip in previous.timeline.clips] if previous else []
        return self._save([*values, compiled.clips[0].media], previous)

    def move(self, scene_id, position, *, expected):
        previous = self._expected(expected)
        if previous is None:
            raise ValueError("Select a timeline clip first.")
        values = [clip.media for clip in previous.timeline.clips]
        if type(position) is not int or not 0 <= position < len(values):
            raise ValueError("Invalid timeline position.")
        source = next((v for v in values if v.scene_id == scene_id), None)
        if source is None:
            raise ValueError("Unknown timeline clip.")
        values.remove(source)
        values.insert(position, source)
        return self._save(values, previous)

    def set_range(self, scene_id, start, end, *, expected):
        previous = self._expected(expected)
        if previous is None:
            raise ValueError("Select a timeline clip first.")
        values = [clip.media for clip in previous.timeline.clips]
        index = next((i for i, v in enumerate(values) if v.scene_id == scene_id), None)
        if index is None:
            raise ValueError("Unknown timeline clip.")
        media = values[index]
        boundaries = self.media.boundaries(media)
        if type(start) is not int or type(end) is not int or start >= end or start not in boundaries or end not in boundaries:
            raise ValueError("Choose increasing, verified sentence boundaries within the source scene.")
        values[index] = replace(media, audio=replace(media.audio, start_sample=start, end_sample=end))
        return self._save(values, previous)

    def remove(self, scene_id, *, expected):
        previous = self._expected(expected)
        if previous is None:
            raise ValueError("Select a timeline clip first.")
        values = [clip.media for clip in previous.timeline.clips if clip.media.scene_id != scene_id]
        if len(values) == len(previous.timeline.clips):
            raise ValueError("Unknown timeline clip.")
        if not values:
            raise ValueError("A timeline must retain at least one clip.")
        return self._save(values, previous)

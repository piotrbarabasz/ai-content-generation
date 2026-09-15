"""Compile selected scenes without generation, persistence or rendering."""

from dataclasses import dataclass
from fractions import Fraction
from typing import Protocol

from app.domain.timeline import OutputTimebase, TimelineClip, TimelineMedia, TimelineRevision


@dataclass(frozen=True)
class TimelineSceneInput:
    timing_id: str
    scene_id: str
    audio_variant: str

    def __post_init__(self):
        if (any(type(value) is not str or not value.strip() for value in (self.timing_id, self.scene_id))
                or self.audio_variant not in ("original", "processed")):
            raise ValueError("Timeline input requires timing/scene IDs and an explicit audio variant.")


class TimelineMediaPort(Protocol):
    def resolve(self, source: TimelineSceneInput) -> TimelineMedia:
        """Read verified selected media for the exact current accepted scene."""
        ...


class TimelineCompiler:
    def __init__(self, media: TimelineMediaPort):
        self.media = media

    def compile(self, project_id, sources, *, timebase=OutputTimebase(), fit_policy="fit"):
        sources = tuple(sources)
        if not sources or any(not isinstance(source, TimelineSceneInput) for source in sources):
            raise ValueError("Compile requires explicit nonempty timeline scene inputs.")
        if not isinstance(timebase, OutputTimebase):
            raise ValueError("Compile requires an exact output timebase.")
        if len({source.scene_id for source in sources}) != len(sources):
            raise ValueError("Timeline scenes must be unique.")
        selected, clips, cursor = [], [], Fraction(0)
        for source in sources:
            media = self.media.resolve(source)
            if (not isinstance(media, TimelineMedia) or (media.project_id, media.timing_id, media.scene_id, media.audio.variant)
                    != (project_id, source.timing_id, source.scene_id, source.audio_variant)):
                raise ValueError("Resolved media differs from the requested project, timing, scene or variant.")
            clips.append(TimelineClip(media, cursor, timebase.frame_at(cursor), timebase.frame_at(cursor + media.audio.duration)))
            selected.append(media)
            cursor += media.audio.duration
        # Reads can be slow. Reject a changed selection instead of returning a mixed snapshot.
        for source, expected in zip(sources, selected):
            if self.media.resolve(source) != expected:
                raise ValueError("Selected timeline inputs changed during compilation; refresh and compile again.")
        return TimelineRevision(project_id, timebase, fit_policy, tuple(clips))

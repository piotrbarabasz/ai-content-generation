"""Immutable, path-free render snapshots; audio samples are not video frames."""

from dataclasses import asdict, dataclass
from fractions import Fraction
from math import gcd

from .dependencies import content_fingerprint
from .scene_image import SceneImage


def _text(value):
    if type(value) is not str or not value.strip():
        raise ValueError("Timeline identities must be nonempty strings.")


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("Invalid timeline integer measurement.")


def _ratio(value):
    return [value.numerator, value.denominator]


def _read_ratio(value):
    if type(value) is not list or len(value) != 2:
        raise ValueError("Expected a canonical rational time.")
    _integer(value[0])
    _integer(value[1], 1)
    if gcd(*value) != 1:
        raise ValueError("Rational time must be reduced.")
    return Fraction(*value)


@dataclass(frozen=True)
class OutputTimebase:
    """Reduced positive seconds per video frame, e.g. 1001/30000."""

    numerator: int = 1
    denominator: int = 25

    def __post_init__(self):
        _integer(self.numerator, 1)
        _integer(self.denominator, 1)
        if gcd(self.numerator, self.denominator) != 1:
            raise ValueError("Output timebase must be reduced.")

    @property
    def seconds(self):
        return Fraction(self.numerator, self.denominator)

    def frame_at(self, seconds):
        if type(seconds) is not Fraction or seconds < 0:
            raise ValueError("Frame conversion requires nonnegative exact time.")
        frames = seconds / self.seconds
        return (2 * frames.numerator + frames.denominator) // (2 * frames.denominator)


@dataclass(frozen=True)
class AudioSpan:
    artifact_id: str
    checksum: str
    variant: str
    sample_rate: int
    frame_count: int
    start_sample: int
    end_sample: int

    def __post_init__(self):
        _text(self.artifact_id)
        if (type(self.checksum) is not str or len(self.checksum) != 64
                or any(c not in "0123456789abcdef" for c in self.checksum)
                or self.variant not in ("original", "processed")):
            raise ValueError("Audio span requires an exact checksum and explicit variant.")
        for value in (self.sample_rate, self.frame_count, self.end_sample):
            _integer(value, 1)
        _integer(self.start_sample)
        if not self.start_sample < self.end_sample <= self.frame_count:
            raise ValueError("Audio source span is empty or out of range.")

    @property
    def duration(self):
        return Fraction(self.end_sample - self.start_sample, self.sample_rate)


@dataclass(frozen=True)
class TimelineMedia:
    project_id: str
    section_id: str
    section_revision_id: str
    scene_id: str
    plan_id: str
    acceptance_id: str
    timing_id: str
    timing_quality: str
    image_selection_id: str
    image: SceneImage
    audio: AudioSpan

    def __post_init__(self):
        for name in ("project_id", "section_id", "section_revision_id", "scene_id", "plan_id",
                     "acceptance_id", "timing_id", "image_selection_id"):
            _text(getattr(self, name))
        if (not isinstance(self.image, SceneImage) or not isinstance(self.audio, AudioSpan)
                or self.timing_quality not in ("measured_sentence_blocks", "approximate_internal_positions")):
            raise ValueError("Timeline requires measured media and known timing quality.")
        if (self.image.project_id, self.image.scene_id, self.image.section_revision_id, self.image.acceptance_id) != (
                self.project_id, self.scene_id, self.section_revision_id, self.acceptance_id):
            raise ValueError("Timeline image belongs to a different accepted scene revision.")

    @classmethod
    def from_payload(cls, value):
        data = dict(value)
        data["image"] = SceneImage(**data["image"])
        data["audio"] = AudioSpan(**data["audio"])
        return cls(**data)


@dataclass(frozen=True)
class TimelineClip:
    media: TimelineMedia
    audio_offset: Fraction
    start_frame: int
    end_frame: int

    def __post_init__(self):
        if not isinstance(self.media, TimelineMedia) or type(self.audio_offset) is not Fraction or self.audio_offset < 0:
            raise ValueError("Timeline clip requires immutable media and exact audio offset.")
        _integer(self.start_frame)
        _integer(self.end_frame)
        if self.end_frame <= self.start_frame:
            raise ValueError("Timeline clip must occupy at least one video frame.")

    @property
    def duration(self):
        return self.media.audio.duration

    @property
    def duration_frames(self):
        return self.end_frame - self.start_frame

    def to_payload(self):
        return {"media": asdict(self.media), "audio_offset": _ratio(self.audio_offset),
                "start_frame": self.start_frame, "end_frame": self.end_frame,
                "audio_duration": _ratio(self.duration)}

    @classmethod
    def from_payload(cls, value):
        data = dict(value)
        duration = _read_ratio(data.pop("audio_duration"))
        data["media"] = TimelineMedia.from_payload(data["media"])
        data["audio_offset"] = _read_ratio(data["audio_offset"])
        clip = cls(**data)
        if duration != clip.duration:
            raise ValueError("Timeline duration differs from its exact audio source span.")
        return clip


@dataclass(frozen=True)
class TimelineRevision:
    project_id: str
    timebase: OutputTimebase
    fit_policy: str
    clips: tuple[TimelineClip, ...]

    def __post_init__(self):
        _text(self.project_id)
        if (not isinstance(self.timebase, OutputTimebase) or self.fit_policy not in ("fit", "fill")
                or type(self.clips) is not tuple or not self.clips):
            raise ValueError("Timeline requires a timebase, fit/fill policy and immutable nonempty clips.")
        cursor, frames, seen = Fraction(0), 0, set()
        for clip in self.clips:
            if not isinstance(clip, TimelineClip):
                raise ValueError("Expected an immutable timeline clip.")
            if (clip.media.project_id != self.project_id or clip.media.scene_id in seen
                    or clip.audio_offset != cursor or clip.start_frame != frames
                    or clip.end_frame != self.timebase.frame_at(cursor + clip.duration)):
                raise ValueError("Timeline ownership, unique ordering or contiguous rounded offsets are invalid.")
            cursor += clip.duration
            frames = clip.end_frame
            seen.add(clip.media.scene_id)

    @property
    def duration(self):
        return sum((clip.duration for clip in self.clips), Fraction(0))

    @property
    def total_frames(self):
        return self.clips[-1].end_frame

    @property
    def video_duration(self):
        return self.total_frames * self.timebase.seconds

    def _content(self):
        return {"version": 1, "project_id": self.project_id, "timebase": asdict(self.timebase),
                "fit_policy": self.fit_policy, "rounding": "cumulative_nearest_ties_up",
                "clips": [clip.to_payload() for clip in self.clips]}

    @property
    def id(self):
        return "timeline_" + content_fingerprint(self._content())

    def to_payload(self):
        return {"id": self.id, **self._content()}

    @classmethod
    def from_payload(cls, value):
        data = dict(value)
        identity = data.pop("id")
        version = data.pop("version")
        if type(version) is not int or version != 1 or data.pop("rounding") != "cumulative_nearest_ties_up":
            raise ValueError("Unsupported timeline version or rounding policy.")
        data["timebase"] = OutputTimebase(**data["timebase"])
        data["clips"] = tuple(TimelineClip.from_payload(clip) for clip in data["clips"])
        timeline = cls(**data)
        if timeline.id != identity:
            raise ValueError("Timeline content differs from its immutable identity.")
        return timeline

"""Validated legacy captions and measured desktop caption tracks."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from fractions import Fraction
from hashlib import sha256

from app.domain.base import DomainEntity, DomainValidationError, new_id
from app.domain.dependencies import content_fingerprint
from app.domain.speech_alignment import SpeechAlignment, source_words
from app.domain.timeline import TimelineRevision


@dataclass(frozen=True, slots=True)
class CaptionSegment:
    id: str
    index: int
    start_ms: int
    end_ms: int
    text: str


def validate_caption_segments(
    values: Sequence[Mapping[str, object]],
) -> tuple[CaptionSegment, ...]:
    """Validate canonical structured timing before any subtitle persistence."""

    if not values:
        raise DomainValidationError("Caption segments cannot be empty.")
    segments: list[CaptionSegment] = []
    previous_end = 0
    for index, value in enumerate(values, start=1):
        if not isinstance(value, Mapping):
            raise DomainValidationError("Each caption segment must be an object.")
        raw_start = value.get("start_ms", value.get("startMs"))
        raw_end = value.get("end_ms", value.get("endMs"))
        if isinstance(raw_start, bool) or isinstance(raw_end, bool):
            raise DomainValidationError("Caption timestamps must be integer milliseconds.")
        if isinstance(raw_start, float) and not raw_start.is_integer():
            raise DomainValidationError("Caption timestamps must be integer milliseconds.")
        if isinstance(raw_end, float) and not raw_end.is_integer():
            raise DomainValidationError("Caption timestamps must be integer milliseconds.")
        try:
            start_ms = int(raw_start)
            end_ms = int(raw_end)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("Caption timestamps must be integer milliseconds.") from exc
        text = str(value.get("text", "")).strip()
        if not text:
            raise DomainValidationError("Caption segment text cannot be empty.")
        if start_ms < 0 or end_ms < 0:
            raise DomainValidationError("Caption timestamps cannot be negative.")
        if start_ms >= end_ms:
            raise DomainValidationError("Caption segment start must be before end.")
        if index > 1 and start_ms < previous_end:
            raise DomainValidationError("Caption segments must be monotonic and cannot overlap.")
        signature = sha256(
            f"{index}|{start_ms}|{end_ms}|{text}".encode("utf-8")
        ).hexdigest()[:12]
        segments.append(
            CaptionSegment(
                id=f"caption_{signature}",
                index=index,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
            )
        )
        previous_end = end_ms
    return tuple(segments)


def _srt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def serialize_srt(segments: Sequence[CaptionSegment]) -> str:
    """Serialize deterministic UTF-8-ready SRT with CRLF line endings."""

    blocks = [
        f"{segment.index}\r\n"
        f"{_srt_timestamp(segment.start_ms)} --> {_srt_timestamp(segment.end_ms)}\r\n"
        f"{segment.text}"
        for segment in segments
    ]
    return "\r\n\r\n".join(blocks) + "\r\n"


def _ass_timestamp(milliseconds: int, *, end: bool) -> str:
    # ASS has centisecond resolution. Keep the rendered interval inside the
    # measured millisecond interval rather than extending estimated timing.
    centiseconds = milliseconds // 10 if end else (milliseconds + 9) // 10
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def _ass_text(value: str) -> str:
    return (value.replace("\\", r"\\")
            .replace("{", r"\{").replace("}", r"\}")
            .replace("\r\n", r"\N").replace("\r", r"\N").replace("\n", r"\N"))


def serialize_ass(segments: Sequence[CaptionSegment]) -> str:
    """Serialize a deterministic, static ASS track suitable for libass burn-in."""

    values = tuple(segments)
    if not values:
        raise DomainValidationError("Caption segments cannot be empty.")
    events = []
    for segment in values:
        start = _ass_timestamp(segment.start_ms, end=False)
        end = _ass_timestamp(segment.end_ms, end=True)
        if segment.end_ms // 10 <= (segment.start_ms + 9) // 10:
            raise DomainValidationError(
                "Measured caption interval is too short for ASS centisecond timing."
            )
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{_ass_text(segment.text)}")
    header = (
        "[Script Info]\r\n"
        "ScriptType: v4.00+\r\n"
        "PlayResX: 1280\r\n"
        "PlayResY: 720\r\n"
        "WrapStyle: 0\r\n"
        "ScaledBorderAndShadow: yes\r\n\r\n"
        "[V4+ Styles]\r\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\r\n"
        "Style: Default,Arial,42,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "0,0,0,0,100,100,0,0,1,2,1,2,60,60,42,1\r\n\r\n"
        "[Events]\r\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n"
    )
    return header + "\r\n".join(events) + "\r\n"


def _milliseconds(value: Fraction) -> int:
    scaled = value * 1000
    return (2 * scaled.numerator + scaled.denominator) // (2 * scaled.denominator)


@dataclass(frozen=True, slots=True)
class SynchronizedCaptionTrack:
    """Static captions derived only from measured D031 word intervals."""

    id: str
    project_id: str
    timeline_id: str
    language: str
    duration_ms: int
    alignment_ids: tuple[str, ...]
    segments: tuple[CaptionSegment, ...]
    method: str = "measured_word_alignment_v1"

    def __post_init__(self):
        if (not isinstance(self.id, str) or not self.id.startswith("caption_track_")
                or any(not isinstance(value, str) or not value.strip()
                       for value in (self.project_id, self.timeline_id, self.language))
                or type(self.duration_ms) is not int or self.duration_ms <= 0
                or not isinstance(self.alignment_ids, tuple) or not self.alignment_ids
                or len(set(self.alignment_ids)) != len(self.alignment_ids)
                or any(not isinstance(value, str) or not value.strip()
                       for value in self.alignment_ids)
                or not isinstance(self.segments, tuple) or not self.segments
                or self.method != "measured_word_alignment_v1"):
            raise DomainValidationError("Synchronized caption track identity is invalid.")
        canonical = validate_caption_segments([asdict(value) for value in self.segments])
        if canonical != self.segments or canonical[-1].end_ms > self.duration_ms:
            raise DomainValidationError("Caption intervals must fit the selected audio duration.")
        serialize_ass(canonical)  # Every retained track must support both promised exports.
        if self.id != self.content_id(self._content()):
            raise DomainValidationError("Caption track ID differs from its immutable content.")

    @staticmethod
    def content_id(value) -> str:
        return "caption_track_" + content_fingerprint(value)

    def _content(self):
        return {"version": 1, "project_id": self.project_id,
                "timeline_id": self.timeline_id, "language": self.language,
                "duration_ms": self.duration_ms,
                "alignment_ids": list(self.alignment_ids),
                "segments": [asdict(value) for value in self.segments],
                "method": self.method}

    def to_payload(self):
        return {"id": self.id, **self._content()}

    @classmethod
    def from_payload(cls, value):
        data = dict(value)
        if data.pop("version", None) != 1:
            raise DomainValidationError("Unsupported synchronized caption track version.")
        data["alignment_ids"] = tuple(data["alignment_ids"])
        data["segments"] = tuple(CaptionSegment(**item) for item in data["segments"])
        return cls(**data)


def _digest(value: str) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


@dataclass(frozen=True, slots=True)
class PublishedCaptionTrack:
    """A verified track and its independently exportable SRT/ASS artifacts."""

    track: SynchronizedCaptionTrack
    track_artifact_id: str
    track_checksum: str
    srt_artifact_id: str
    srt_checksum: str
    ass_artifact_id: str
    ass_checksum: str

    def __post_init__(self):
        if (not isinstance(self.track, SynchronizedCaptionTrack)
                or any(not isinstance(getattr(self, name), str)
                       or not getattr(self, name).strip()
                       for name in ("track_artifact_id", "srt_artifact_id", "ass_artifact_id"))
                or any(not _digest(getattr(self, name))
                       for name in ("track_checksum", "srt_checksum", "ass_checksum"))):
            raise DomainValidationError("Published caption artifact identity is invalid.")

    def to_payload(self):
        return {"version": 1, "track": self.track.to_payload(),
                "track_artifact_id": self.track_artifact_id,
                "track_checksum": self.track_checksum,
                "srt_artifact_id": self.srt_artifact_id,
                "srt_checksum": self.srt_checksum,
                "ass_artifact_id": self.ass_artifact_id,
                "ass_checksum": self.ass_checksum}

    @classmethod
    def from_payload(cls, value):
        data = dict(value)
        if data.pop("version", None) != 1:
            raise DomainValidationError("Unsupported published caption reference version.")
        data["track"] = SynchronizedCaptionTrack.from_payload(data["track"])
        return cls(**data)


def build_synchronized_caption_track(
    timeline: TimelineRevision,
    section_texts: Mapping[str, str],
    alignments: Mapping[str, SpeechAlignment],
    *,
    language: str | None = None,
    max_words: int = 8,
    max_chars: int = 42,
    max_duration_ms: int = 4_000,
) -> SynchronizedCaptionTrack:
    """Map measured source-word frames onto the selected timeline audio."""

    if not isinstance(timeline, TimelineRevision):
        raise DomainValidationError("Synchronized captions require a timeline revision.")
    if (type(max_words) is not int or max_words <= 0
            or type(max_chars) is not int or max_chars <= 0
            or type(max_duration_ms) is not int or max_duration_ms <= 0):
        raise DomainValidationError("Caption readability limits must be positive integers.")
    section_ids = tuple(dict.fromkeys(clip.media.section_id for clip in timeline.clips))
    if set(section_texts) != set(section_ids) or set(alignments) != set(section_ids):
        raise DomainValidationError("Captions require exactly one text and alignment per timeline section.")
    ordered_alignments = tuple(alignments[value] for value in section_ids)
    resolved_language = language or ordered_alignments[0].language
    if (not isinstance(resolved_language, str) or not resolved_language.strip()
            or any(item.language != resolved_language for item in ordered_alignments)):
        raise DomainValidationError("Caption language must match every selected alignment.")

    selected = []
    used_words = set()
    for clip_index, clip in enumerate(timeline.clips):
        media, span = clip.media, clip.media.audio
        text = section_texts[media.section_id]
        alignment = alignments[media.section_id]
        if (alignment.outcome != "complete"
                or alignment.project_id != timeline.project_id
                or alignment.section_id != media.section_id
                or alignment.revision_id != media.section_revision_id
                or alignment.text_checksum != sha256(text.encode("utf-8")).hexdigest()
                or alignment.text_length != len(text)
                or tuple((word.source_start, word.source_end) for word in alignment.words)
                != source_words(text)
                or (alignment.audio_artifact_id, alignment.audio_checksum,
                    alignment.sample_rate, alignment.frame_count)
                != (span.artifact_id, span.checksum, span.sample_rate, span.frame_count)):
            raise DomainValidationError(
                "Captions require complete measured alignment for the exact selected text and audio."
            )
        for word in alignment.words:
            intersects = word.end_frame > span.start_sample and word.start_frame < span.end_sample
            if not intersects:
                continue
            if not span.start_sample <= word.start_frame < word.end_frame <= span.end_sample:
                raise DomainValidationError("A selected audio boundary cuts through an aligned word.")
            identity = (alignment.id, word.index)
            if identity in used_words:
                raise DomainValidationError("Timeline clips select an aligned word more than once.")
            used_words.add(identity)
            start = clip.audio_offset + Fraction(word.start_frame - span.start_sample, span.sample_rate)
            end = clip.audio_offset + Fraction(word.end_frame - span.start_sample, span.sample_rate)
            if not 0 <= start < end <= timeline.duration:
                raise DomainValidationError("Mapped caption timing exceeds selected timeline audio.")
            selected.append((clip_index, alignment, text, word, start, end))
    if not selected:
        raise DomainValidationError("Selected timeline audio has no aligned words to caption.")

    groups = []
    current = []

    def trailing_end(alignment, text, word):
        following = alignment.words[word.index + 1] if word.index + 1 < len(alignment.words) else None
        return following.source_start if following is not None else len(text)

    def candidate_text(values):
        _, alignment, text, first, _, _ = values[0]
        last = values[-1][3]
        return text[first.source_start:trailing_end(alignment, text, last)].strip()

    for item in selected:
        if current:
            same_block = (item[0] == current[-1][0]
                          and item[1].id == current[-1][1].id
                          and item[3].sentence_id == current[-1][3].sentence_id)
            proposal = current + [item]
            elapsed_ms = _milliseconds(proposal[-1][5] - proposal[0][4])
            if (not same_block or len(proposal) > max_words
                    or len(candidate_text(proposal)) > max_chars
                    or elapsed_ms > max_duration_ms):
                groups.append(current)
                current = []
        current.append(item)
    if current:
        groups.append(current)

    raw_segments = []
    for group in groups:
        start_ms, end_ms = _milliseconds(group[0][4]), _milliseconds(group[-1][5])
        if end_ms // 10 <= (start_ms + 9) // 10:
            raise DomainValidationError(
                "Measured caption interval is too short for synchronized ASS export."
            )
        raw_segments.append({"start_ms": start_ms, "end_ms": end_ms,
                             "text": candidate_text(group)})
    segments = validate_caption_segments(raw_segments)
    duration_ms = _milliseconds(timeline.duration)
    content = {"version": 1, "project_id": timeline.project_id,
               "timeline_id": timeline.id, "language": resolved_language,
               "duration_ms": duration_ms,
               "alignment_ids": [value.id for value in ordered_alignments],
               "segments": [asdict(value) for value in segments],
               "method": "measured_word_alignment_v1"}
    return SynchronizedCaptionTrack(
        SynchronizedCaptionTrack.content_id(content), timeline.project_id, timeline.id,
        resolved_language, duration_ms, tuple(content["alignment_ids"]), segments)


@dataclass(slots=True)
class CaptionTrack(DomainEntity):
    workflow_run_id: str = ""
    provider: str = ""
    caption_storage_key: str = ""
    srt_storage_key: str = ""
    language: str = "en"
    approved_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        workflow_run_id: str,
        provider: str,
        caption_storage_key: str,
        srt_storage_key: str = "",
        language: str = "en",
        approved_at: datetime | None = None,
    ) -> "CaptionTrack":
        if not workflow_run_id.strip():
            raise DomainValidationError("CaptionTrack workflow_run_id is required.")
        if not provider.strip():
            raise DomainValidationError("CaptionTrack provider is required.")
        if not caption_storage_key.strip():
            raise DomainValidationError("CaptionTrack caption_storage_key is required.")
        if not language.strip():
            raise DomainValidationError("CaptionTrack language is required.")

        return cls(
            id=new_id("caption_track"),
            workflow_run_id=workflow_run_id,
            provider=provider,
            caption_storage_key=caption_storage_key,
            srt_storage_key=srt_storage_key,
            language=language.strip().lower(),
            approved_at=approved_at,
        )


__all__ = [
    "CaptionSegment",
    "CaptionTrack",
    "PublishedCaptionTrack",
    "SynchronizedCaptionTrack",
    "build_synchronized_caption_track",
    "serialize_ass",
    "serialize_srt",
    "validate_caption_segments",
]

"""Exact contiguous excerpts and safe vertical presentation; no parent edits."""
from dataclasses import asdict, dataclass
from fractions import Fraction
import math
import textwrap

from app.domain.caption_track import validate_caption_segments, _ass_timestamp, _ass_text
from app.domain.dependencies import content_fingerprint

ALGORITHM = "shorts-v1"
def ratio(t):
    t = Fraction(t)
    return [t.numerator, t.denominator]


def speech_interval(start, end, timing):
    start, end = Fraction(start), Fraction(end)
    if timing.intro and start < timing.insertion < end:
        raise ValueError("An excerpt cannot cross an inserted branding card.")
    return timing.point(start), end + (timing.intro if end > timing.insertion or start >= timing.insertion else 0)


def mapped_interval(start, end, timing, fps, boundaries):
    start, end, fps = Fraction(start), Fraction(end), Fraction(fps)
    if start not in boundaries or end not in boundaries or not 0 <= start < end <= timing.original_duration:
        raise ValueError("Choose ordered verified sentence/scene boundaries within the source.")
    if timing.intro and timing.insertion * fps % 1 and timing.insertion in {start, end}:
        raise ValueError("Choose another boundary: branding insertion is not an exact source video frame.")
    # Pad video by at most one frame; audio is trimmed separately at speech edges.
    a, b = speech_interval(start, end, timing)
    first, last = math.floor(a * fps), math.ceil(b * fps)
    a, b = Fraction(first, fps), min(Fraction(last, fps), timing.duration)
    if not 30 <= b - a <= 60:
        raise ValueError("The actual frame-aligned Short must be 30-60 seconds.")
    return a, b


def overlap(a, b):
    return max(Fraction(0), min(Fraction(*a["end"]), Fraction(*b["end"])) - max(Fraction(*a["start"]), Fraction(*b["start"])))


def clip_captions(segments, start, end):
    lo, hi = math.ceil(Fraction(start) * 1000), math.floor(Fraction(end) * 1000)
    result, seen = [], set()
    for s in segments:
        a, b = max(lo, s.start_ms) - lo, min(hi, s.end_ms) - lo
        key = (a, b, s.text)
        if a < b and key not in seen:
            result.append({"start_ms": a, "end_ms": b, "text": s.text}); seen.add(key)
    return validate_caption_segments(result) if result else ()


@dataclass(frozen=True)
class ShortStyle:
    mode: str = "fit"
    focal_points: tuple = ()  # (scene_id, normalized left-to-right focal point)
    captions: bool = False
    burn: bool = False
    font_size: int = 54
    margin: int = 320
    accent: str = "FFFFFF"
    hook: str = ""
    hook_seconds: int = 2

    def __post_init__(self):
        if (self.mode not in {"fill", "fit"} or type(self.captions) is not bool or type(self.burn) is not bool
                or self.burn and not self.captions or type(self.font_size) is not int or not 32 <= self.font_size <= 72
                or type(self.margin) is not int or not 280 <= self.margin <= 450
                or type(self.hook_seconds) is not int or not 1 <= self.hook_seconds <= 5
                or not isinstance(self.hook, str) or len(self.hook) > 100 or "\n" in self.hook
                or len(self.accent) != 6 or any(c not in "0123456789ABCDEFabcdef" for c in self.accent)):
            raise ValueError("Invalid safe Short presentation settings.")
        ids = []
        for scene, point in self.focal_points:
            if not isinstance(scene, str) or not scene or type(point) not in (int, float) or not math.isfinite(point) or not 0 <= point <= 1:
                raise ValueError("Crop focal points must reference a shot and be between 0 and 1.")
            ids.append(scene)
        if len(set(ids)) != len(ids): raise ValueError("Duplicate shot crop settings.")

    def to_payload(self):
        value = asdict(self); value["focal_points"] = [list(x) for x in self.focal_points]
        return value

    @classmethod
    def from_payload(cls, value):
        value = dict(value); value["focal_points"] = tuple(tuple(x) for x in value["focal_points"])
        return cls(**value)


def crop_box(width, height, focal):
    w = min(width, int(height * 9 / 16) // 2 * 2)
    h = min(height, int(width * 16 / 9) // 2 * 2)
    x = int(max(0, min(width - w, width * focal - w / 2))) // 2 * 2
    return w, h, x, (height - h) // 2 // 2 * 2


def overlays(style, segments):
    # Separate fixed zones; user text is escaped as ASS text, never filter code.
    header = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
    color = style.accent[4:6] + style.accent[2:4] + style.accent[:2]
    header += f"Style: Captions,Arial,{style.font_size},&H00{color},&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,2,2,100,160,{style.margin},1\n"
    header += "Style: Hook,Arial,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,8,100,160,240,1\n"
    header += "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    def wrapped(text, size):
        lines = textwrap.wrap(text, width=int(790 / (size * .57)), break_long_words=False)
        if len(lines) > 2 or any(len(line) > int(790 / (size * .57)) for line in lines): raise ValueError("Overlay exceeds two readable lines; reduce text or font size.")
        return _ass_text("\n".join(lines))
    events = []
    if style.burn:
        for s in segments:
            a, b = _ass_timestamp(s.start_ms, end=False), _ass_timestamp(s.end_ms, end=True)
            if s.end_ms // 10 <= (s.start_ms + 9) // 10: raise ValueError("Caption is too short for burn-in.")
            events.append(f"Dialogue: 0,{a},{b},Captions,,0,0,0,,{wrapped(s.text, style.font_size)}")
    if style.hook:
        events.append(f"Dialogue: 1,0:00:00.00,0:00:0{style.hook_seconds}.00,Hook,,0,0,0,," + r"{\fad(150,200)}" + wrapped(style.hook, 64))
    return header + "\n".join(events) + "\n"


def output_identity(source, timeline_id, edit, renderer, caption_identity):
    value = {"algorithm": ALGORITHM, "source_id": source.artifact_id, "source_checksum": source.checksum,
             "timeline_id": timeline_id, "edit": edit, "renderer": renderer, "caption_identity": caption_identity}
    return value, content_fingerprint(value)

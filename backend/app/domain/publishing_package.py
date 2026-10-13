"""Manual publishing contracts and exact composition timing; no upload behavior."""

from fractions import Fraction
import re

from app.domain.caption_track import validate_caption_segments
from app.domain.plan_script import planned_section_identity


TITLE_STYLES = ("curiosity", "search", "cinematic")
METADATA_SCHEMA = {
    "$id": "youtube_metadata_v1", "type": "object", "additionalProperties": False,
    "required": ["titles", "description", "keywords", "hashtags", "pinned_comment"],
    "properties": {
        "titles": {"type": "object", "additionalProperties": False, "required": list(TITLE_STYLES),
                   "properties": {k: {"type": "string", "minLength": 1, "maxLength": 100} for k in TITLE_STYLES}},
        "description": {"type": "string", "minLength": 1, "maxLength": 5000},
        "keywords": {"type": "array", "maxItems": 30, "items": {"type": "string", "minLength": 1, "maxLength": 80}},
        "hashtags": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "string", "pattern": "^#[\\w]+$"}},
        "pinned_comment": {"type": "string", "maxLength": 1000},
    },
}


def urls(text):
    return set(re.findall(r"(?:https?://|www\.)[^\s<>]+", text, re.I))


def validate_metadata(value, *, allowed_urls=(), generated_context=None):
    if type(value) is not dict or set(value) != set(METADATA_SCHEMA["required"]):
        raise ValueError("Metadata does not match the strict publishing schema.")
    titles = value["titles"]
    if type(titles) is not dict or set(titles) != set(TITLE_STYLES):
        raise ValueError("Provide exactly three differentiated title styles.")
    for title in titles.values():
        if type(title) is not str or not title.strip() or len(title) > 100 or "\n" in title or "\r" in title or "<" in title or ">" in title:
            raise ValueError("Each title must be a nonempty single line of at most 100 characters.")
    if len({t.casefold().strip() for t in titles.values()}) != 3:
        raise ValueError("Title alternatives must be distinct.")
    for name, limit, required in (("description", 5000, True), ("pinned_comment", 1000, False)):
        if type(value[name]) is not str or len(value[name]) > limit or required and not value[name].strip():
            raise ValueError(f"Invalid {name} text.")
    if len(value["description"].encode("utf-8")) > 5000 or "<" in value["description"] or ">" in value["description"]:
        raise ValueError("Invalid description: use at most 5,000 UTF-8 bytes without angle brackets.")
    for name, count, limit in (("keywords", 30, 80), ("hashtags", 3, 80)):
        items = value[name]
        if (type(items) is not list or len(items) > count
                or any(type(s) is not str or not s.strip() or len(s) > limit or "\n" in s for s in items)
                or len(set(items)) != len(items)):
            raise ValueError(f"Invalid {name} list.")
    if any(not re.fullmatch(r"#\w+", s) for s in value["hashtags"]):
        raise ValueError("Hashtags must be single #words.")
    tags = value["keywords"]
    if sum(len(s) + (2 if " " in s else 0) for s in tags) + max(0, len(tags) - 1) > 500:
        raise ValueError("Keywords exceed YouTube's combined 500-character tag limit.")
    text = "\n".join([*titles.values(), value["description"], value["pinned_comment"], *value["keywords"], *value["hashtags"]])
    if urls(text) - set(allowed_urls):
        raise ValueError("Unconfigured URLs in publication metadata; configure links explicitly first.")
    if generated_context is not None:
        if re.search(r"(?m)^\s*\d{1,3}:\d{2}(?::\d{2})?\b", value["description"]):
            raise ValueError("LLM chapter timestamps are forbidden; generate chapters from the Timeline.")
        if len(value["hashtags"]) != 3:
            raise ValueError("Generated metadata requires three hashtag suggestions.")
        numbers = lambda s: set(re.findall(r"\b\d+(?:[.,]\d+)?%?", s))
        if numbers(text) - numbers(generated_context):
            raise ValueError("Generated metadata introduced unsupported numeric claims. Review source facts and retry.")
    return value


def validate_chapters(chapters, duration):
    if type(chapters) is not list or len(chapters) < 3:
        raise ValueError("CHAPTERS: REVIEW REQUIRED — at least three chapters are required.")
    previous, titles = -1, set()
    for chapter in chapters:
        if type(chapter) is not dict or set(chapter) != {"seconds", "title"}:
            raise ValueError("Invalid chapter structure.")
        seconds, title = chapter["seconds"], chapter["title"]
        if type(seconds) is not int or seconds < 0 or seconds <= previous or seconds >= duration:
            raise ValueError("Chapter timestamps must ascend strictly within the selected video.")
        normalized = re.sub(r"\W+", "", title.casefold()) if type(title) is str else ""
        if not normalized or normalized in titles or len(title) > 160 or "\n" in title:
            raise ValueError("Chapter titles must be meaningful and distinct.")
        if previous >= 0 and seconds - previous < 10:
            raise ValueError("Each chapter must span at least 10 seconds.")
        titles.add(normalized); previous = seconds
    if chapters[0]["seconds"] != 0 or duration - previous < 10:
        raise ValueError("Chapters must start at 00:00 and the final chapter must span at least 10 seconds.")
    return chapters


def timestamp(seconds):
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def chapter_text(chapters):
    return "\n".join(f"{timestamp(c['seconds'])} {c['title']}" for c in chapters)


def parse_chapters(text):
    result = []
    for line in text.splitlines():
        if not line.strip(): continue
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"\d{2,}:\d{2}(?::\d{2})?", parts[0]):
            raise ValueError("Use MM:SS Title or HH:MM:SS Title for each chapter.")
        fields = list(map(int, parts[0].split(":")))
        if any(v >= 60 for v in fields[1:]): raise ValueError("Invalid chapter clock timestamp.")
        seconds = fields[0] * 60 + fields[1] if len(fields) == 2 else fields[0] * 3600 + fields[1] * 60 + fields[2]
        result.append({"seconds": seconds, "title": parts[1]})
    return result


class CompositionTiming:
    """Piecewise translation of original measured audio times around silent cards."""

    def __init__(self, original_duration, duration, *, intro=0, insertion=0, outro=0):
        self.original_duration, self.duration = Fraction(original_duration), Fraction(duration)
        self.intro, self.insertion, self.outro = Fraction(intro), Fraction(insertion), Fraction(outro)
        if (min(self.intro, self.insertion, self.outro) < 0 or self.insertion > self.original_duration
                or abs(self.duration - (self.original_duration + self.intro + self.outro)) > Fraction(1, 25)):
            raise ValueError("Missing or inconsistent verified composition timing.")

    def point(self, value):
        value = Fraction(value)
        if not 0 <= value <= self.original_duration: raise ValueError("Timing is outside original narration.")
        return value + (self.intro if value >= self.insertion else 0)

    def captions(self, segments):
        values = []
        for segment in segments:
            start, end = Fraction(segment.start_ms, 1000), Fraction(segment.end_ms, 1000)
            if self.intro and start < self.insertion < end:
                raise ValueError("Caption crosses the branding insertion boundary; timed export blocked.")
            shift = self.intro if start >= self.insertion else 0
            values.append({"start_ms": round((start + shift) * 1000), "end_ms": round((end + shift) * 1000), "text": segment.text})
        result = validate_caption_segments(values)
        if Fraction(result[-1].end_ms, 1000) > self.duration:
            raise ValueError("Adapted captions exceed selected video duration.")
        return result

    def to_payload(self):
        return {key: [getattr(self, key).numerator, getattr(self, key).denominator]
                for key in ("original_duration", "duration", "intro", "insertion", "outro")}


def timeline_chapters(plan, binding, script, timeline, timing):
    if plan is None or binding is None or binding.video_plan_revision_id != plan.id or binding.script_id != script.script_id:
        raise ValueError("CHAPTERS: REVIEW REQUIRED — a current plan/script binding is missing.")
    owners = {planned_section_identity(plan.project_id, section.id): group
              for group in plan.groups for section in group.sections}
    revisions = {s.section_id: s.id for s in script.sections}
    chapters, seen, last_group = [], set(), None
    for clip in timeline.clips:
        group = owners.get(clip.media.section_id)
        if group is None or revisions.get(clip.media.section_id) != clip.media.section_revision_id:
            raise ValueError("CHAPTERS: REVIEW REQUIRED — stale or unknown Timeline section mapping.")
        if group.id == last_group: continue
        if group.id in seen:
            raise ValueError("CHAPTERS: REVIEW REQUIRED — a narrative group is split/repeated in the Timeline.")
        seconds = 0 if not chapters else int(timing.point(clip.audio_offset))
        chapters.append({"seconds": seconds, "title": group.title})
        seen.add(group.id); last_group = group.id
    return validate_chapters(chapters, timing.duration)

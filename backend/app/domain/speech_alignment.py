"""Versioned word alignment bound to immutable section text and PCM audio."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import re


WORD_PATTERN = re.compile(r"\w+(?:[-'’]\w+)*", re.UNICODE)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_STATUSES = frozenset({"aligned", "low_confidence", "omitted"})


def source_words(text: str) -> tuple[tuple[int, int], ...]:
    if not isinstance(text, str) or not text:
        raise ValueError("Alignment source text is required.")
    return tuple((match.start(), match.end()) for match in WORD_PATTERN.finditer(text))


@dataclass(frozen=True, slots=True)
class AlignedWord:
    index: int
    source_start: int
    source_end: int
    sentence_id: str
    status: str
    start_frame: int | None = None
    end_frame: int | None = None
    confidence: float | None = None

    def __post_init__(self):
        if (type(self.index) is not int or self.index < 0
                or any(type(value) is not int for value in (self.source_start, self.source_end))
                or not 0 <= self.source_start < self.source_end
                or not isinstance(self.sentence_id, str) or not self.sentence_id.strip()
                or self.status not in _STATUSES):
            raise ValueError("Aligned word identity, source span or status is invalid.")
        if self.status == "omitted":
            if any(value is not None for value in (self.start_frame, self.end_frame, self.confidence)):
                raise ValueError("Omitted words cannot claim measured timing or confidence.")
            return
        if (type(self.start_frame) is not int or type(self.end_frame) is not int
                or not 0 <= self.start_frame < self.end_frame
                or (self.confidence is not None
                    and (type(self.confidence) not in (int, float)
                         or not 0 <= float(self.confidence) <= 1))):
            raise ValueError("Aligned words require a positive frame interval and bounded confidence.")


@dataclass(frozen=True, slots=True)
class SpeechAlignment:
    id: str
    project_id: str
    section_id: str
    revision_id: str
    text_checksum: str
    text_length: int
    audio_artifact_id: str
    audio_checksum: str
    sample_rate: int
    frame_count: int
    provider: str
    model: str
    language: str
    confidence_threshold: float
    words: tuple[AlignedWord, ...]
    unmatched_observed_count: int = 0
    method: str = "whisperx_forced_alignment_v1"

    def __post_init__(self):
        for value in (self.id, self.project_id, self.section_id, self.revision_id,
                      self.audio_artifact_id, self.provider, self.model, self.language):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Speech alignment identities are required.")
        if (_SHA256.fullmatch(self.text_checksum) is None
                or _SHA256.fullmatch(self.audio_checksum) is None
                or type(self.text_length) is not int or self.text_length <= 0
                or type(self.sample_rate) is not int or self.sample_rate <= 0
                or type(self.frame_count) is not int or self.frame_count <= 0
                or type(self.confidence_threshold) not in (int, float)
                or not 0 <= float(self.confidence_threshold) <= 1
                or type(self.unmatched_observed_count) is not int
                or self.unmatched_observed_count < 0
                or self.method != "whisperx_forced_alignment_v1"
                or not isinstance(self.words, tuple) or not self.words):
            raise ValueError("Speech alignment measurements or method are invalid.")
        previous_frame = 0
        for index, word in enumerate(self.words):
            if not isinstance(word, AlignedWord) or word.index != index or word.source_end > self.text_length:
                raise ValueError("Speech alignment words must be complete, ordered and indexed.")
            if word.status != "omitted":
                if word.start_frame < previous_frame or word.end_frame > self.frame_count:
                    raise ValueError("Speech alignment frames must be monotonic and within the WAV.")
                previous_frame = word.end_frame
                expected = ("low_confidence" if word.confidence is None
                            or word.confidence < self.confidence_threshold else "aligned")
                if word.status != expected:
                    raise ValueError("Speech alignment confidence status differs from its threshold.")

    @property
    def outcome(self) -> str:
        issues = []
        if any(word.status == "omitted" for word in self.words):
            issues.append("omissions")
        if any(word.status == "low_confidence" for word in self.words):
            issues.append("low_confidence")
        if self.unmatched_observed_count:
            issues.append("provider_insertions")
        return "_and_".join(issues) if issues else "complete"

    @property
    def coverage(self) -> float:
        return sum(word.status != "omitted" for word in self.words) / len(self.words)

    def validate_source(self, section, audio, sentence_spans):
        if ((section.project_id, section.section_id, section.id)
                != (self.project_id, self.section_id, self.revision_id)
                or self.text_checksum != sha256(section.text.encode("utf-8")).hexdigest()
                or self.text_length != len(section.text)
                or (audio.artifact_id, audio.checksum, audio.sample_rate, audio.frame_count)
                != (self.audio_artifact_id, self.audio_checksum, self.sample_rate, self.frame_count)):
            raise ValueError("Speech alignment differs from its section text or selected audio.")
        expected_words = source_words(section.text)
        if tuple((word.source_start, word.source_end) for word in self.words) != expected_words:
            raise ValueError("Speech alignment must account for every source word exactly once.")
        spans = tuple(sentence_spans)
        if not spans:
            raise ValueError("Speech alignment requires retained sentence spans.")
        for word in self.words:
            owners = [span for span in spans
                      if span.sentence_start <= word.source_start < word.source_end <= span.sentence_end]
            if len(owners) != 1 or owners[0].sentence_id != word.sentence_id:
                raise ValueError("Speech alignment word has an invalid sentence identity.")

    def to_payload(self):
        return {"version": 1, **asdict(self), "outcome": self.outcome, "coverage": self.coverage,
                "words": [asdict(word) for word in self.words]}

    @classmethod
    def from_payload(cls, value):
        data = dict(value)
        if data.pop("version", None) != 1:
            raise ValueError("Unsupported speech alignment version.")
        expected_outcome, expected_coverage = data.pop("outcome", None), data.pop("coverage", None)
        data["words"] = tuple(AlignedWord(**word) for word in data["words"])
        alignment = cls(**data)
        if expected_outcome != alignment.outcome or expected_coverage != alignment.coverage:
            raise ValueError("Speech alignment summary differs from its word outcomes.")
        return alignment

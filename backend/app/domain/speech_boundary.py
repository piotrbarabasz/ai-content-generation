"""Sentence identity and measured block coverage, deliberately not word alignment."""

from dataclasses import asdict, dataclass, replace
from hashlib import sha256


@dataclass(frozen=True)
class SourceSpan:
    sentence_id: str
    sentence_start: int
    sentence_end: int
    start: int
    end: int


@dataclass(frozen=True)
class SpeechChunkBoundary:
    chunk_id: str
    source: SourceSpan
    start_frame: int
    end_frame: int


@dataclass(frozen=True)
class SpeechBlock:
    sentence_id: str
    source_start: int
    source_end: int
    start_frame: int
    end_frame: int
    chunk_ids: tuple[str, ...]


@dataclass(frozen=True)
class SpeechBoundaryMap:
    text_checksum: str
    text_length: int
    audio_checksum: str
    sample_rate: int
    frame_count: int
    chunks: tuple[SpeechChunkBoundary, ...]
    method: str = "measured_chunk_assembly"
    quality: str = "measured_sentence_blocks"
    source_audio_checksum: str | None = None

    def __post_init__(self):
        if (type(self.sample_rate) is not int or self.sample_rate <= 0
                or type(self.frame_count) is not int or self.frame_count <= 0
                or type(self.text_length) is not int or self.text_length <= 0
                or not isinstance(self.chunks, tuple) or not self.chunks):
            raise ValueError("Boundary map requires positive text and PCM measurements.")
        for checksum in (self.text_checksum, self.audio_checksum):
            if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
                raise ValueError("Boundary map requires SHA-256 identities.")
        if (self.method, self.quality) == ("measured_chunk_assembly", "measured_sentence_blocks"):
            if self.source_audio_checksum is not None:
                raise ValueError("Raw map cannot claim a tempo source.")
        elif (self.method, self.quality) == ("measured_duration_ratio", "approximate_internal_positions"):
            if (not isinstance(self.source_audio_checksum, str) or len(self.source_audio_checksum) != 64
                    or any(c not in "0123456789abcdef" for c in self.source_audio_checksum)):
                raise ValueError("Tempo map requires its source checksum.")
        else:
            raise ValueError("Unknown speech boundary method/quality.")
        text_cursor = frame_cursor = 0
        seen_chunks, seen_sentences = set(), set()
        previous = None
        for chunk in self.chunks:
            span = chunk.source
            if (not chunk.chunk_id or chunk.chunk_id in seen_chunks or not span.sentence_id
                    or any(type(n) is not int for n in (span.start, span.end, span.sentence_start,
                                                       span.sentence_end, chunk.start_frame, chunk.end_frame))
                    or span.start != text_cursor or chunk.start_frame != frame_cursor
                    or not 0 <= span.sentence_start <= span.start < span.end <= span.sentence_end <= self.text_length
                    or not chunk.start_frame < chunk.end_frame <= self.frame_count):
                raise ValueError("Speech boundaries contain gaps, overlaps or invalid spans.")
            if previous is None or span.sentence_id != previous.sentence_id:
                if (span.sentence_id in seen_sentences or span.start != span.sentence_start
                        or previous is not None and previous.end != previous.sentence_end):
                    raise ValueError("Sentence coverage must be complete and contiguous.")
                seen_sentences.add(span.sentence_id)
            elif (span.sentence_start, span.sentence_end) != (previous.sentence_start, previous.sentence_end):
                raise ValueError("Technical subchunks must retain one sentence span.")
            seen_chunks.add(chunk.chunk_id)
            text_cursor, frame_cursor, previous = span.end, chunk.end_frame, span
        if text_cursor != self.text_length or frame_cursor != self.frame_count or previous.end != previous.sentence_end:
            raise ValueError("Speech boundaries must cover the entire text and WAV.")

    @property
    def blocks(self) -> tuple[SpeechBlock, ...]:
        blocks = []
        for chunk in self.chunks:
            span = chunk.source
            if blocks and blocks[-1].sentence_id == span.sentence_id:
                blocks[-1] = replace(blocks[-1], end_frame=chunk.end_frame,
                                     chunk_ids=blocks[-1].chunk_ids + (chunk.chunk_id,))
            else:
                blocks.append(SpeechBlock(span.sentence_id, span.sentence_start, span.sentence_end,
                                          chunk.start_frame, chunk.end_frame, (chunk.chunk_id,)))
        return tuple(blocks)

    def validate_source(self, text: str, checksum: str, sample_rate: int, frame_count: int):
        if (self.text_checksum != sha256(text.encode("utf-8")).hexdigest() or self.text_length != len(text)
                or (self.audio_checksum, self.sample_rate, self.frame_count) != (checksum, sample_rate, frame_count)):
            raise ValueError("Speech map differs from its section text or measured WAV.")

    def retime(self, *, checksum: str, sample_rate: int, frame_count: int):
        if sample_rate != self.sample_rate or type(frame_count) is not int or frame_count <= 0:
            raise ValueError("Tempo mapping requires compatible PCM measurements.")
        if self.method != "measured_chunk_assembly":
            raise ValueError("Tempo mapping requires the original measured map.")
        def scale(frame):
            # Integer half-up rounding shared by both sides of each boundary.
            return (2 * frame * frame_count + self.frame_count) // (2 * self.frame_count)
        return replace(self, audio_checksum=checksum, frame_count=frame_count,
                       chunks=tuple(replace(c, start_frame=scale(c.start_frame), end_frame=scale(c.end_frame))
                                    for c in self.chunks), method="measured_duration_ratio",
                       quality="approximate_internal_positions", source_audio_checksum=self.audio_checksum)

    def to_payload(self):
        return {"version": 1, **asdict(self), "chunks": [asdict(c) for c in self.chunks]}

    @classmethod
    def from_payload(cls, payload):
        data = dict(payload)
        if data.pop("version") != 1:
            raise ValueError("Unsupported speech boundary map version.")
        data["chunks"] = tuple(SpeechChunkBoundary(c["chunk_id"], SourceSpan(**c["source"]),
                                                  c["start_frame"], c["end_frame"]) for c in data["chunks"])
        return cls(**data)

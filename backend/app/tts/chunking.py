"""Deterministic, provider-neutral technical narration chunking.

This module deliberately knows nothing about scenes or TTS providers.  Its
offsets refer to the normalized narration returned by :func:`normalize_narration`.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re


_WHITESPACE = re.compile(r"\s+")
_WORD = re.compile(r"\S+")
_PARAGRAPH = re.compile(r"(?:\r?\n\s*){2,}")
_ABBREVIATIONS = frozenset(
    {
        "al.", "dr.", "godz.", "inż.", "itd.", "itp.", "m.in.", "mgr.",
        "np.", "nr.", "p.n.e.", "prof.", "r.", "str.", "tj.", "tzn.",
        "ul.", "św.", "vs.", "zob.",
    }
)


@dataclass(frozen=True, slots=True)
class NarrationChunkingSettings:
    """The request-size setting for technical chunking.

    ``max_words`` must be positive.  A sentence exceeding it is split on word
    boundaries and each resulting record is explicitly marked as oversized.
    """

    max_words: int = 120

    def __post_init__(self) -> None:
        if not isinstance(self.max_words, int) or isinstance(self.max_words, bool):
            raise TypeError("max_words must be an integer.")
        if self.max_words < 1:
            raise ValueError("max_words must be at least 1.")


@dataclass(frozen=True, slots=True)
class NarrationChunk:
    """One stable technical TTS request, located in normalized narration."""

    id: str
    index: int
    text: str
    source_start: int
    source_end: int
    word_count: int
    text_hash: str
    is_oversized: bool = False

    @property
    def source_offset(self) -> int:
        """Compatibility-friendly singular spelling for the start offset."""
        return self.source_start


def normalize_narration(text: str) -> str:
    """Collapse whitespace without changing wording, punctuation, or Unicode."""
    if not isinstance(text, str):
        raise TypeError("narration text must be a string.")
    return _WHITESPACE.sub(" ", text).strip()


def chunk_narration(
    text: str,
    settings: NarrationChunkingSettings | None = None,
    *,
    max_words: int | None = None,
) -> list[NarrationChunk]:
    """Return stable chunks whose joined text equals normalized *text*.

    Paragraph boundaries are considered before sentence boundaries.  When a
    sentence cannot fit, it is split only between words; this is deterministic
    and preserves every character of the normalized narration.
    """
    if settings is not None and max_words is not None:
        raise ValueError("Use either settings or max_words, not both.")
    active_settings = settings or NarrationChunkingSettings(
        max_words=120 if max_words is None else max_words
    )
    if not isinstance(active_settings, NarrationChunkingSettings):
        raise TypeError("settings must be NarrationChunkingSettings or None.")

    normalized = normalize_narration(text)
    if not normalized:
        return []

    combined: list[tuple[str, bool]] = []
    # Process every paragraph separately: a paragraph is preferred even when
    # combining its sentences would otherwise leave room in the prior chunk.
    for paragraph_units in _paragraph_then_sentence_units(text):
        pieces: list[tuple[str, bool]] = []
        for unit in paragraph_units:
            if _count_words(unit) <= active_settings.max_words:
                pieces.append((unit, False))
            else:
                pieces.extend((part, True) for part in _split_oversized(unit, active_settings.max_words))
        paragraph_combined: list[tuple[str, bool]] = []
        for piece, oversized in pieces:
            if not paragraph_combined:
                paragraph_combined.append((piece, oversized))
                continue
            previous, previous_oversized = paragraph_combined[-1]
            if not oversized and not previous_oversized and (
                _count_words(previous) + _count_words(piece) <= active_settings.max_words
            ):
                paragraph_combined[-1] = (f"{previous} {piece}", False)
            else:
                paragraph_combined.append((piece, oversized))
        combined.extend(paragraph_combined)

    records: list[NarrationChunk] = []
    cursor = 0
    for index, (chunk_text, oversized) in enumerate(combined):
        start = normalized.find(chunk_text, cursor)
        # The invariant is useful both for future maintainers and accidental
        # changes to the sentence parser: source offsets must never be guessed.
        if start < 0:  # pragma: no cover - defensive invariant
            raise RuntimeError("Chunk text no longer matches normalized narration.")
        end = start + len(chunk_text)
        digest = sha256(chunk_text.encode("utf-8")).hexdigest()
        records.append(
            NarrationChunk(
                id=f"chunk-{index:04d}-{digest[:12]}",
                index=index,
                text=chunk_text,
                source_start=start,
                source_end=end,
                word_count=_count_words(chunk_text),
                text_hash=digest,
                is_oversized=oversized,
            )
        )
        cursor = end
    return records


def _paragraph_then_sentence_units(text: str) -> list[list[str]]:
    paragraphs = [normalize_narration(part) for part in _PARAGRAPH.split(text)]
    units: list[list[str]] = []
    for paragraph in paragraphs:
        if paragraph:
            units.append(_sentences(paragraph))
    return units


def _sentences(paragraph: str) -> list[str]:
    boundaries: list[int] = []
    for match in re.finditer(r"[.!?]+(?:[»”\"']+)?(?=\s|$)", paragraph):
        end = match.end()
        token = paragraph[:end].rsplit(None, 1)[-1].lower()
        marker = match.group(0)
        if marker.startswith(".") and _period_is_internal_or_abbreviation(paragraph, match.start(), token):
            continue
        boundaries.append(end)

    result: list[str] = []
    start = 0
    for end in boundaries:
        sentence = paragraph[start:end].strip()
        if sentence:
            result.append(sentence)
        start = end
    tail = paragraph[start:].strip()
    if tail:
        result.append(tail)
    return result or [paragraph]


def _period_is_internal_or_abbreviation(paragraph: str, position: int, token: str) -> bool:
    before = paragraph[position - 1] if position else ""
    after = paragraph[position + 1] if position + 1 < len(paragraph) else ""
    if before.isdigit() and after.isdigit():
        return True
    if token in _ABBREVIATIONS:
        return True
    # Initials and chained abbreviations, e.g. "A. Kowalski" or "m.in.".
    return bool(re.fullmatch(r"(?:[\wÀ-ſ]\.){1,}", token, flags=re.UNICODE))


def _split_oversized(text: str, max_words: int) -> list[str]:
    words = _WORD.findall(text)
    return [" ".join(words[index : index + max_words]) for index in range(0, len(words), max_words)]


def _count_words(text: str) -> int:
    return len(_WORD.findall(text))

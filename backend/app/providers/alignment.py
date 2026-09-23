"""Provider-neutral known-text alignment boundary."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ProviderAlignedWord:
    source_start: int
    source_end: int
    start_seconds: float | None
    end_seconds: float | None
    confidence: float | None

    def __post_init__(self):
        if (type(self.source_start) is not int or type(self.source_end) is not int
                or not 0 <= self.source_start < self.source_end):
            raise ValueError("Provider alignment source span is invalid.")
        timing = (self.start_seconds, self.end_seconds, self.confidence)
        if all(value is None for value in timing):
            return
        if (type(self.start_seconds) not in (int, float)
                or type(self.end_seconds) not in (int, float)
                or not math.isfinite(self.start_seconds) or not math.isfinite(self.end_seconds)
                or not 0 <= self.start_seconds < self.end_seconds
                or (self.confidence is not None
                    and (type(self.confidence) not in (int, float)
                         or not math.isfinite(self.confidence)
                         or not 0 <= self.confidence <= 1))):
            raise ValueError("Provider alignment timing or confidence is invalid.")


@dataclass(frozen=True, slots=True)
class ProviderAlignmentResult:
    words: tuple[ProviderAlignedWord, ...]
    unmatched_observed_count: int = 0

    def __post_init__(self):
        if (not isinstance(self.words, tuple) or not self.words
                or any(not isinstance(word, ProviderAlignedWord) for word in self.words)
                or type(self.unmatched_observed_count) is not int
                or self.unmatched_observed_count < 0):
            raise ValueError("Provider alignment result is invalid.")


@runtime_checkable
class AlignmentProvider(Protocol):
    provider_name: str
    model_name: str

    def align(self, audio_bytes: bytes, text: str, language: str) -> ProviderAlignmentResult: ...

    def effective_alignment_identity(self) -> dict: ...


__all__ = ["AlignmentProvider", "ProviderAlignedWord", "ProviderAlignmentResult"]

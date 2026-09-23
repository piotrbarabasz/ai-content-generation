"""WhisperX result adapter with exact known-text coverage and no model loading."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import math
import unicodedata

from app.domain.speech_alignment import WORD_PATTERN, source_words
from .alignment import ProviderAlignedWord, ProviderAlignmentResult


class WhisperXAlignmentError(ValueError):
    pass


def _normalized(value):
    if not isinstance(value, str):
        return None
    matches = WORD_PATTERN.findall(unicodedata.normalize("NFKC", value).casefold())
    return matches[0] if len(matches) == 1 else None


def _seconds(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise WhisperXAlignmentError(f"WhisperX {name} must be finite nonnegative seconds.")
    return float(value)


def _score(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise WhisperXAlignmentError("WhisperX word score must be between zero and one.")
    return float(value)


class WhisperXAlignmentAdapter:
    """Normalize one explicitly managed WhisperX backend result.

    The backend owns model/runtime execution and returns WhisperX-compatible
    ``segments[].words[]`` data. This adapter never downloads or imports models.
    """

    provider_name = "whisperx"

    def __init__(self, backend: Callable[[bytes, str, str], Mapping], *,
                 model_name="whisperx-align", runtime_version="unreported"):
        if not callable(backend) or not all(isinstance(value, str) and value.strip()
                                            for value in (model_name, runtime_version)):
            raise ValueError("WhisperX alignment requires a managed backend and model identity.")
        self.backend = backend
        self.model_name = model_name.strip()
        self.runtime_version = runtime_version.strip()

    def effective_alignment_identity(self):
        return {"provider": self.provider_name, "model": self.model_name,
                "runtime_version": self.runtime_version,
                "adapter": "known-text-lcs-v1"}

    def align(self, audio_bytes, text, language):
        if not isinstance(audio_bytes, bytes) or not audio_bytes:
            raise WhisperXAlignmentError("WhisperX alignment requires audio bytes.")
        if not isinstance(text, str) or not text or not isinstance(language, str) or not language.strip():
            raise WhisperXAlignmentError("WhisperX alignment requires known text and language.")
        expected_spans = source_words(text)
        if not expected_spans:
            raise WhisperXAlignmentError("Known text has no alignable words.")
        try:
            payload = self.backend(audio_bytes, text, language.strip())
        except Exception as exc:
            raise WhisperXAlignmentError("Managed WhisperX alignment failed.") from exc
        if not isinstance(payload, Mapping) or not isinstance(payload.get("segments"), list):
            raise WhisperXAlignmentError("WhisperX returned an invalid segment payload.")
        observed = []
        for segment in payload["segments"]:
            if not isinstance(segment, Mapping) or not isinstance(segment.get("words"), list):
                raise WhisperXAlignmentError("WhisperX returned an invalid word list.")
            for word in segment["words"]:
                if not isinstance(word, Mapping):
                    raise WhisperXAlignmentError("WhisperX returned an invalid word entry.")
                raw_word = word.get("word")
                tokens = (WORD_PATTERN.findall(
                    unicodedata.normalize("NFKC", raw_word).casefold())
                    if isinstance(raw_word, str) else [])
                if not tokens:
                    continue
                if len(tokens) != 1:
                    raise WhisperXAlignmentError("WhisperX word entries must contain one lexical token.")
                normalized = tokens[0]
                timing = (word.get("start"), word.get("end"), word.get("score"))
                if timing[0] is None and timing[1] is None:
                    if timing[2] is not None:
                        raise WhisperXAlignmentError("WhisperX untimed word cannot claim confidence.")
                    observed.append((normalized, None, None, None))
                    continue
                if timing[0] is None or timing[1] is None:
                    raise WhisperXAlignmentError("WhisperX word timing metadata is incomplete.")
                start, end = _seconds(timing[0], "word start"), _seconds(timing[1], "word end")
                if end <= start:
                    raise WhisperXAlignmentError("WhisperX word intervals must be positive.")
                observed.append((normalized, start, end,
                                 None if timing[2] is None else _score(timing[2])))
        expected = [_normalized(text[start:end]) for start, end in expected_spans]
        rows, columns = len(expected), len(observed)
        lengths = [[0] * (columns + 1) for _ in range(rows + 1)]
        for i in range(rows - 1, -1, -1):
            for j in range(columns - 1, -1, -1):
                lengths[i][j] = (1 + lengths[i + 1][j + 1] if expected[i] == observed[j][0]
                                 else max(lengths[i + 1][j], lengths[i][j + 1]))
        matched, i, j = {}, 0, 0
        while i < rows and j < columns:
            if expected[i] == observed[j][0] and lengths[i][j] == 1 + lengths[i + 1][j + 1]:
                matched[i] = j
                i, j = i + 1, j + 1
            elif lengths[i + 1][j] >= lengths[i][j + 1]:
                i += 1
            else:
                j += 1
        words = []
        for index, (start, end) in enumerate(expected_spans):
            if index not in matched:
                words.append(ProviderAlignedWord(start, end, None, None, None))
            else:
                _, begin, finish, confidence = observed[matched[index]]
                words.append(ProviderAlignedWord(start, end, begin, finish, confidence))
        return ProviderAlignmentResult(tuple(words), columns - len(matched))


__all__ = ["WhisperXAlignmentAdapter", "WhisperXAlignmentError"]

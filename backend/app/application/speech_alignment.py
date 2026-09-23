"""Validate known-text alignment before immutable project publication."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256

from app.domain.base import new_id
from app.domain.dependencies import InputEdge, RequestFingerprint, content_fingerprint
from app.domain.speech_alignment import AlignedWord, SpeechAlignment
from app.providers.alignment import ProviderAlignmentResult


class SpeechAlignmentService:
    def __init__(self, artifacts, provider, *, confidence_threshold=0.6):
        if (type(confidence_threshold) not in (int, float)
                or not 0 <= float(confidence_threshold) <= 1):
            raise ValueError("Alignment confidence threshold must be between zero and one.")
        self.artifacts, self.provider = artifacts, provider
        self.confidence_threshold = float(confidence_threshold)

    def align(self, section, audio, *, language):
        if not isinstance(language, str) or not language.strip():
            raise ValueError("Speech alignment language is required.")
        language = language.strip()
        audio_bytes, sentence_spans, audio_edge = self.artifacts.inputs(section, audio)
        result = self.provider.align(audio_bytes, section.text, language)
        if not isinstance(result, ProviderAlignmentResult):
            raise ValueError("Alignment provider returned an invalid result.")
        identity = self.provider.effective_alignment_identity()
        if (not isinstance(identity, dict)
                or identity.get("provider") != self.provider.provider_name
                or identity.get("model") != self.provider.model_name):
            raise ValueError("Alignment provider identity is invalid.")
        owners = []
        for item in result.words:
            matches = [span for span in sentence_spans
                       if span.sentence_start <= item.source_start < item.source_end <= span.sentence_end]
            if len(matches) != 1:
                raise ValueError("Aligned word does not belong to one retained sentence.")
            owners.append(matches[0].sentence_id)
        words = []
        for index, (item, sentence_id) in enumerate(zip(result.words, owners)):
            if item.start_seconds is None or item.end_seconds is None:
                if any(value is not None for value in (item.start_seconds, item.end_seconds)):
                    raise ValueError("Omitted alignment word has incomplete timing metadata.")
                words.append(AlignedWord(index, item.source_start, item.source_end,
                                         sentence_id, "omitted"))
                continue
            start = int(item.start_seconds * audio.sample_rate + 0.5)
            end = int(item.end_seconds * audio.sample_rate + 0.5)
            status = ("low_confidence" if item.confidence is None
                      or item.confidence < self.confidence_threshold else "aligned")
            words.append(AlignedWord(index, item.source_start, item.source_end, sentence_id,
                                     status, start, end, item.confidence))
        alignment = SpeechAlignment(
            new_id("speech_alignment"), section.project_id, section.section_id, section.id,
            sha256(section.text.encode("utf-8")).hexdigest(), len(section.text),
            audio.artifact_id, audio.checksum, audio.sample_rate, audio.frame_count,
            self.provider.provider_name, self.provider.model_name, language,
            self.confidence_threshold, tuple(words), result.unmatched_observed_count)
        alignment.validate_source(section, audio, sentence_spans)
        request = RequestFingerprint.create(
            "speech_alignment.align", "1",
            inputs=(InputEdge("section_revision", f"section:{section.section_id}:revision",
                              content_fingerprint(asdict(section))), audio_edge),
            settings={"language": language, "confidence_threshold": self.confidence_threshold},
            effective_identity=identity)
        self.artifacts.save(section, audio, alignment, request)
        return alignment


__all__ = ["SpeechAlignmentService"]

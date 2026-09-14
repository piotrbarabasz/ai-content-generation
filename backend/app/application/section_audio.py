"""One immutable editorial section, injected voice/output ports and D040 publication."""

from typing import Protocol

from app.domain.dependencies import RequestFingerprint
from app.domain.narrative_segment import SectionRevision


OPERATION = "section_audio.synthesize"
VERSION = "2"  # Sentence-isolated synthesis with measured source boundaries.


class SectionVoicePort(Protocol):
    def prepare(self, selection: dict, max_words: int) -> dict: ...


class SectionOutputPort(Protocol):
    def validated(self, job): ...  # Context manager yielding (stream, SectionAudio metadata).


class SectionAudioService:
    def __init__(self, publication, jobs, voices: SectionVoicePort, outputs: SectionOutputPort):
        self.publication, self.jobs, self.voices, self.outputs = publication, jobs, voices, outputs

    def enqueue(self, section: SectionRevision, selection: dict, *, max_words: int = 120):
        if not isinstance(section, SectionRevision) or not section.text.strip():
            raise ValueError("Section audio requires a nonempty immutable section revision.")
        if type(max_words) is not int or not 1 <= max_words <= 1000:
            raise ValueError("Technical chunk size must be between 1 and 1000 words.")
        prepared = self.voices.prepare(selection, max_words)
        request = RequestFingerprint.create(OPERATION, VERSION, settings=prepared,
                                            effective_identity=prepared["effective_identity"])
        return self.publication.enqueue("section:" + section.section_id + ":audio:raw", request,
                                        expected_sections={section.section_id: section.id},
                                        inputs={"section_audio": prepared})

    def complete(self, claim, reported_references=()):
        """Trusted D007 completion hook; never resolve paths from worker references."""
        existing = self.publication.repository.result(claim)
        if existing is not None:
            return existing
        job = self.publication.repository.prepare(claim)
        if job.request.operation != OPERATION or job.request.algorithm_version not in ("1", VERSION):
            raise ValueError("This completion handler only accepts section audio jobs.")
        with self.outputs.validated(job) as (stream, audio):
            return self.publication.publish(claim, "section-audio.wav", stream,
                                            metadata={"section_audio": audio})

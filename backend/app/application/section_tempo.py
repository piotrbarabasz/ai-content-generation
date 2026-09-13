"""Tempo derivatives consume retained raw audio, never a TTS provider."""

from typing import Protocol

from app.domain.dependencies import InputEdge, RequestFingerprint
from app.domain.generation_job import AttemptStatus
from app.domain.section_audio import SectionAudio


OPERATION = "section_audio.tempo"


class TempoArtifactsPort(Protocol):
    def raw(self, section, artifact_id: str) -> SectionAudio: ...
    def settings(self, raw: SectionAudio, tempo) -> dict: ...
    def processed(self, job): ...
    def selected(self, section, variant: str) -> SectionAudio | None: ...


class SectionTempoService:
    def __init__(self, publication, coordinator, artifacts: TempoArtifactsPort):
        self.publication, self.coordinator, self.artifacts = publication, coordinator, artifacts

    def enqueue(self, section, raw_artifact_id: str, tempo):
        raw = self.artifacts.raw(section, raw_artifact_id)
        settings = self.artifacts.settings(raw, tempo)
        edge = InputEdge.artifact("raw_audio", "section:" + section.section_id + ":audio:raw",
                                  raw.artifact_id, raw.checksum)
        request = RequestFingerprint.create(OPERATION, "1", inputs=[edge], settings=settings,
                                            effective_identity={"processor_version": settings["processor_version"]})
        return self.publication.enqueue("section:" + section.section_id + ":audio:processed", request,
                                        expected_sections={section.section_id: section.id},
                                        inputs={"section_tempo": settings})

    def run(self, claim):
        """Run on the owning coordinator outside the UI thread; FFmpeg is a child.

        Claim/retry/cancel remain D006 commands. The gate decides selection only
        after processing; its committed success survives journal-cleanup failure.
        """
        existing = self.publication.repository.result(claim)
        if existing is not None:
            return existing
        job = self.coordinator.repository.get_job(claim.job_id)
        if job.request.operation != OPERATION or job.request.algorithm_version != "1":
            raise ValueError("Tempo service only accepts tempo-derivative jobs.")
        try:
            self.publication.repository.prepare(claim)
            with self.artifacts.processed(job) as (stream, metadata):
                return self.publication.publish(claim, "section-tempo.wav", stream, metadata=metadata)
        except Exception as exc:
            actual = self.coordinator.repository.get_attempt(claim.id)
            if actual.status == AttemptStatus.COMPLETED:
                return self.publication.repository.result(claim)
            if actual.status == AttemptStatus.RUNNING:
                if actual.cancel_requested:
                    self.coordinator.acknowledge_cancel(claim)
                else:
                    self.coordinator.fail(claim, f"tempo_publication: {type(exc).__name__}: {str(exc)[:1024]}")
            raise

    def selected(self, section, *, variant="original"):
        """Choose original or processed explicitly; never silently fall back."""
        return self.artifacts.selected(section, variant)

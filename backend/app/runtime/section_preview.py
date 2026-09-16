"""Managed Piper preview through the existing D007/D010 worker boundary."""

import asyncio
from dataclasses import replace
import shutil

from app.application.section_audio import OPERATION, VERSION
from app.domain.base import new_id, utc_now
from app.domain.dependencies import RequestFingerprint, canonical_json
from app.domain.enums import ProviderType
from app.domain.generation_job import AttemptStatus, JobAttempt, JobProgress, JobRequest
from app.domain.narrative_segment import SectionRevision
from app.domain.publication import PUBLICATION_KEY, PublicationSnapshot
from app.providers.tts_capabilities import TTSCapabilities
from app.providers.tts_result import TTSSynthesisResult
from app.tts.assembly import inspect_pcm_wav
from .section_synthesis import workspace
from .supervisor import WorkerSupervisor


class _OneJobCoordinator:
    """Queue-shaped state for one private preview worker invocation."""

    repository = None

    def __init__(self, job):
        self.repository = self
        self.job = job
        now = utc_now()
        self.attempt = JobAttempt(new_id("attempt"), job.id, 1, AttemptStatus.QUEUED, now, now)

    def claim_next(self, owner):
        if self.attempt.status != AttemptStatus.QUEUED:
            return None
        now = utc_now()
        self.attempt = replace(self.attempt, status=AttemptStatus.RUNNING, updated_at=now,
                               started_at=now, owner=owner, claim_token=new_id("claim"))
        return self.attempt

    def get_job(self, job_id):
        if job_id != self.job.id:
            raise KeyError(job_id)
        return self.job

    def get_attempt(self, attempt_id):
        if attempt_id != self.attempt.id:
            raise KeyError(attempt_id)
        return self.attempt

    def progress(self, claim, progress):
        if not isinstance(progress, JobProgress) or claim.id != self.attempt.id:
            raise ValueError("Preview progress does not belong to this attempt.")
        self.attempt = replace(self.attempt, progress=progress, updated_at=utc_now())
        return self.attempt

    def cancel(self, attempt_id):
        if attempt_id != self.attempt.id:
            raise KeyError(attempt_id)
        self.attempt = replace(self.attempt, cancel_requested=True, updated_at=utc_now())
        return self.attempt

    def _finish(self, claim, status, *, error=""):
        if claim.id != self.attempt.id:
            raise ValueError("Preview outcome does not belong to this attempt.")
        now = utc_now()
        self.attempt = replace(self.attempt, status=status, updated_at=now, finished_at=now,
                               error=error, output_artifact_ids=())
        return self.attempt

    def complete(self, claim, output_artifact_ids=()):
        if output_artifact_ids:
            raise ValueError("Preview workers do not publish artifacts.")
        return self._finish(claim, AttemptStatus.COMPLETED)

    def fail(self, claim, error):
        return self._finish(claim, AttemptStatus.FAILED, error=error)

    def acknowledge_cancel(self, claim):
        return self._finish(claim, AttemptStatus.CANCELED)


class ManagedSectionPreviewProvider:
    """TTSProvider facade whose synthesis always runs in the managed runtime."""

    provider_type = ProviderType.TTS
    provider_name = "piper"

    def __init__(self, managed, launch, prepared):
        self.managed, self.launch, self.prepared = managed, launch, prepared

    def capabilities(self):
        selection = self.prepared["selection"]
        return TTSCapabilities("piper", (selection["language"],), ("builtin",), False, False)

    def effective_synthesis_identity(self, voice_config=None):
        if canonical_json(dict(voice_config or {})) != canonical_json(self.prepared["voice_config"]):
            raise ValueError("Preview voice configuration differs from production.")
        return self.prepared["effective_identity"]["synthesis"]

    def synthesize(self, text, voice_config=None):
        self.effective_synthesis_identity(voice_config)
        section = SectionRevision.create(project_id="managed-preview", title="Voice preview",
                                         text=text, role="narration")
        snapshot = PublicationSnapshot("managed-preview", new_id("preview_generation"), (section,))
        request = snapshot.bind(RequestFingerprint.create(
            OPERATION, VERSION, settings=self.prepared,
            effective_identity=self.prepared["effective_identity"]))
        job = JobRequest(new_id("preview_job"), "preview:audio", request, canonical_json({
            PUBLICATION_KEY: snapshot.to_payload(), "inputs": {"section_audio": self.prepared}}), utc_now())
        coordinator = _OneJobCoordinator(job)
        result = asyncio.run(WorkerSupervisor(coordinator, self.launch).run_next("desktop-preview"))
        if result is None or result.attempt.status != AttemptStatus.COMPLETED:
            detail = "worker did not run" if result is None else result.attempt.error
            raise RuntimeError(f"Managed preview failed: {detail}")
        directory = workspace(self.managed.work_root, job)
        try:
            with self.managed.validated(job) as (source, _metadata):
                payload = source.read()
            parameters, _ = inspect_pcm_wav(payload)
            return TTSSynthesisResult(payload, parameters.sample_rate, parameters.duration_seconds,
                                      "wav", self.provider_name,
                                      {"effective_synthesis_identity": self.effective_synthesis_identity(voice_config)})
        finally:
            shutil.rmtree(directory, ignore_errors=True)


__all__ = ["ManagedSectionPreviewProvider"]

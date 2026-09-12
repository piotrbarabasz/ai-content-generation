"""Presentation/provider-independent commands with an injected queue and clock."""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Protocol

from app.domain.base import new_id, utc_now
from app.domain.dependencies import RequestFingerprint, canonical_json
from app.domain.generation_job import JobAttempt, JobProgress, JobRequest, AttemptStatus, artifact_ids


class JobRepositoryPort(Protocol):
    def enqueue(self, job: JobRequest) -> JobAttempt: ...
    def claim_next(self, owner: str, now: datetime) -> JobAttempt | None: ...
    def get_job(self, job_id: str) -> JobRequest: ...
    def get_attempt(self, attempt_id: str) -> JobAttempt: ...
    def attempts(self, job_id: str) -> tuple[JobAttempt, ...]: ...
    def set_paused(self, paused: bool) -> None: ...
    def cancel(self, attempt_id: str, now: datetime) -> JobAttempt: ...
    def retry(self, job_id: str, now: datetime) -> JobAttempt: ...
    def report_progress(self, attempt_id: str, token: str, progress: JobProgress, now: datetime) -> JobAttempt: ...
    def finish(self, attempt_id: str, token: str, status: AttemptStatus, now: datetime, *,
               output_artifact_ids: Sequence[str] = (), error: str = "") -> JobAttempt: ...


class JobCoordinator:
    def __init__(self, repository: JobRepositoryPort, *, clock: Callable[[], datetime] = utc_now):
        self.repository = repository
        self.clock = clock

    def enqueue(self, output_key: str, request: RequestFingerprint, input_snapshot: Mapping,
                *, prior_artifact_ids: Sequence[str] = ()) -> JobAttempt:
        job = JobRequest(new_id("job"), output_key, request, canonical_json(dict(input_snapshot)),
                         self.clock(), artifact_ids(prior_artifact_ids))
        return self.repository.enqueue(job)

    def claim_next(self, owner: str) -> JobAttempt | None:
        return self.repository.claim_next(owner, self.clock())

    def pause(self) -> None:
        self.repository.set_paused(True)

    def resume(self) -> None:
        self.repository.set_paused(False)

    def cancel(self, attempt_id: str) -> JobAttempt:
        return self.repository.cancel(attempt_id, self.clock())

    def retry(self, job_id: str) -> JobAttempt:
        return self.repository.retry(job_id, self.clock())

    def progress(self, claim: JobAttempt, progress: JobProgress) -> JobAttempt:
        return self.repository.report_progress(claim.id, claim.claim_token, progress, self.clock())

    def complete(self, claim: JobAttempt, output_artifact_ids: Sequence[str] = ()) -> JobAttempt:
        return self.repository.finish(claim.id, claim.claim_token, AttemptStatus.COMPLETED, self.clock(),
                                      output_artifact_ids=output_artifact_ids)

    def fail(self, claim: JobAttempt, error: str) -> JobAttempt:
        return self.repository.finish(claim.id, claim.claim_token, AttemptStatus.FAILED, self.clock(), error=error)

    def acknowledge_cancel(self, claim: JobAttempt) -> JobAttempt:
        return self.repository.finish(claim.id, claim.claim_token, AttemptStatus.CANCELED, self.clock())

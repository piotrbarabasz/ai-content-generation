"""Coordinator-side publication orchestration through injected ports, never a worker."""

import json
from collections.abc import Mapping
from datetime import datetime
from typing import BinaryIO, Callable, Protocol

from app.domain.base import new_id, utc_now
from app.domain.dependencies import DEPENDENCY_METADATA_KEY, DependencyDeclaration, RequestFingerprint, canonical_json
from app.domain.generation_job import JobAttempt, JobRequest
from app.domain.publication import PUBLICATION_KEY, PublicationConflictError, PublicationResult, PublicationSnapshot


class ResultPublicationPort(Protocol):
    def capture(self, expected_sections: dict[str, str], script_revision_id: str | None) -> PublicationSnapshot: ...
    def enqueue(self, job: JobRequest) -> JobAttempt: ...
    def selected(self) -> dict[str, str]: ...
    def result(self, claim: JobAttempt) -> PublicationResult | None: ...
    def prepare(self, claim: JobAttempt) -> JobRequest: ...


class ResultByteStore(Protocol):
    def import_stream(self, name, source, metadata=None): ...


class ResultPublicationService:
    def __init__(self, repository: ResultPublicationPort, store: ResultByteStore, *, clock: Callable[[], datetime] = utc_now):
        self.repository, self.store, self.clock = repository, store, clock

    def enqueue(self, output_key: str, request: RequestFingerprint, *, expected_sections: dict[str, str],
                expected_script_revision_id: str | None = None, inputs: Mapping | None = None) -> JobAttempt:
        snapshot = self.repository.capture(expected_sections, expected_script_revision_id)
        request = snapshot.bind(request)
        previous = self.repository.selected().get(output_key)
        job = JobRequest(new_id("job"), output_key, request,
                         canonical_json({PUBLICATION_KEY: snapshot.to_payload(), "inputs": dict(inputs or {})}),
                         self.clock(), (previous,) if previous else ())
        return self.repository.enqueue(job)

    def publish(self, claim: JobAttempt, name: str, source: BinaryIO, *, metadata: Mapping | None = None) -> PublicationResult:
        """One result per attempt: a replay returns its committed decision, never reselects.

        The replay does not read replacement bytes. First committed result wins.
        Validation of media format belongs to the generation operation before this call.
        """
        existing = self.repository.result(claim)
        if existing is not None:
            return existing
        job = self.repository.prepare(claim)
        metadata = json.loads(canonical_json(dict(metadata or {})))
        if PUBLICATION_KEY in metadata or DEPENDENCY_METADATA_KEY in metadata:
            raise PublicationConflictError("Operation metadata cannot replace publication inputs.")
        metadata.update(DependencyDeclaration(job.output_key, job.request).to_metadata())
        metadata[PUBLICATION_KEY] = {"version": 1, "job_id": job.id, "attempt_id": claim.id,
                                     "claim_token": claim.claim_token,
                                     "input_snapshot": json.loads(job.input_snapshot_json)}
        self.store.import_stream(name, source, metadata)
        result = self.repository.result(claim)
        if result is None:
            raise PublicationConflictError("Store did not commit through the publication gate.")
        return result

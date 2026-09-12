"""Generation job domain model."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
import json
from typing import ClassVar

from app.domain.base import DomainEntity, DomainValidationError, new_id
from app.domain.types import JsonDict
from app.domain.dependencies import FailedAttempt, RequestFingerprint, canonical_json


def _coerce_str_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if values is None:
        return []
    return [str(value) for value in values]


@dataclass(slots=True)
class GenerationJob(DomainEntity):
    VALID_STATUSES: ClassVar[set[str]] = {
        "pending",
        "running",
        "completed",
        "failed",
        "skipped",
        "waiting_for_approval",
    }

    workflow_run_id: str = ""
    module_name: str = ""
    status: str = "pending"
    attempt: int = 1
    retry_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    output_artifact_ids: list[str] = field(default_factory=list)
    usage_metadata: JsonDict = field(default_factory=dict)
    error_message: str = ""

    @classmethod
    def create(
        cls,
        *,
        workflow_run_id: str,
        module_name: str,
        status: str = "pending",
        attempt: int = 1,
        retry_count: int = 0,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        output_artifact_ids: list[str] | tuple[str, ...] | None = None,
        usage_metadata: JsonDict | None = None,
        error_message: str = "",
    ) -> "GenerationJob":
        if not workflow_run_id.strip():
            raise DomainValidationError("GenerationJob workflow_run_id is required.")
        if not module_name.strip():
            raise DomainValidationError("GenerationJob module_name is required.")
        if status not in cls.VALID_STATUSES:
            raise DomainValidationError(f"Invalid GenerationJob status: {status}.")
        if attempt < 1:
            raise DomainValidationError("GenerationJob attempt must be greater than zero.")
        if retry_count < 0:
            raise DomainValidationError("GenerationJob retry_count cannot be negative.")

        return cls(
            id=new_id("generation_job"),
            workflow_run_id=workflow_run_id,
            module_name=module_name,
            status=status,
            attempt=attempt,
            retry_count=retry_count,
            started_at=started_at,
            completed_at=completed_at,
            output_artifact_ids=_coerce_str_list(output_artifact_ids),
            usage_metadata=usage_metadata or {},
            error_message=error_message,
        )


class AttemptStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INTERRUPTED = "interrupted"


def require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{name} must be non-empty text.")


def require_time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError("Job times must be timezone-aware.")


def artifact_ids(values) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise DomainValidationError("Artifact references must be an ordered list of IDs.")
    for value in values:
        require_text(value, "artifact ID")
    if len(set(values)) != len(values):
        raise DomainValidationError("Artifact references must be unique.")
    return tuple(values)


@dataclass(frozen=True, slots=True)
class JobRequest:
    """Durable desktop operation, separate from the legacy workflow-run DTO above.

    Snapshot JSON contains actual immutable inputs/revision references, not just
    hashes. Producers define its operation-specific shape and exclude secrets.
    """

    id: str
    output_key: str
    request: RequestFingerprint
    input_snapshot_json: str
    created_at: datetime
    prior_artifact_ids: tuple[str, ...] = ()

    def __post_init__(self):
        require_text(self.id, "job ID")
        require_text(self.output_key, "output key")
        require_time(self.created_at)
        if not isinstance(self.request, RequestFingerprint):
            raise DomainValidationError("A job requires a request fingerprint.")
        snapshot = json.loads(self.input_snapshot_json)
        if not isinstance(snapshot, dict):
            raise DomainValidationError("Job inputs must be a JSON object.")
        object.__setattr__(self, "input_snapshot_json", canonical_json(snapshot))
        object.__setattr__(self, "prior_artifact_ids", artifact_ids(self.prior_artifact_ids))

    def to_payload(self):
        return {"version": 1, "id": self.id, "output_key": self.output_key,
                "request": self.request.to_payload(), "input_snapshot": json.loads(self.input_snapshot_json),
                "created_at": self.created_at.isoformat(), "prior_artifact_ids": list(self.prior_artifact_ids)}

    @classmethod
    def from_payload(cls, payload):
        if type(payload.get("version")) is not int or payload["version"] != 1:
            raise DomainValidationError("Unsupported job request format.")
        return cls(payload["id"], payload["output_key"], RequestFingerprint.from_payload(payload["request"]),
                   canonical_json(payload["input_snapshot"]), datetime.fromisoformat(payload["created_at"]),
                   tuple(payload["prior_artifact_ids"]))


@dataclass(frozen=True, slots=True)
class JobProgress:
    phase: str
    completed: int | None = None
    total: int | None = None

    def __post_init__(self):
        require_text(self.phase, "progress phase")
        for value in (self.completed, self.total):
            if value is not None and (type(value) is not int or value < 0):
                raise DomainValidationError("Progress counts must be non-negative integers.")
        if self.total is not None and (self.completed is None or self.completed > self.total):
            raise DomainValidationError("Progress must not exceed its declared total.")


@dataclass(frozen=True, slots=True)
class JobAttempt:
    id: str
    job_id: str
    number: int
    status: AttemptStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    owner: str | None = None
    claim_token: str | None = None
    cancel_requested: bool = False
    progress: JobProgress | None = None
    output_artifact_ids: tuple[str, ...] = ()
    error: str = ""

    def __post_init__(self):
        require_text(self.id, "attempt ID")
        require_text(self.job_id, "job ID")
        if type(self.number) is not int or self.number < 1:
            raise DomainValidationError("Attempt numbers must be positive integers.")
        object.__setattr__(self, "status", AttemptStatus(self.status))
        object.__setattr__(self, "output_artifact_ids", artifact_ids(self.output_artifact_ids))
        for value in (self.created_at, self.updated_at, self.started_at, self.finished_at):
            if value is not None:
                require_time(value)
        if self.progress is not None and not isinstance(self.progress, JobProgress):
            raise DomainValidationError("Attempt progress must be an immutable JobProgress value.")

    def failure(self, job: JobRequest) -> FailedAttempt:
        if self.status != AttemptStatus.FAILED or self.job_id != job.id:
            raise DomainValidationError("Only a failed attempt of this job supplies failure evidence.")
        return FailedAttempt(self.id, job.output_key, job.request.fingerprint, self.error)

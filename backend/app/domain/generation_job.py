"""Generation job domain model."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from app.domain.base import DomainEntity, DomainValidationError, new_id
from app.domain.types import JsonDict


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

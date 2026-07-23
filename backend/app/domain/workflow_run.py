"""Workflow run domain model."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from app.domain.base import DomainEntity, DomainValidationError, new_id


def _coerce_str_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if values is None:
        return []
    return [str(value) for value in values]


@dataclass(slots=True)
class WorkflowRun(DomainEntity):
    VALID_STATUSES: ClassVar[set[str]] = {
        "pending",
        "validating",
        "running",
        "waiting_for_approval",
        "completed",
        "failed",
        "skipped",
    }

    workflow_config_id: str = ""
    status: str = "pending"
    current_stage: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    approval_checkpoint_ids: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        workflow_config_id: str,
        status: str = "pending",
        current_stage: str = "",
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        error_message: str = "",
        artifact_ids: list[str] | tuple[str, ...] | None = None,
        approval_checkpoint_ids: list[str] | tuple[str, ...] | None = None,
    ) -> "WorkflowRun":
        if not workflow_config_id.strip():
            raise DomainValidationError("WorkflowRun workflow_config_id is required.")
        if status not in cls.VALID_STATUSES:
            raise DomainValidationError(f"Invalid WorkflowRun status: {status}.")
        if current_stage and not current_stage.strip():
            raise DomainValidationError("WorkflowRun current_stage cannot be blank.")

        return cls(
            id=new_id("workflow_run"),
            workflow_config_id=workflow_config_id,
            status=status,
            current_stage=current_stage,
            started_at=started_at,
            completed_at=completed_at,
            error_message=error_message,
            artifact_ids=_coerce_str_list(artifact_ids),
            approval_checkpoint_ids=_coerce_str_list(approval_checkpoint_ids),
        )

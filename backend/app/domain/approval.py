"""Approval checkpoint domain model and state machine."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from app.domain.base import DomainEntity, DomainValidationError, new_id, utc_now


def _coerce_str(value: str | None) -> str:
    return "" if value is None else str(value)


@dataclass(slots=True)
class ApprovalDecision(DomainEntity):
    """A recorded approval decision for a checkpoint."""

    VALID_DECISIONS: ClassVar[set[str]] = {
        "approve",
        "reject",
        "request_changes",
        "skip",
    }

    checkpoint_id: str = ""
    decision: str = ""
    reviewer_id: str = ""
    comment: str = ""
    revised_artifact_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        checkpoint_id: str,
        decision: str,
        reviewer_id: str,
        comment: str = "",
        revised_artifact_id: str | None = None,
        created_at: datetime | None = None,
    ) -> "ApprovalDecision":
        if not checkpoint_id.strip():
            raise DomainValidationError("ApprovalDecision checkpoint_id is required.")
        if decision not in cls.VALID_DECISIONS:
            raise DomainValidationError(f"Invalid ApprovalDecision decision: {decision}.")
        if not reviewer_id.strip():
            raise DomainValidationError("ApprovalDecision reviewer_id is required.")

        return cls(
            id=new_id("approval_decision"),
            checkpoint_id=checkpoint_id,
            decision=decision,
            reviewer_id=reviewer_id,
            comment=comment,
            revised_artifact_id=_coerce_str(revised_artifact_id) or None,
            created_at=created_at or utc_now(),
        )


@dataclass(slots=True)
class ApprovalCheckpoint(DomainEntity):
    """Approval checkpoint with explicit state transitions."""

    VALID_STATUSES: ClassVar[set[str]] = {
        "not_required",
        "pending",
        "approved",
        "rejected",
        "changes_requested",
        "skipped",
    }

    ACTIVE_STATUSES: ClassVar[set[str]] = {"pending", "changes_requested"}
    RESUMABLE_STATUSES: ClassVar[set[str]] = {"not_required", "approved", "skipped"}
    DECISION_TO_STATUS: ClassVar[dict[str, str]] = {
        "approve": "approved",
        "reject": "rejected",
        "request_changes": "changes_requested",
        "skip": "skipped",
    }

    workflow_run_id: str = ""
    checkpoint_type: str = ""
    artifact_id: str = ""
    status: str = "pending"
    required: bool = True
    resolved_at: datetime | None = None
    decision_history: list[ApprovalDecision] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        workflow_run_id: str,
        checkpoint_type: str,
        artifact_id: str,
        required: bool = True,
        status: str | None = None,
        resolved_at: datetime | None = None,
        decision_history: list[ApprovalDecision] | tuple[ApprovalDecision, ...] | None = None,
    ) -> "ApprovalCheckpoint":
        if not workflow_run_id.strip():
            raise DomainValidationError("ApprovalCheckpoint workflow_run_id is required.")
        if not checkpoint_type.strip():
            raise DomainValidationError("ApprovalCheckpoint checkpoint_type is required.")
        if not artifact_id.strip():
            raise DomainValidationError("ApprovalCheckpoint artifact_id is required.")

        effective_status = status or ("pending" if required else "not_required")
        if effective_status not in cls.VALID_STATUSES:
            raise DomainValidationError(f"Invalid ApprovalCheckpoint status: {effective_status}.")
        if required and effective_status == "not_required":
            raise DomainValidationError("Required ApprovalCheckpoint cannot be not_required.")
        if not required and effective_status == "pending":
            raise DomainValidationError("Optional ApprovalCheckpoint cannot start as pending.")
        if effective_status in cls.ACTIVE_STATUSES and resolved_at is not None:
            raise DomainValidationError("Active ApprovalCheckpoint cannot have resolved_at set.")
        if effective_status in cls.RESUMABLE_STATUSES and resolved_at is None and effective_status != "not_required":
            raise DomainValidationError("Resolved ApprovalCheckpoint requires resolved_at.")

        return cls(
            id=new_id("approval_checkpoint"),
            workflow_run_id=workflow_run_id,
            checkpoint_type=checkpoint_type,
            artifact_id=artifact_id,
            status=effective_status,
            required=required,
            resolved_at=resolved_at,
            decision_history=list(decision_history or []),
        )

    @property
    def is_blocking(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

    @property
    def is_resumable(self) -> bool:
        return self.status in self.RESUMABLE_STATUSES

    @property
    def latest_decision(self) -> ApprovalDecision | None:
        if not self.decision_history:
            return None
        return self.decision_history[-1]

    def approve(
        self,
        *,
        reviewer_id: str,
        comment: str = "",
        revised_artifact_id: str | None = None,
    ) -> ApprovalDecision:
        return self._apply_decision(
            "approve",
            reviewer_id=reviewer_id,
            comment=comment,
            revised_artifact_id=revised_artifact_id,
        )

    def reject(
        self,
        *,
        reviewer_id: str,
        comment: str = "",
        revised_artifact_id: str | None = None,
    ) -> ApprovalDecision:
        return self._apply_decision(
            "reject",
            reviewer_id=reviewer_id,
            comment=comment,
            revised_artifact_id=revised_artifact_id,
        )

    def request_changes(
        self,
        *,
        reviewer_id: str,
        comment: str = "",
        revised_artifact_id: str | None = None,
    ) -> ApprovalDecision:
        return self._apply_decision(
            "request_changes",
            reviewer_id=reviewer_id,
            comment=comment,
            revised_artifact_id=revised_artifact_id,
        )

    def skip(
        self,
        *,
        reviewer_id: str,
        comment: str = "",
        revised_artifact_id: str | None = None,
    ) -> ApprovalDecision:
        return self._apply_decision(
            "skip",
            reviewer_id=reviewer_id,
            comment=comment,
            revised_artifact_id=revised_artifact_id,
        )

    def _apply_decision(
        self,
        decision: str,
        *,
        reviewer_id: str,
        comment: str = "",
        revised_artifact_id: str | None = None,
    ) -> ApprovalDecision:
        if decision not in ApprovalDecision.VALID_DECISIONS:
            raise DomainValidationError(f"Invalid ApprovalDecision decision: {decision}.")
        if not reviewer_id.strip():
            raise DomainValidationError("ApprovalCheckpoint reviewer_id is required.")
        if self.status not in self.ACTIVE_STATUSES and not (
            decision == "skip" and self.status == "not_required"
        ):
            raise DomainValidationError(
                f"ApprovalCheckpoint status {self.status} does not allow {decision}."
            )

        if self.status == "not_required" and decision != "skip":
            raise DomainValidationError("ApprovalCheckpoint status not_required only allows skip.")
        if self.status == "pending" and decision == "skip" and self.required is False:
            raise DomainValidationError("Optional ApprovalCheckpoint cannot be skipped from pending.")

        recorded_decision = ApprovalDecision.create(
            checkpoint_id=self.id,
            decision=decision,
            reviewer_id=reviewer_id,
            comment=comment,
            revised_artifact_id=revised_artifact_id,
        )
        self.decision_history.append(recorded_decision)

        self.status = self.DECISION_TO_STATUS[decision]
        if self.status in {"approved", "rejected", "skipped"}:
            self.resolved_at = recorded_decision.created_at
        else:
            self.resolved_at = None

        return recorded_decision

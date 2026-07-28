"""Approval checkpoint endpoints for the AI Content Studio API."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from app.api.schemas import ApiSchema, WorkflowRunSchema
from app.domain.approval import ApprovalCheckpoint

from .projects import get_workflow_run_or_404, register_api_router

router = APIRouter(tags=["approvals"])

APPROVAL_CHECKPOINTS_BY_RUN: dict[str, dict[str, ApprovalCheckpoint]] = {}


class ApprovalDecisionRequest(ApiSchema):
    reviewer_id: str = Field(min_length=1)
    comment: str = ""
    revised_artifact_id: str | None = Field(default=None)


class ApprovalDecisionSchema(ApiSchema):
    id: str
    checkpoint_id: str
    decision: str
    reviewer_id: str
    comment: str
    revised_artifact_id: str | None = None
    created_at: datetime


class ApprovalCheckpointSchema(ApiSchema):
    id: str
    workflow_run_id: str
    checkpoint_type: str
    artifact_id: str
    status: str
    required: bool
    resolved_at: datetime | None = None
    decision_history: list[ApprovalDecisionSchema] = Field(default_factory=list)
    created_at: datetime


def reset_approval_state() -> None:
    APPROVAL_CHECKPOINTS_BY_RUN.clear()


def register_approval_checkpoint(checkpoint: ApprovalCheckpoint) -> ApprovalCheckpoint:
    workflow_run_checkpoints = APPROVAL_CHECKPOINTS_BY_RUN.setdefault(checkpoint.workflow_run_id, {})
    workflow_run_checkpoints[checkpoint.id] = checkpoint

    workflow_run = get_workflow_run_or_404(checkpoint.workflow_run_id)
    if checkpoint.id not in workflow_run.approval_checkpoint_ids:
        workflow_run.approval_checkpoint_ids.append(checkpoint.id)
    return checkpoint


def list_approval_checkpoints_for_run(workflow_run_id: str) -> tuple[ApprovalCheckpoint, ...]:
    checkpoints = APPROVAL_CHECKPOINTS_BY_RUN.get(workflow_run_id, {})
    return tuple(checkpoints.values())


def get_approval_checkpoint_or_404(workflow_run_id: str, checkpoint_id: str) -> ApprovalCheckpoint:
    workflow_run = get_workflow_run_or_404(workflow_run_id)
    try:
        return APPROVAL_CHECKPOINTS_BY_RUN[workflow_run.id][checkpoint_id]
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval checkpoint not found") from exc


def get_blocking_approval_checkpoints(workflow_run_id: str) -> tuple[ApprovalCheckpoint, ...]:
    checkpoints = list_approval_checkpoints_for_run(workflow_run_id)
    return tuple(checkpoint for checkpoint in checkpoints if not checkpoint.is_resumable)


def _checkpoint_schema(checkpoint: ApprovalCheckpoint) -> ApprovalCheckpointSchema:
    return ApprovalCheckpointSchema.model_validate(checkpoint)


def _apply_decision(
    workflow_run_id: str,
    checkpoint_id: str,
    request: ApprovalDecisionRequest,
    action: str,
) -> ApprovalCheckpointSchema:
    checkpoint = get_approval_checkpoint_or_404(workflow_run_id, checkpoint_id)
    decision_handler = getattr(checkpoint, action)
    decision_handler(
        reviewer_id=request.reviewer_id,
        comment=request.comment,
        revised_artifact_id=request.revised_artifact_id,
    )
    return ApprovalCheckpointSchema.model_validate(checkpoint)


@router.get("/workflow-runs/{workflow_run_id}/approvals", response_model=list[ApprovalCheckpointSchema])
def list_workflow_run_approvals(workflow_run_id: str) -> list[ApprovalCheckpointSchema]:
    get_workflow_run_or_404(workflow_run_id)
    return [_checkpoint_schema(checkpoint) for checkpoint in list_approval_checkpoints_for_run(workflow_run_id)]


@router.post(
    "/workflow-runs/{workflow_run_id}/approvals/{checkpoint_id}/approve",
    response_model=ApprovalCheckpointSchema,
)
def approve_workflow_run_approval(
    workflow_run_id: str,
    checkpoint_id: str,
    request: ApprovalDecisionRequest,
) -> ApprovalCheckpointSchema:
    return _apply_decision(workflow_run_id, checkpoint_id, request, "approve")


@router.post(
    "/workflow-runs/{workflow_run_id}/approvals/{checkpoint_id}/reject",
    response_model=ApprovalCheckpointSchema,
)
def reject_workflow_run_approval(
    workflow_run_id: str,
    checkpoint_id: str,
    request: ApprovalDecisionRequest,
) -> ApprovalCheckpointSchema:
    return _apply_decision(workflow_run_id, checkpoint_id, request, "reject")


@router.post(
    "/workflow-runs/{workflow_run_id}/approvals/{checkpoint_id}/request-changes",
    response_model=ApprovalCheckpointSchema,
)
def request_changes_for_workflow_run_approval(
    workflow_run_id: str,
    checkpoint_id: str,
    request: ApprovalDecisionRequest,
) -> ApprovalCheckpointSchema:
    return _apply_decision(workflow_run_id, checkpoint_id, request, "request_changes")


def approval_checkpoints_are_resolved(workflow_run_id: str) -> bool:
    return all(checkpoint.is_resumable for checkpoint in list_approval_checkpoints_for_run(workflow_run_id))


register_api_router(router)


__all__ = [
    "APPROVAL_CHECKPOINTS_BY_RUN",
    "ApprovalCheckpointSchema",
    "ApprovalDecisionRequest",
    "ApprovalDecisionSchema",
    "approval_checkpoints_are_resolved",
    "approve_workflow_run_approval",
    "get_approval_checkpoint_or_404",
    "get_blocking_approval_checkpoints",
    "list_approval_checkpoints_for_run",
    "list_workflow_run_approvals",
    "register_approval_checkpoint",
    "reject_workflow_run_approval",
    "request_changes_for_workflow_run_approval",
    "reset_approval_state",
]

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from app.domain.approval import ApprovalCheckpoint, ApprovalDecision
from app.domain.base import DomainValidationError


def test_approval_checkpoint_required_flow_records_changes_requested_approved_and_decisions() -> None:
    checkpoint = ApprovalCheckpoint.create(
        workflow_run_id="workflow_run_1",
        checkpoint_type="script",
        artifact_id="artifact_1",
    )

    assert checkpoint.status == "pending"
    assert checkpoint.required is True
    assert checkpoint.is_blocking is True
    assert checkpoint.is_resumable is False
    assert checkpoint.latest_decision is None

    changes_requested = checkpoint.request_changes(
        reviewer_id="reviewer_1",
        comment="Tighten the hook.",
        revised_artifact_id="artifact_2",
    )

    assert changes_requested.decision == "request_changes"
    assert checkpoint.status == "changes_requested"
    assert checkpoint.artifact_id == "artifact_1"
    assert checkpoint.resolved_at is None
    assert checkpoint.is_blocking is True
    assert checkpoint.latest_decision == changes_requested

    approved = checkpoint.approve(
        reviewer_id="reviewer_1",
        comment="Ready to proceed.",
    )

    payload = asdict(checkpoint)
    encoded = json.dumps(payload, default=str)

    assert approved.decision == "approve"
    assert checkpoint.status == "approved"
    assert checkpoint.is_blocking is False
    assert checkpoint.is_resumable is True
    assert checkpoint.resolved_at == approved.created_at
    assert checkpoint.latest_decision == approved
    assert [decision.decision for decision in checkpoint.decision_history] == [
        "request_changes",
        "approve",
    ]
    assert payload["artifact_id"] == "artifact_1"
    assert payload["decision_history"][0]["revised_artifact_id"] == "artifact_2"
    assert "\"status\": \"approved\"" in encoded


def test_approval_checkpoint_rejection_preserves_artifact_and_records_decision() -> None:
    checkpoint = ApprovalCheckpoint.create(
        workflow_run_id="workflow_run_1",
        checkpoint_type="scene_plan",
        artifact_id="artifact_2",
    )

    rejected = checkpoint.reject(
        reviewer_id="reviewer_2",
        comment="The pacing is off.",
    )

    payload = asdict(checkpoint)

    assert rejected.decision == "reject"
    assert checkpoint.status == "rejected"
    assert checkpoint.artifact_id == "artifact_2"
    assert checkpoint.is_resumable is False
    assert checkpoint.resolved_at == rejected.created_at
    assert checkpoint.decision_history == [rejected]
    assert payload["artifact_id"] == "artifact_2"
    assert payload["decision_history"][0]["comment"] == "The pacing is off."


def test_approval_checkpoint_optional_states_and_skip_transition_are_supported() -> None:
    optional_checkpoint = ApprovalCheckpoint.create(
        workflow_run_id="workflow_run_1",
        checkpoint_type="captions",
        artifact_id="artifact_3",
        required=False,
    )

    assert optional_checkpoint.status == "not_required"
    assert optional_checkpoint.is_resumable is True
    assert optional_checkpoint.resolved_at is None

    skipped = optional_checkpoint.skip(
        reviewer_id="system",
        comment="Policy skipped captions for this run.",
    )

    assert skipped.decision == "skip"
    assert optional_checkpoint.status == "skipped"
    assert optional_checkpoint.is_resumable is True
    assert optional_checkpoint.resolved_at == skipped.created_at
    assert optional_checkpoint.latest_decision == skipped


def test_approval_models_reject_invalid_inputs_and_terminal_state_transitions() -> None:
    with pytest.raises(DomainValidationError, match="ApprovalCheckpoint workflow_run_id is required"):
        ApprovalCheckpoint.create(
            workflow_run_id=" ",
            checkpoint_type="script",
            artifact_id="artifact_1",
        )

    with pytest.raises(DomainValidationError, match="ApprovalDecision decision"):
        ApprovalDecision.create(
            checkpoint_id="checkpoint_1",
            decision="hold",
            reviewer_id="reviewer_1",
        )

    checkpoint = ApprovalCheckpoint.create(
        workflow_run_id="workflow_run_1",
        checkpoint_type="script",
        artifact_id="artifact_1",
    )
    checkpoint.approve(reviewer_id="reviewer_1")

    with pytest.raises(DomainValidationError, match="does not allow reject"):
        checkpoint.reject(reviewer_id="reviewer_1")

    terminal_checkpoint = ApprovalCheckpoint.create(
        workflow_run_id="workflow_run_2",
        checkpoint_type="export",
        artifact_id="artifact_4",
        status="skipped",
        resolved_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
    )

    with pytest.raises(DomainValidationError, match="status skipped does not allow approve"):
        terminal_checkpoint.approve(reviewer_id="reviewer_2")

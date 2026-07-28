"""Workflow execution state types."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.domain.approval import ApprovalCheckpoint
from app.domain.types import JsonDict

ExecutionStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
    "waiting_for_approval",
]


def _coerce_tuple(values: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    return tuple(str(value).strip() for value in values)


def _coerce_approval_checkpoint_map(
    approval_checkpoints: Mapping[str, ApprovalCheckpoint] | Sequence[ApprovalCheckpoint] | None,
) -> dict[str, ApprovalCheckpoint]:
    if approval_checkpoints is None:
        return {}
    if isinstance(approval_checkpoints, Mapping):
        checkpoint_map: dict[str, ApprovalCheckpoint] = {}
        for key, checkpoint in approval_checkpoints.items():
            checkpoint_id = str(key).strip()
            if checkpoint_id and isinstance(checkpoint, ApprovalCheckpoint):
                checkpoint_map[checkpoint_id] = checkpoint
        return checkpoint_map

    checkpoint_map: dict[str, ApprovalCheckpoint] = {}
    for checkpoint in approval_checkpoints:
        if not isinstance(checkpoint, ApprovalCheckpoint):
            continue
        checkpoint_id = checkpoint.id.strip()
        if checkpoint_id:
            checkpoint_map[checkpoint_id] = checkpoint
    return checkpoint_map


def _approval_checkpoint_id_from_output(output: Mapping[str, object]) -> str:
    checkpoint_payload = output.get("approval_checkpoint")
    if not isinstance(checkpoint_payload, Mapping):
        return ""
    checkpoint_id = str(checkpoint_payload.get("id", "")).strip()
    if checkpoint_id:
        return checkpoint_id

    checkpoint_ids = output.get("approval_checkpoint_ids")
    if isinstance(checkpoint_ids, Sequence) and not isinstance(checkpoint_ids, (str, bytes)):
        for value in checkpoint_ids:
            candidate = str(value).strip()
            if candidate:
                return candidate
    return ""


def approval_checkpoint_id_from_result(result: "ModuleResult") -> str:
    """Return the first approval checkpoint id embedded in a module result."""

    output = result.output
    if not isinstance(output, Mapping):
        return ""
    return _approval_checkpoint_id_from_output(output)


def approval_checkpoint_ids_from_result(result: "ModuleResult") -> tuple[str, ...]:
    """Return approval checkpoint ids embedded in a module result."""

    output = result.output
    if not isinstance(output, Mapping):
        return ()

    checkpoint_ids: list[str] = []
    checkpoint_id = approval_checkpoint_id_from_result(result)
    if checkpoint_id:
        checkpoint_ids.append(checkpoint_id)

    nested_ids = output.get("approval_checkpoint_ids")
    if isinstance(nested_ids, Sequence) and not isinstance(nested_ids, (str, bytes)):
        for value in nested_ids:
            candidate = str(value).strip()
            if candidate and candidate not in checkpoint_ids:
                checkpoint_ids.append(candidate)

    return tuple(checkpoint_ids)


@dataclass(slots=True, frozen=True)
class ModuleResult:
    """Captured outcome from a module execution attempt."""

    module_name: str
    status: ExecutionStatus = "pending"
    output_artifact_ids: tuple[str, ...] = field(default_factory=tuple)
    usage_metadata: JsonDict = field(default_factory=dict)
    output: JsonDict = field(default_factory=dict)
    error_message: str = ""
    skipped_reason: str = ""

    def __post_init__(self) -> None:
        if not self.module_name.strip():
            raise ValueError("ModuleResult module_name is required.")
        object.__setattr__(self, "output_artifact_ids", _coerce_tuple(self.output_artifact_ids))
        object.__setattr__(self, "usage_metadata", dict(self.usage_metadata))
        object.__setattr__(self, "output", dict(self.output))
        if self.status not in {
            "pending",
            "running",
            "completed",
            "failed",
            "skipped",
            "waiting_for_approval",
        }:
            raise ValueError(f"Invalid ModuleResult status: {self.status}.")


@dataclass(slots=True, frozen=True)
class ModuleExecutionContext:
    """Immutable context passed to a module execution."""

    workflow_run_id: str
    workflow_config_id: str
    module_name: str
    enabled_modules: tuple[str, ...] = field(default_factory=tuple)
    disabled_modules: tuple[str, ...] = field(default_factory=tuple)
    inputs: JsonDict = field(default_factory=dict)
    module_results: dict[str, ModuleResult] = field(default_factory=dict)
    artifact_ids: tuple[str, ...] = field(default_factory=tuple)
    approval_checkpoint_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.workflow_run_id.strip():
            raise ValueError("ModuleExecutionContext workflow_run_id is required.")
        if not self.workflow_config_id.strip():
            raise ValueError("ModuleExecutionContext workflow_config_id is required.")
        if not self.module_name.strip():
            raise ValueError("ModuleExecutionContext module_name is required.")
        object.__setattr__(self, "enabled_modules", _coerce_tuple(self.enabled_modules))
        object.__setattr__(self, "disabled_modules", _coerce_tuple(self.disabled_modules))
        object.__setattr__(self, "inputs", dict(self.inputs))
        object.__setattr__(self, "module_results", dict(self.module_results))
        object.__setattr__(self, "artifact_ids", _coerce_tuple(self.artifact_ids))
        object.__setattr__(
            self,
            "approval_checkpoint_ids",
            _coerce_tuple(self.approval_checkpoint_ids),
        )


@dataclass(slots=True, frozen=True)
class WorkflowExecutionState:
    """Seed state used to resume a partially completed workflow run."""

    module_results: dict[str, ModuleResult] = field(default_factory=dict)
    approval_checkpoint_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "module_results", dict(self.module_results))
        object.__setattr__(self, "approval_checkpoint_ids", _coerce_tuple(self.approval_checkpoint_ids))

    @classmethod
    def create(
        cls,
        *,
        module_results: Mapping[str, ModuleResult] | None = None,
        approval_checkpoints: Mapping[str, ApprovalCheckpoint] | Sequence[ApprovalCheckpoint] | None = None,
    ) -> "WorkflowExecutionState":
        checkpoint_map = _coerce_approval_checkpoint_map(approval_checkpoints)
        seeded_results = dict(module_results or {})
        checkpoint_ids: list[str] = []
        for result in seeded_results.values():
            for checkpoint_id in approval_checkpoint_ids_from_result(result):
                if checkpoint_id not in checkpoint_ids:
                    checkpoint_ids.append(checkpoint_id)
        for checkpoint_id in checkpoint_map:
            if checkpoint_id not in checkpoint_ids:
                checkpoint_ids.append(checkpoint_id)
        return cls(module_results=seeded_results, approval_checkpoint_ids=tuple(checkpoint_ids))


@dataclass(slots=True, frozen=True)
class ModuleExecutionStep:
    """Single step in a workflow execution plan."""

    module_name: str
    dependency_groups: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    enabled: bool = True
    status: ExecutionStatus = "pending"
    retry_limit: int = 0
    artifact_outputs: tuple[str, ...] = field(default_factory=tuple)
    disabled_reason: str = ""
    resolved_dependencies: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.module_name.strip():
            raise ValueError("ModuleExecutionStep module_name is required.")
        object.__setattr__(
            self,
            "dependency_groups",
            tuple(tuple(str(value).strip() for value in group) for group in self.dependency_groups),
        )
        object.__setattr__(self, "artifact_outputs", _coerce_tuple(self.artifact_outputs))
        object.__setattr__(self, "resolved_dependencies", _coerce_tuple(self.resolved_dependencies))
        if self.retry_limit < 0:
            raise ValueError("ModuleExecutionStep retry_limit cannot be negative.")
        if self.status not in {
            "pending",
            "running",
            "completed",
            "failed",
            "skipped",
            "waiting_for_approval",
        }:
            raise ValueError(f"Invalid ModuleExecutionStep status: {self.status}.")


@dataclass(slots=True, frozen=True)
class ModuleExecutionPlan:
    """Ordered execution plan for a workflow run."""

    steps: tuple[ModuleExecutionStep, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(self.steps))
        seen: set[str] = set()
        for step in self.steps:
            if step.module_name in seen:
                raise ValueError(f"Duplicate module in execution plan: {step.module_name}.")
            seen.add(step.module_name)

    @property
    def module_names(self) -> tuple[str, ...]:
        return tuple(step.module_name for step in self.steps)

    @property
    def enabled_modules(self) -> tuple[str, ...]:
        return tuple(step.module_name for step in self.steps if step.enabled)

    @property
    def disabled_modules(self) -> tuple[str, ...]:
        return tuple(step.module_name for step in self.steps if not step.enabled)

    def step_for(self, module_name: str) -> ModuleExecutionStep:
        for step in self.steps:
            if step.module_name == module_name:
                return step
        raise KeyError(module_name)

"""Workflow execution state types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

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

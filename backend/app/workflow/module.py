"""Workflow module contract types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from app.domain.types import JsonDict, ModuleName

DependencyGroup = tuple[ModuleName, ...]
DisabledModuleBehavior = Literal["skip", "fail"]


def _coerce_name(value: str, *, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"ModuleDefinition {field_name} is required.")
    return normalized


def _coerce_tuple(values: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    return tuple(_coerce_name(value, field_name="value") for value in values)


def _coerce_dependency_groups(
    values: tuple[tuple[str, ...], ...] | list[tuple[str, ...]] | None,
) -> tuple[DependencyGroup, ...]:
    if values is None:
        return ()
    groups: list[DependencyGroup] = []
    for group in values:
        normalized_group = tuple(
            _coerce_name(value, field_name="dependency") for value in group
        )
        if not normalized_group:
            raise ValueError("ModuleDefinition dependencies cannot contain empty groups.")
        groups.append(normalized_group)
    return tuple(groups)


@dataclass(slots=True, frozen=True)
class ModuleDefinition:
    """Static contract for a workflow module."""

    name: ModuleName
    input_schema: JsonDict = field(default_factory=dict)
    output_schema: JsonDict = field(default_factory=dict)
    config_schema: JsonDict = field(default_factory=dict)
    dependencies: tuple[DependencyGroup, ...] = field(default_factory=tuple)
    enabled_by_default: bool = True
    disabled_behavior: DisabledModuleBehavior = "fail"
    retry_limit: int = 0
    artifact_outputs: tuple[str, ...] = field(default_factory=tuple)
    error_behavior: str = "fail_fast"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _coerce_name(self.name, field_name="name"))
        object.__setattr__(self, "input_schema", dict(self.input_schema))
        object.__setattr__(self, "output_schema", dict(self.output_schema))
        object.__setattr__(self, "config_schema", dict(self.config_schema))
        object.__setattr__(
            self,
            "dependencies",
            _coerce_dependency_groups(self.dependencies),
        )
        object.__setattr__(
            self,
            "artifact_outputs",
            _coerce_tuple(self.artifact_outputs),
        )

        if self.disabled_behavior not in {"skip", "fail"}:
            raise ValueError(
                "ModuleDefinition disabled_behavior must be 'skip' or 'fail'."
            )
        if self.retry_limit < 0:
            raise ValueError("ModuleDefinition retry_limit cannot be negative.")
        if not self.error_behavior.strip():
            raise ValueError("ModuleDefinition error_behavior is required.")

    @property
    def is_optional(self) -> bool:
        """Return whether the module may be disabled and skipped."""

        return self.disabled_behavior == "skip"


@runtime_checkable
class WorkflowModule(Protocol):
    """Interface implemented by executable workflow modules."""

    definition: ModuleDefinition

    def execute(self, context: "ModuleExecutionContext") -> "ModuleResult":
        """Execute the module for a workflow run."""

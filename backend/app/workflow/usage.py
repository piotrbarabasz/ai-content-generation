"""Minimal usage and cost tracking interfaces for workflow execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.domain.types import JsonDict


@runtime_checkable
class UsageTracker(Protocol):
    """Record optional usage metadata emitted by workflow modules."""

    def record(
        self,
        *,
        workflow_run_id: str,
        module_name: str,
        usage_metadata: JsonDict | None = None,
    ) -> None:
        """Persist or forward module usage metadata."""


@dataclass(slots=True)
class NoopCostTracker:
    """Default tracker that intentionally ignores all usage metadata."""

    def record(
        self,
        *,
        workflow_run_id: str,
        module_name: str,
        usage_metadata: JsonDict | None = None,
    ) -> None:
        """Accept usage metadata without doing anything with it."""

        return None

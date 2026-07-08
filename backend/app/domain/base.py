"""Shared domain helpers."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def utc_now() -> datetime:
    return datetime.now(UTC)


class DomainValidationError(ValueError):
    """Raised when a domain object violates canonical validation rules."""


@dataclass(slots=True)
class DomainEntity:
    id: str
    created_at: datetime = field(default_factory=utc_now)

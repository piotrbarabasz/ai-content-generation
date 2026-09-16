"""Transactional section-reorder command over an injected D003 repository."""

from typing import Protocol

from app.domain.base import DomainValidationError
from app.domain.section_order import reorder_script


class SectionOrderingRepository(Protocol):
    def active_script(self): ...
    def save_and_select(self, revision, *, expected_active_revision_id): ...


class SectionOrderingService:
    def __init__(self, repository: SectionOrderingRepository):
        self.repository = repository

    def reorder(self, section_ids, *, expected_active_revision_id):
        if type(expected_active_revision_id) is not str or not expected_active_revision_id.strip():
            raise DomainValidationError("Expected active script revision ID is required.")
        current = self.repository.active_script()
        if current.id != expected_active_revision_id:
            raise DomainValidationError("Active script changed; refresh before reordering sections.")
        result = reorder_script(current, section_ids)
        if result.impact.changed:
            self.repository.save_and_select(result.script, expected_active_revision_id=expected_active_revision_id)
        return result

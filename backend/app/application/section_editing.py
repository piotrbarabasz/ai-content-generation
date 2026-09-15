"""Transactional split/merge commands over an injected D003 project repository."""

from typing import Protocol

from app.domain.base import DomainValidationError
from app.domain.section_edit import describe_section_edit, merge_script, split_script


class SectionEditingRepository(Protocol):
    def active_script(self): ...
    def get_script(self, revision_id): ...
    def save_and_select(self, revision, *, expected_active_revision_id): ...


class SectionEditingService:
    def __init__(self, repository: SectionEditingRepository):
        self.repository = repository

    def _current(self, expected_active_revision_id):
        if type(expected_active_revision_id) is not str or not expected_active_revision_id.strip():
            raise DomainValidationError("Expected active script revision ID is required.")
        current = self.repository.active_script()
        if current.id != expected_active_revision_id:
            raise DomainValidationError("Active script changed; refresh before editing sections.")
        return current

    def split(self, section_id, boundary, *, expected_active_revision_id, left_title=None, right_title=None):
        current = self._current(expected_active_revision_id)
        result = split_script(current, section_id, boundary, left_title=left_title, right_title=right_title)
        self.repository.save_and_select(result.script, expected_active_revision_id=expected_active_revision_id)
        return result

    def merge(self, section_ids, *, expected_active_revision_id, separator="\n\n", title=None, role=None):
        current = self._current(expected_active_revision_id)
        result = merge_script(current, section_ids, separator=separator, title=title, role=role)
        self.repository.save_and_select(result.script, expected_active_revision_id=expected_active_revision_id)
        return result

    def describe(self, script_revision_id):
        child = self.repository.get_script(script_revision_id)
        if child.parent_revision_id is None:
            raise DomainValidationError("Script revision has no parent section edit.")
        return describe_section_edit(self.repository.get_script(child.parent_revision_id), child)

"""Editable project sessions, independent of Qt, HTTP and providers."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from app.domain.base import new_id
from app.domain.enums import ContentGenre, ContentType, TargetPlatform
from app.domain.project import Project
from app.domain.narrative_segment import SectionRevision
from app.domain.script import ScriptRevision
from app.application.section_editing import SectionEditingService
from app.application.section_ordering import SectionOrderingService


class ProjectRepositoryPort(Protocol):
    def project(self) -> Project: ...
    def active_script(self) -> ScriptRevision: ...
    def get_script(self, revision_id: str) -> ScriptRevision: ...
    def get_section(self, revision_id: str) -> SectionRevision: ...
    def script_history(self) -> tuple[ScriptRevision, ...]: ...
    def section_history(self, section_id: str) -> tuple[SectionRevision, ...]: ...
    def save_and_select(self, revision: ScriptRevision, *, expected_active_revision_id: str) -> None: ...
    def select_script(self, revision_id: str, *, expected_active_revision_id: str) -> None: ...
    def close(self) -> None: ...


class ProjectRepositoryFactory(Protocol):
    def create(self, workspace: Path | str, project: Project,
               initial_script: ScriptRevision) -> ProjectRepositoryPort: ...
    def open(self, workspace: Path | str) -> ProjectRepositoryPort: ...


class ProjectSession:
    """Keep a repository open for one editing session, closing it explicitly."""

    def __init__(self, repository: ProjectRepositoryPort):
        self.repository = repository

    @classmethod
    def create(cls, workspace: Path | str, *, repository_factory: ProjectRepositoryFactory,
               name: str, language: str = "en",
               content_type: ContentType | str = ContentType.SCRIPT_ONLY,
               genre: ContentGenre | str = ContentGenre.STORY,
               target_platform: TargetPlatform | str = TargetPlatform.GENERIC_EXPORT,
               tone: str = "neutral") -> "ProjectSession":
        project = Project.create(workspace_id=new_id("workspace"), name=name,
                                 content_type=content_type, genre=genre,
                                 target_platform=target_platform, language=language, tone=tone)
        script = ScriptRevision.create(project_id=project.id, language=project.language)
        return cls(repository_factory.create(workspace, project, script))

    @classmethod
    def open(cls, workspace: Path | str, *, repository_factory: ProjectRepositoryFactory) -> "ProjectSession":
        return cls(repository_factory.open(workspace))

    @property
    def project(self) -> Project:
        return self.repository.project()

    @property
    def active_script(self) -> ScriptRevision:
        return self.repository.active_script()

    def save_script(self, revision: ScriptRevision, *, expected_active_revision_id: str) -> None:
        self.repository.save_and_select(revision, expected_active_revision_id=expected_active_revision_id)

    def edit_section(self, section_id: str, *, text: str,
                     title: str | None = None, role: str | None = None) -> ScriptRevision:
        current = self.active_script
        revision = current.edit_section(section_id, text=text, title=title, role=role)
        self.save_script(revision, expected_active_revision_id=current.id)
        return revision

    def reorder_sections(self, section_ids: Sequence[str], *, expected_active_revision_id: str):
        return SectionOrderingService(self.repository).reorder(
            section_ids, expected_active_revision_id=expected_active_revision_id
        )

    def split_section(self, section_id: str, boundary: int, *, expected_active_revision_id: str,
                      left_title: str | None = None, right_title: str | None = None):
        return SectionEditingService(self.repository).split(
            section_id, boundary, expected_active_revision_id=expected_active_revision_id,
            left_title=left_title, right_title=right_title)

    def merge_sections(self, section_ids: Sequence[str], *, expected_active_revision_id: str,
                       separator: str = "\n\n", title: str | None = None, role: str | None = None):
        return SectionEditingService(self.repository).merge(
            section_ids, expected_active_revision_id=expected_active_revision_id,
            separator=separator, title=title, role=role)

    def describe_section_edit(self, script_revision_id: str):
        return SectionEditingService(self.repository).describe(script_revision_id)

    def select_section_revision(self, revision_id: str) -> ScriptRevision:
        current = self.active_script
        revision = current.select_section_revision(self.repository.get_section(revision_id))
        self.save_script(revision, expected_active_revision_id=current.id)
        return revision

    def close(self) -> None:
        self.repository.close()

    def __enter__(self) -> "ProjectSession":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

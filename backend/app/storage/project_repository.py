"""Single-session SQLite persistence for editable projects (format version 1).

All paths are resolved from a caller-owned local workspace. An EXCLUSIVE SQLite
connection retains its lock between transactions; close it before moving a project.
"""

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from app.domain.enums import ContentGenre, ContentType, TargetPlatform
from app.domain.base import new_id
from app.domain.narrative_segment import SectionRevision
from app.domain.project import Project
from app.domain.script import ScriptRevision


SCHEMA_VERSION = 1
APPLICATION_ID = 0x41494353  # AICS


class ProjectRepositoryError(ValueError):
    """The requested project operation is invalid."""


class UnsupportedSchemaError(ProjectRepositoryError):
    """Refuse unknown formats instead of initializing or migrating them."""


class ProjectWriterBusyError(ProjectRepositoryError):
    """Another session owns the project database."""


class RevisionConflictError(ProjectRepositoryError):
    """An immutable ID or expected active selection does not match stored state."""


_SCHEMA = """
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    singleton INTEGER NOT NULL UNIQUE CHECK (singleton = 1),
    metadata TEXT NOT NULL,
    workspace_ref TEXT NOT NULL CHECK (workspace_ref = '.'),
    active_script_revision_id TEXT REFERENCES script_revisions(id)
);
CREATE TABLE sections (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id)
);
CREATE TABLE section_revisions (
    id TEXT PRIMARY KEY,
    section_id TEXT NOT NULL REFERENCES sections(id),
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    role TEXT NOT NULL,
    parent_revision_id TEXT REFERENCES section_revisions(id)
);
CREATE TABLE scripts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id)
);
CREATE TABLE script_revisions (
    id TEXT PRIMARY KEY,
    script_id TEXT NOT NULL REFERENCES scripts(id),
    language TEXT NOT NULL,
    parent_revision_id TEXT REFERENCES script_revisions(id)
);
CREATE TABLE script_sections (
    script_revision_id TEXT NOT NULL REFERENCES script_revisions(id),
    position INTEGER NOT NULL CHECK (position >= 0),
    section_revision_id TEXT NOT NULL REFERENCES section_revisions(id),
    PRIMARY KEY (script_revision_id, position),
    UNIQUE (script_revision_id, section_revision_id)
);
"""


class ProjectRepository:
    """Own one project session on the calling thread; use as a context manager.

Only create/open construct sessions. No long-lived write transaction is used:
each selection commits independently while the connection's file lock persists.
Separate readers are also excluded in this minimal local single-session format.
"""

    def __init__(self, workspace: Path, connection: sqlite3.Connection):
        self.workspace = workspace
        self._connection = connection
        # Ephemeral identity for project-owned services; never a persisted path.
        self.session_id = new_id("project_session")

    @staticmethod
    def _connect(workspace: Path) -> sqlite3.Connection:
        # mode=rw prevents a typo in open() from silently creating a database.
        database = workspace / "project.sqlite"
        if not database.is_file():
            raise FileNotFoundError(database)
        connection = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True,
                                     timeout=0, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA locking_mode = EXCLUSIVE")
            return connection
        except BaseException as exc:
            connection.close()
            ProjectRepository._raise_busy(exc)
            raise

    @classmethod
    def create(cls, workspace: Path | str, project: Project,
               initial_script: ScriptRevision) -> "ProjectRepository":
        if initial_script.project_id != project.id:
            raise ProjectRepositoryError("Script belongs to a different project.")
        payload = asdict(project)
        payload["created_at"] = project.created_at.isoformat()
        metadata = json.dumps(payload, ensure_ascii=False)
        root = Path(workspace).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        # Never reuse or overwrite an existing database, even an empty one.
        with (root / "project.sqlite").open("xb"):
            pass
        repository = cls(root, cls._connect(root))
        try:
            with repository._transaction():
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        repository._connection.execute(statement)
                repository._connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                repository._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                repository._connection.execute(
                    "INSERT INTO projects VALUES (?, 1, ?, '.', NULL)", (project.id, metadata))
                repository._save_script(initial_script)
                repository._set_active(initial_script.id)
            return repository
        except BaseException:
            repository.close()
            raise

    @classmethod
    def open(cls, workspace: Path | str) -> "ProjectRepository":
        root = Path(workspace).expanduser().resolve()
        repository = cls(root, cls._connect(root))
        try:
            # Check before requesting write access to an unsupported database.
            repository._check_format()
            with repository._transaction():
                repository._check_format()
                repository.project()
                repository.active_script()
            return repository
        except BaseException as exc:
            repository.close()
            cls._raise_busy(exc)
            raise

    @staticmethod
    def _raise_busy(exc: BaseException) -> None:
        code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise ProjectWriterBusyError("Project is already open in another session.") from exc

    def _check_format(self) -> None:
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        application = self._connection.execute("PRAGMA application_id").fetchone()[0]
        if version != SCHEMA_VERSION or application != APPLICATION_ID:
            raise UnsupportedSchemaError(f"Unsupported project format: schema={version}, application={application}.")
        if self._connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise UnsupportedSchemaError("Version 1 requires the DELETE rollback journal mode.")

    @contextmanager
    def _transaction(self):
        try:
            self._connection.execute("BEGIN EXCLUSIVE")
            yield
            self._connection.commit()
        except BaseException as exc:
            self._connection.rollback()
            self._raise_busy(exc)
            raise

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "ProjectRepository":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _project_row(self) -> sqlite3.Row:
        row = self._connection.execute("SELECT * FROM projects WHERE singleton = 1").fetchone()
        if row is None:
            raise ProjectRepositoryError("Project record is missing.")
        return row

    def project(self) -> Project:
        row = self._project_row()
        payload = json.loads(row["metadata"])
        payload["created_at"] = datetime.fromisoformat(payload["created_at"])
        payload["content_type"] = ContentType(payload["content_type"])
        payload["genre"] = ContentGenre(payload["genre"])
        payload["target_platform"] = TargetPlatform(payload["target_platform"])
        project = Project(**payload)
        if project.id != row["id"] or row["workspace_ref"] != ".":
            raise ProjectRepositoryError("Project identity or workspace reference is invalid.")
        return project

    def active_script(self) -> ScriptRevision:
        return self.get_script(self._project_row()["active_script_revision_id"])

    def get_section(self, revision_id: str) -> SectionRevision:
        row = self._connection.execute(
            "SELECT r.*, s.project_id FROM section_revisions r JOIN sections s ON s.id = r.section_id WHERE r.id = ?",
            (revision_id,)).fetchone()
        if row is None:
            raise KeyError(revision_id)
        return SectionRevision(**dict(row))

    def get_script(self, revision_id: str) -> ScriptRevision:
        row = self._connection.execute(
            "SELECT r.*, s.project_id FROM script_revisions r JOIN scripts s ON s.id = r.script_id WHERE r.id = ?",
            (revision_id,)).fetchone()
        if row is None:
            raise KeyError(revision_id)
        selection = self._connection.execute(
            "SELECT section_revision_id FROM script_sections WHERE script_revision_id = ? ORDER BY position",
            (revision_id,)).fetchall()
        return ScriptRevision(**dict(row), sections=tuple(self.get_section(item[0]) for item in selection))

    def script_history(self) -> tuple[ScriptRevision, ...]:
        rows = self._connection.execute("SELECT id FROM script_revisions ORDER BY rowid").fetchall()
        return tuple(self.get_script(row[0]) for row in rows)

    def section_history(self, section_id: str) -> tuple[SectionRevision, ...]:
        rows = self._connection.execute(
            "SELECT id FROM section_revisions WHERE section_id = ? ORDER BY rowid", (section_id,)).fetchall()
        return tuple(self.get_section(row[0]) for row in rows)

    def save_and_select(self, revision: ScriptRevision, *, expected_active_revision_id: str) -> None:
        """Publish one complete snapshot and its selection, or change nothing."""
        with self._transaction():
            if self._project_row()["active_script_revision_id"] != expected_active_revision_id:
                raise RevisionConflictError("The active script has changed.")
            self._save_script(revision)
            self._set_active(revision.id)

    def select_script(self, revision_id: str, *, expected_active_revision_id: str) -> None:
        self.save_and_select(self.get_script(revision_id), expected_active_revision_id=expected_active_revision_id)

    def _set_active(self, revision_id: str) -> None:
        self._connection.execute("UPDATE projects SET active_script_revision_id = ? WHERE singleton = 1", (revision_id,))

    def _save_section(self, revision: SectionRevision) -> None:
        existing = self._connection.execute("SELECT id FROM section_revisions WHERE id = ?", (revision.id,)).fetchone()
        if existing:
            if self.get_section(revision.id) != revision:
                raise RevisionConflictError("Section revision ID already identifies different content.")
            return
        if revision.parent_revision_id is not None:
            parent = self.get_section(revision.parent_revision_id)
            if parent.section_id != revision.section_id or parent.project_id != revision.project_id:
                raise RevisionConflictError("Section parent belongs to a different identity.")
        self._connection.execute("INSERT OR IGNORE INTO sections VALUES (?, ?)", (revision.section_id, revision.project_id))
        owner = self._connection.execute("SELECT project_id FROM sections WHERE id = ?", (revision.section_id,)).fetchone()[0]
        if owner != revision.project_id:
            raise RevisionConflictError("Section identity belongs to a different project.")
        self._connection.execute("INSERT INTO section_revisions VALUES (?, ?, ?, ?, ?, ?)",
                                 (revision.id, revision.section_id, revision.title, revision.text,
                                  revision.role, revision.parent_revision_id))

    def _save_script(self, revision: ScriptRevision) -> None:
        if revision.project_id != self._project_row()["id"]:
            raise ProjectRepositoryError("Script belongs to a different project.")
        existing = self._connection.execute("SELECT id FROM script_revisions WHERE id = ?", (revision.id,)).fetchone()
        if existing:
            if self.get_script(revision.id) != revision:
                raise RevisionConflictError("Script revision ID already identifies different content.")
            return
        if revision.parent_revision_id is not None:
            parent = self.get_script(revision.parent_revision_id)
            if parent.script_id != revision.script_id:
                raise RevisionConflictError("Script parent belongs to a different identity.")
        self._connection.execute("INSERT OR IGNORE INTO scripts VALUES (?, ?)", (revision.script_id, revision.project_id))
        for section in revision.sections:
            self._save_section(section)
        self._connection.execute("INSERT INTO script_revisions VALUES (?, ?, ?, ?)",
                                 (revision.id, revision.script_id, revision.language, revision.parent_revision_id))
        self._connection.executemany("INSERT INTO script_sections VALUES (?, ?, ?)",
                                     [(revision.id, position, section.id) for position, section in enumerate(revision.sections)])

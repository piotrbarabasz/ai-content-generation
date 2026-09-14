"""Versioned publication index, separate from D003's unchanged project schema."""

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3

from .paths import contained_path, database_path
from .manifest import ArtifactManifest
from .project_repository import ProjectRepository, UnsupportedSchemaError


INDEX_APPLICATION_ID = 0x41494341  # AICA


class ProjectArtifactIndex:
    """Use only while the owning D003 session is open, on its coordinator thread."""

    def __init__(self, repository: ProjectRepository):
        self.repository = repository
        self.project_id = repository.project().id
        self.root = contained_path(repository.workspace, "artifacts")
        self.root.relative_to(repository.workspace)
        self.path = database_path(repository.workspace, "artifacts/.artifacts/index.sqlite")
        self.path.resolve().relative_to(self.root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            if any(self.path.parent.glob("*.json")):
                raise ValueError("Existing sidecars require explicit adoption; refusing an empty project index.")
            with self.path.open("xb"):
                pass
            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE owner (project_id TEXT PRIMARY KEY, root_ref TEXT NOT NULL CHECK(root_ref = 'artifacts'))")
                connection.execute("INSERT INTO owner VALUES (?, 'artifacts')", (self.project_id,))
                connection.execute("""CREATE TABLE artifacts (
                    artifact_id TEXT PRIMARY KEY, storage_key TEXT NOT NULL UNIQUE,
                    checksum TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                    manifest_json TEXT NOT NULL)""")
                connection.execute(f"PRAGMA application_id = {INDEX_APPLICATION_ID}")
                connection.execute("PRAGMA user_version = 1")
        with self._connection():
            pass

    @contextmanager
    def _connection(self):
        # Also rejects closed sessions / access from a different thread.
        if self.repository.project().id != self.project_id:
            raise ValueError("Artifact index project owner changed.")
        database_path(self.repository.workspace, self.path.relative_to(self.repository.workspace).as_posix())
        connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=0)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            application = connection.execute("PRAGMA application_id").fetchone()[0]
            if (version, application) != (1, INDEX_APPLICATION_ID):
                raise UnsupportedSchemaError("Unsupported artifact index format; no automatic migration.")
            if connection.execute("SELECT project_id, root_ref FROM owner").fetchall() != [(self.project_id, "artifacts")]:
                raise ValueError("Artifact index belongs to a different project.")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            connection.close()

    def register(self, manifest: ArtifactManifest) -> None:
        payload = json.dumps(manifest.to_payload(), sort_keys=True)
        with self._connection() as connection, connection:
            self._insert(connection, manifest, payload)

    def _insert(self, connection, manifest, payload):
        # INSERT, never replace/upsert: both identity and key are immutable.
        connection.execute("INSERT INTO artifacts VALUES (?, ?, ?, ?, ?)",
                           (manifest.artifact_id, manifest.storage_key, manifest.checksum, manifest.size_bytes, payload))

    def manifests(self) -> tuple[ArtifactManifest, ...]:
        with self._connection() as connection:
            rows = connection.execute("SELECT manifest_json FROM artifacts ORDER BY storage_key").fetchall()
        return tuple(ArtifactManifest.from_payload(json.loads(row[0])) for row in rows)

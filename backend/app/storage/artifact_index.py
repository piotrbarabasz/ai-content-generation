"""Versioned publication index, separate from D003's unchanged project schema."""

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3

from .paths import contained_path, database_path
from .manifest import ArtifactManifest
from .project_repository import ProjectRepository, UnsupportedSchemaError


INDEX_APPLICATION_ID = 0x41494341  # AICA


def matches_metadata(manifest, filters):
    """Mirror JSON-path filters for legacy sidecars and frozen owner snapshots."""
    for path, (expected, include_missing) in filters.items():
        value = manifest.metadata
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value != expected and not (include_missing and value is None):
            return False
    return True


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

    def manifest_by_id(self, artifact_id):
        return self._manifest("artifact_id", artifact_id)

    def manifest_by_key(self, storage_key):
        return self._manifest("storage_key", storage_key)

    def _manifest(self, column, value):
        # Column names are private constants; values remain bound parameters.
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT artifact_id, storage_key, checksum, size_bytes, manifest_json FROM artifacts WHERE {column}=?",
                (value,)).fetchone()
        if row is None:
            return None
        manifest = ArtifactManifest.from_payload(json.loads(row[4]))
        if (manifest.artifact_id, manifest.storage_key, manifest.checksum, manifest.size_bytes) != row[:4]:
            raise ValueError("Artifact manifest differs from its registered index identity.")
        return manifest

    def find_manifests(self, artifact_type, metadata):
        """Filter existing JSON headers in SQLite, reconstruct only matching manifests.

        Metadata has no new index/schema requirement. None as the type permits
        value-ID searches across types. Missing metadata is retained for callers
        which must validate legacy/unknown scope rather than silently skip it.
        """
        clauses, parameters = [], []
        if artifact_type is not None:
            clauses.append("json_extract(manifest_json, '$.artifact_type') = ?")
            parameters.append(artifact_type)
        for key, (value, include_missing) in metadata.items():
            path = '$.metadata.' + key
            clause = "json_extract(manifest_json, ?) = ?"
            parameters.extend((path, value))
            if include_missing:
                clause = "(" + clause + " OR json_extract(manifest_json, ?) IS NULL)"
                parameters.append(path)
            clauses.append(clause)
        with self._connection() as connection:
            rows = connection.execute("SELECT manifest_json FROM artifacts WHERE " +
                (" AND ".join(clauses) or "1") + " ORDER BY storage_key", parameters).fetchall()
        return tuple(ArtifactManifest.from_payload(json.loads(row[0])) for row in rows)

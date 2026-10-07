"""D042: one explicit, backed-up project upgrade (1 → 2), not a framework."""

from contextlib import ExitStack, closing
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from .paths import contained_path, database_path, storage_root


DURABILITY_SCHEMA = """
CREATE TABLE artifact_pins (artifact_id TEXT PRIMARY KEY, reason TEXT NOT NULL);
CREATE TABLE cleanup_items (
    run_id TEXT NOT NULL, path TEXT NOT NULL, size_bytes INTEGER NOT NULL,
    checksum TEXT NOT NULL, artifact_id TEXT, state TEXT NOT NULL
        CHECK(state IN ('pending', 'quarantined', 'removed', 'kept')),
    PRIMARY KEY(run_id, path)
);
"""

DATABASES = ("project.sqlite", "jobs.sqlite", "artifacts/.artifacts/index.sqlite")


def _checksum(path):
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def _backup(root, connections, *, checkpoint):
    parent = contained_path(root, ".backups")
    parent.mkdir(exist_ok=True)
    staging = contained_path(root, ".backups/incomplete-" + uuid4().hex[:16])
    staging.mkdir()
    entries = {}
    for source in root.rglob("*"):
        key = source.relative_to(root).as_posix()
        if key.startswith(".backups/"):
            continue
        source = contained_path(root, key)
        if not source.is_file() or any(key == db + suffix for db in DATABASES for suffix in ("-wal", "-shm", "-journal")):
            continue
        backup_key = "files/" + sha256(key.encode()).hexdigest()
        destination = contained_path(staging, backup_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if key in connections:
            # Separate reader avoids backup() deadlock on a writing connection.
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
                with closing(sqlite3.connect(destination)) as target:
                    reader.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Backup SQLite integrity check failed.")
        else:
            shutil.copy2(source, destination)
            with destination.open("ab") as durable:
                durable.flush()
                os.fsync(durable.fileno())
        entries[key] = {"file": backup_key, "checksum": _checksum(destination)}
    checkpoint("backup_files")
    with (staging / "backup.json").open("x", encoding="utf-8") as manifest:
        json.dump({"version": 1, "source_schema": 1, "files": entries}, manifest, sort_keys=True)
        manifest.flush()
        os.fsync(manifest.fileno())
    complete = contained_path(root, ".backups/schema-1-" + uuid4().hex[:16])
    staging.rename(complete)
    return complete


def migrate_project(root, *, checkpoint=lambda stage: None):
    """Reject unknown formats first; serialize all owned DB writers for backup.

    The caller must close the old desktop session/workers before upgrading.
    SQLite locks reject another coordinator. No provider code is involved.
    """
    from .project_repository import APPLICATION_ID, UnsupportedSchemaError
    root = storage_root(root)
    path = database_path(root, "project.sqlite")
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as inspection:
        version = inspection.execute("PRAGMA user_version").fetchone()[0]
        application = inspection.execute("PRAGMA application_id").fetchone()[0]
        if application != APPLICATION_ID or version not in (1, 2):
            raise UnsupportedSchemaError(f"Unsupported project format: schema={version}, application={application}.")
        if version == 2:
            return None
        required = {"projects", "scripts", "script_revisions", "sections", "section_revisions", "script_sections"}
        tables = {r[0] for r in inspection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= tables:
            raise UnsupportedSchemaError("Incomplete version 1 project; refusing upgrade.")
    with ExitStack() as stack:
        connections = {}
        for key in DATABASES:
            db = database_path(root, key)
            if not db.exists():
                continue
            connection = stack.enter_context(closing(sqlite3.connect(db.as_uri() + "?mode=rw", uri=True, timeout=0, isolation_level=None)))
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            connections[key] = connection
        main = connections["project.sqlite"]
        if main.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise UnsupportedSchemaError("Project changed during upgrade inspection.")
        backup = _backup(root, connections, checkpoint=checkpoint)
        checkpoint("backup_ready")
        # Release reserved lock to switch WAL only after a complete snapshot.
        main.rollback()
        if main.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
            raise UnsupportedSchemaError(f"Cannot normalize journal; restore backup {backup}.")
        main.execute("BEGIN EXCLUSIVE")
        try:
            if main.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise UnsupportedSchemaError("Project changed before the upgrade transaction.")
            for statement in DURABILITY_SCHEMA.split(";"):
                if statement.strip():
                    main.execute(statement)
            checkpoint("tables_created")
            main.execute("PRAGMA user_version=2")
            checkpoint("before_commit")
            main.commit()
        except BaseException:
            main.rollback()
            raise
        return backup


def restore_backup(backup, destination):
    """Restore to a new folder only; never silently replace/downgrade a project."""
    backup = storage_root(backup)
    target = Path(destination).expanduser().absolute()
    if target.exists():
        raise FileExistsError("Restore destination must be a new folder.")
    payload = json.loads(contained_path(backup, "backup.json").read_text(encoding="utf-8"))
    if (type(payload.get("version")) is not int or payload["version"] != 1
            or type(payload.get("source_schema")) is not int or payload["source_schema"] != 1
            or not isinstance(payload.get("files"), dict) or "project.sqlite" not in payload["files"]):
        raise ValueError("Unsupported backup format.")
    # Validate *all* destination keys before creating anything, including a
    # malicious/corrupt manifest with a traversal in an original path.
    for key, entry in payload["files"].items():
        contained_path(storage_root(target.parent) / target.name, key)
        if _checksum(contained_path(backup, entry["file"])) != entry["checksum"]:
            raise ValueError("Backup checksum mismatch.")
    target.mkdir(parents=True)
    target = storage_root(target)
    for key, entry in payload["files"].items():
        output = contained_path(target, key)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(contained_path(backup, entry["file"]), output)
    return target

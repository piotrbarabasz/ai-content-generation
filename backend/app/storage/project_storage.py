"""D047 conservative graph analysis and journaled, explicit project cleanup.

Unknown registered media is retained. Only unregistered owned artifact files,
explicitly disposable unreferenced catalog entries, verified preview cache and
completed job workspaces are eligible. Runtime/model folders are never candidates.
"""

from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import file_digest, sha256
import json
import sqlite3
from uuid import uuid4

from app.application.project_storage import StorageFile, StorageReport
from .manifest import ArtifactManifest
from .paths import contained_path, database_path


ACTIVE = "Active media"
HISTORY = "History"
PINNED = "Pinned / protected"
JOBS = "Active / interrupted jobs"
CACHE = "Regenerable cache"
TEMP = "Temporary workspace"
ORPHAN = "Unused files"
OTHER = "Project records / other protected files"


def checksum(path):
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        yield from value.keys()
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


class ProjectStorage:
    def __init__(self, repository, *, clock=lambda: datetime.now(UTC), grace=timedelta(days=7), checkpoint=lambda stage: None):
        if grace < timedelta(0):
            raise ValueError("Grace period must not be negative.")
        self.repository, self.root = repository, repository.workspace
        self.clock, self.grace, self.checkpoint = clock, grace, checkpoint

    def _catalog(self):
        path = database_path(self.root, "artifacts/.artifacts/index.sqlite")
        if not path.exists():
            return (), {}, set()
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as connection:
            if connection.execute("PRAGMA application_id").fetchone()[0] != 0x41494341 or connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("Unsupported artifact catalog; cleanup refused.")
            if connection.execute("SELECT project_id, root_ref FROM owner").fetchall() != [(self.repository.project().id, "artifacts")]:
                raise ValueError("Artifact catalog belongs to another project; cleanup refused.")
            manifests = tuple(ArtifactManifest.from_payload(json.loads(row[0])) for row in connection.execute("SELECT manifest_json FROM artifacts"))
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            heads = dict(connection.execute("SELECT output_key, artifact_id FROM d040_heads WHERE artifact_id IS NOT NULL")) if "d040_heads" in tables else {}
            results = {r[0] for r in connection.execute("SELECT artifact_id FROM d040_results")} if "d040_results" in tables else set()
        return manifests, heads, results

    def pin(self, artifact_id, *, reason="Keep this artifact"):
        if artifact_id not in {m.artifact_id for m in self._catalog()[0]}:
            raise ValueError("Cannot pin an unknown artifact.")
        with self.repository._transaction():
            self.repository._connection.execute("INSERT INTO artifact_pins VALUES (?, ?) ON CONFLICT(artifact_id) DO UPDATE SET reason=excluded.reason", (artifact_id, reason))

    def artifacts(self):
        return self._catalog()[0]

    def pins(self):
        self.repository.project()
        return dict(self.repository._connection.execute("SELECT artifact_id, reason FROM artifact_pins"))

    def unpin(self, artifact_id):
        with self.repository._transaction():
            self.repository._connection.execute("DELETE FROM artifact_pins WHERE artifact_id=?", (artifact_id,))

    def _jobs(self):
        path = database_path(self.root, "jobs.sqlite")
        if not path.exists():
            return {}, set(), set()
        active, completed, payloads = set(), set(), {}
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as connection:
            if connection.execute("PRAGMA application_id").fetchone()[0] != 0x4149434A or connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("Unsupported job queue; cleanup refused.")
            if connection.execute("SELECT project_id FROM owner").fetchall() != [(self.repository.project().id,)]:
                raise ValueError("Job queue belongs to another project; cleanup refused.")
            for job_id, raw in connection.execute("SELECT id, request_json FROM jobs"):
                payloads[job_id] = json.loads(raw)
                attempts = connection.execute("SELECT status FROM attempts WHERE job_id=? ORDER BY number DESC", (job_id,)).fetchall()
                if not attempts or attempts[0][0] in ("queued", "running", "interrupted"):
                    active.add(job_id)
                elif attempts[0][0] == "completed":
                    completed.add(job_id)
        return payloads, active, completed

    def analyze(self):
        self.repository.project()  # Live coordinator ownership, same-thread guard.
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("Storage clock must include timezone.")
        manifests, heads, results = self._catalog()
        by_id = {m.artifact_id: m for m in manifests}
        keys = {"artifacts/" + m.storage_key: m for m in manifests}
        values = {m.metadata.get("value_id"): m.artifact_id for m in manifests if m.metadata.get("value_id")}
        aliases = {**{k: k for k in by_id}, **values,
                   **{m.storage_key: m.artifact_id for m in manifests},
                   **{k: m.artifact_id for k, m in keys.items()}}
        refs, payloads = {}, {}
        path_refs = set()
        for manifest in manifests:
            data = manifest.metadata
            path = contained_path(self.root, "artifacts/" + manifest.storage_key)
            if path.suffix == ".json" and path.is_file():
                # Catalogued history must be readable before destructive analysis.
                if checksum(path) != manifest.checksum:
                    raise ValueError("History checksum mismatch; cleanup refused.")
                data = {"metadata": data, "payload": json.loads(path.read_text(encoding="utf-8"))}
                payloads[manifest.artifact_id] = data["payload"]
            tokens = set(strings(data))
            refs[manifest.artifact_id] = {aliases[t] for t in tokens if t in aliases} - {manifest.artifact_id}
            path_refs.update(tokens)
        active = set(heads.values())
        # Explicit immutable selection chains, independent of timestamp ordering.
        selections = [m for m in manifests if m.artifact_type.endswith("_selection")]
        parents = {p.get("parent_selection_id") for m in selections for p in [payloads.get(m.artifact_id, {})]}
        for manifest in selections:
            if manifest.metadata.get("value_id") not in parents:
                active |= refs[manifest.artifact_id]
        timelines = [m for m in manifests if m.artifact_type == "desktop_timeline_edit"]
        timeline_parents = {payloads.get(m.artifact_id, {}).get("parent") for m in timelines}
        active |= {m.artifact_id for m in timelines if m.metadata.get("value_id") not in timeline_parents}
        pinned = {r[0] for r in self.repository._connection.execute("SELECT artifact_id FROM artifact_pins")}
        pinned |= {m.artifact_id for m in manifests if m.metadata.get("pinned") is True}
        jobs, active_jobs, completed_jobs = self._jobs()
        job_refs = set()
        for job_id in active_jobs:
            tokens = set(strings(jobs[job_id]))
            job_refs |= {aliases[t] for t in tokens if t in aliases}
            path_refs |= tokens
        # Known revision/variant records are history even if an accidental
        # disposable hint is present. Generic unreferenced files may opt in.
        retained = {m.artifact_id for m in manifests
                    if m.metadata.get("disposable") is not True
                    or m.metadata.get("value_id") is not None
                    or m.artifact_type in ("scene_image", "reference_audio_source")
                    or "section_audio" in m.metadata} | results
        def closure(roots):
            found, pending = set(roots), list(roots)
            while pending:
                for item in refs.get(pending.pop(), ()):
                    if item not in found:
                        found.add(item)
                        pending.append(item)
            return found
        pinned, job_refs, active, retained = map(closure, (pinned, job_refs, active, retained))
        protected = pinned | job_refs | active | retained
        completed_names = completed_jobs | {sha256(j.encode()).hexdigest() for j in completed_jobs}
        active_names = active_jobs | {sha256(j.encode()).hexdigest() for j in active_jobs}
        files = []
        for discovered in sorted(self.root.rglob("*")):
            key = discovered.relative_to(self.root).as_posix()
            path = contained_path(self.root, key)  # Fail closed on links/junctions.
            if not path.is_file():
                continue
            stat = path.stat()
            old = now.timestamp() - stat.st_mtime >= self.grace.total_seconds()
            manifest = keys.get(key)
            category, eligible, reason = OTHER, False, "Project records, backups, runtime or unknown files are protected."
            if manifest:
                identity = manifest.artifact_id
                category = (PINNED if identity in pinned else JOBS if identity in job_refs else
                            ACTIVE if identity in active else HISTORY if identity in protected else ORPHAN)
                eligible = identity not in protected and old
                reason = "Retained or referenced media." if identity in protected else "Unreferenced disposable artifact; seven-day default grace applies."
            elif key in path_refs or str(path) in path_refs:
                category, reason = HISTORY, "Path referenced by retained history or job inputs."
            elif key.startswith("cache/previews/"):
                category, eligible, reason = CACHE, not active_jobs, "Regenerable project preview; kept while jobs may be using it."
            elif key.startswith("artifacts/") and not key.startswith("artifacts/.artifacts/"):
                category, eligible, reason = ORPHAN, old, "Unregistered owned file; grace period applies."
            elif key.startswith("work/"):
                parts = set(key.split("/"))
                if parts & active_names or active_jobs and not parts & completed_names:
                    category, reason = JOBS, "Active/interrupted job workspace, or ownership is ambiguous."
                elif parts & completed_names:
                    category, eligible, reason = TEMP, old, "Completed job workspace; grace period applies."
                elif key.startswith(("work/render/", "work/preview/", "work/tempo/")):
                    category, eligible, reason = TEMP, old, "Owned render/preview/tempo workspace; no resumable job uses it."
            files.append(StorageFile(key, stat.st_size, checksum(path) if eligible else "", category,
                                     eligible, reason, manifest.artifact_id if manifest else None))
        return StorageReport(tuple(files))

    def pending_runs(self):
        self.repository.project()
        return tuple(r[0] for r in self.repository._connection.execute("SELECT DISTINCT run_id FROM cleanup_items WHERE state IN ('pending', 'quarantined') ORDER BY run_id"))

    def cleanup(self, preview):
        """Explicit action only. Revalidate the preview before persisting its intent."""
        current = {item.path: item for item in self.analyze().candidates}
        candidates = tuple(preview.candidates)
        if any(current.get(item.path) != item for item in candidates):
            raise ValueError("Storage changed; refresh the cleanup preview.")
        run_id = uuid4().hex
        with self.repository._transaction():
            self.repository._connection.executemany("INSERT INTO cleanup_items VALUES (?, ?, ?, ?, ?, 'pending')",
                [(run_id, item.path, item.size_bytes, item.checksum, item.artifact_id) for item in candidates])
        self.checkpoint("intent_committed")
        self.resume_cleanup(run_id)
        return self.analyze()

    def resume_cleanup(self, run_id):
        """Resume only on explicit action, rechecking references and file identity."""
        rows = self.repository._connection.execute("SELECT path, size_bytes, checksum, artifact_id, state FROM cleanup_items WHERE run_id=? AND state IN ('pending', 'quarantined') ORDER BY path", (run_id,)).fetchall()
        for key, size, digest, artifact_id, previous_state in rows:
            path = contained_path(self.root, key)
            quarantine = contained_path(self.root, ".cleanup/" + run_id + "/" + sha256(key.encode()).hexdigest())
            if quarantine.exists() and checksum(quarantine) != digest:
                raise ValueError("Cleanup quarantine changed; refusing deletion.")
            if previous_state == "quarantined":
                quarantine.unlink(missing_ok=True)
                self._record_state(run_id, key, "removed")
                continue
            if quarantine.exists():
                if path.exists():
                    raise ValueError("Cleanup original and quarantine both exist; manual recovery required.")
                # A crash before the catalog transaction leaves the original
                # restorable, including if it became pinned after restart.
                quarantine.rename(path)
            current = {item.path: item for item in self.analyze().candidates}
            candidate = current.get(key)
            if path.exists() and (candidate is None or candidate.checksum != digest or candidate.size_bytes != size):
                state = "kept"
            else:
                # Same-volume rename preserves bytes until the catalog/journal
                # transaction commits. No broad directory deletion is used.
                if path.exists():
                    quarantine.parent.mkdir(parents=True, exist_ok=True)
                    path.rename(quarantine)
                self.checkpoint("file_removed")
                state = "removed"
            if artifact_id and state == "removed":
                self._forget_disposable(artifact_id, run_id, key)
            elif state == "removed":
                self._record_state(run_id, key, "quarantined")
            if state == "removed":
                self.checkpoint("catalog_committed")
                quarantine.unlink(missing_ok=True)
            self._record_state(run_id, key, state)
            self.checkpoint("item_committed")
        return self.analyze()

    def _record_state(self, run_id, key, state):
        with self.repository._transaction():
            self.repository._connection.execute("UPDATE cleanup_items SET state=? WHERE run_id=? AND path=?", (state, run_id, key))

    def _forget_disposable(self, artifact_id, run_id, key):
        path = database_path(self.root, "artifacts/.artifacts/index.sqlite")
        connection = self.repository._connection
        connection.execute("ATTACH DATABASE ? AS cleanup_catalog", (str(path),))
        try:
            if connection.execute("PRAGMA cleanup_catalog.journal_mode").fetchone()[0] != "delete":
                raise ValueError("Cleanup catalog requires DELETE journals.")
            with self.repository._transaction():
                connection.execute("DELETE FROM cleanup_catalog.artifacts WHERE artifact_id=?", (artifact_id,))
                connection.execute("UPDATE cleanup_items SET state='quarantined' WHERE run_id=? AND path=?", (run_id, key))
        finally:
            connection.execute("DETACH DATABASE cleanup_catalog")

"""Project-session-owned SQLite queue; no executor, polling thread or provider I/O."""

from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime
import json
import sqlite3

from app.domain.base import DomainValidationError, new_id, utc_now
from app.domain.generation_job import (
    AttemptStatus, JobAttempt, JobProgress, JobRequest, artifact_ids, require_text, require_time,
)
from app.storage.project_repository import ProjectRepository, UnsupportedSchemaError


APPLICATION_ID = 0x4149434A  # AICJ, independently versioned from project/artifact indexes.


class JobConflictError(ValueError):
    """An event is stale, not owned, or not a valid transition."""


class JobQueueBusyError(ValueError):
    """Another SQLite transaction currently owns the queue write lock."""


_SCHEMA = """
CREATE TABLE owner (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1), project_id TEXT NOT NULL,
    session_id TEXT NOT NULL, paused INTEGER NOT NULL CHECK(paused IN (0, 1))
);
CREATE TABLE jobs (id TEXT PRIMARY KEY, request_json TEXT NOT NULL);
CREATE TABLE attempts (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
    number INTEGER NOT NULL CHECK(number > 0),
    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'completed', 'failed', 'canceled', 'interrupted')),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    owner TEXT, claim_token TEXT UNIQUE, cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1)),
    progress_json TEXT, output_artifact_ids TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '',
    UNIQUE(job_id, number),
    CHECK(status != 'running' OR (owner IS NOT NULL AND claim_token IS NOT NULL AND started_at IS NOT NULL)),
    CHECK((status IN ('queued', 'running')) = (finished_at IS NULL))
);
CREATE UNIQUE INDEX one_active_attempt ON attempts(job_id) WHERE status IN ('queued', 'running');
"""


class JobRepository:
    """Use on the D003 session's coordinator thread; every command commits alone.

    A new D003 session proves the old project writer released its exclusive lock.
    Its first queue open recovers running attempts exactly once. Opening another
    queue adapter in the same live session must never interrupt live claims.
    """

    def __init__(self, project: ProjectRepository, *, clock=utc_now):
        self.project = project
        self.project_id = project.project().id
        self.session_id = project.session_id
        self.path = project.workspace / "jobs.sqlite"
        self.path.resolve().relative_to(project.workspace)
        now = clock()
        require_time(now)
        if not self.path.exists():
            with self.path.open("xb"):
                pass
            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute("INSERT INTO owner VALUES (1, ?, ?, 0)", (self.project_id, self.session_id))
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                connection.execute("PRAGMA user_version = 1")
        self.recovered_attempt_ids = ()
        with self._connection() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT session_id FROM owner").fetchone()[0] != self.session_id:
                running = connection.execute("SELECT * FROM attempts WHERE status = 'running' ORDER BY rowid").fetchall()
                for row in running:
                    stamp = max(now, datetime.fromisoformat(row["updated_at"])).isoformat()
                    connection.execute("""UPDATE attempts SET status = 'interrupted', updated_at = ?, finished_at = ?,
                        error = 'Owning project session ended before an outcome was recorded.' WHERE id = ?""",
                                       (stamp, stamp, row["id"]))
                connection.execute("UPDATE owner SET session_id = ?", (self.session_id,))
                self.recovered_attempt_ids = tuple(row["id"] for row in running)

    @contextmanager
    def _connection(self):
        # Reject closed sessions, foreign threads and changed project identities.
        if self.project.project().id != self.project_id or self.project.session_id != self.session_id:
            raise JobConflictError("Queue project session changed.")
        connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            application = connection.execute("PRAGMA application_id").fetchone()[0]
            journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
            if (version, application, journal) != (1, APPLICATION_ID, "delete"):
                raise UnsupportedSchemaError("Unsupported job queue format; no automatic migration.")
            owners = connection.execute("SELECT project_id FROM owner").fetchall()
            if len(owners) != 1 or owners[0][0] != self.project_id:
                raise JobConflictError("Queue belongs to a different project.")
            yield connection
        except sqlite3.OperationalError as exc:
            if (getattr(exc, "sqlite_errorcode", 0) & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise JobQueueBusyError("Job queue is busy; retry the command after the transaction ends.") from exc
            raise
        finally:
            connection.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT session_id FROM owner").fetchone()[0] != self.session_id:
                raise JobConflictError("Queue ownership has changed.")
            yield connection

    @staticmethod
    def _attempt(connection, attempt_id: str) -> JobAttempt:
        row = connection.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
        if row is None:
            raise KeyError(attempt_id)
        values = dict(row)
        for key in ("created_at", "updated_at", "started_at", "finished_at"):
            values[key] = datetime.fromisoformat(values[key]) if values[key] is not None else None
        values["status"] = AttemptStatus(values["status"])
        values["cancel_requested"] = bool(values["cancel_requested"])
        progress = values.pop("progress_json")
        values["progress"] = JobProgress(**json.loads(progress)) if progress is not None else None
        values["output_artifact_ids"] = tuple(json.loads(values["output_artifact_ids"]))
        return JobAttempt(**values)

    def get_attempt(self, attempt_id: str) -> JobAttempt:
        with self._connection() as connection:
            return self._attempt(connection, attempt_id)

    def get_job(self, job_id: str) -> JobRequest:
        with self._connection() as connection:
            row = connection.execute("SELECT request_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            return JobRequest.from_payload(json.loads(row[0]))

    def jobs(self) -> tuple[JobRequest, ...]:
        with self._connection() as connection:
            return tuple(JobRequest.from_payload(json.loads(row[0])) for row in
                         connection.execute("SELECT request_json FROM jobs ORDER BY rowid"))

    def attempts(self, job_id: str) -> tuple[JobAttempt, ...]:
        with self._connection() as connection:
            return tuple(self._attempt(connection, row[0]) for row in connection.execute(
                "SELECT id FROM attempts WHERE job_id = ? ORDER BY number", (job_id,)).fetchall())

    @staticmethod
    def _enqueue_attempt(connection, job_id, number, now):
        attempt_id = new_id("attempt")
        connection.execute("""INSERT INTO attempts (id, job_id, number, status, created_at, updated_at)
                            VALUES (?, ?, ?, 'queued', ?, ?)""", (attempt_id, job_id, number, now.isoformat(), now.isoformat()))
        return JobRepository._attempt(connection, attempt_id)

    def enqueue(self, job: JobRequest) -> JobAttempt:
        with self._transaction() as connection:
            if connection.execute("SELECT 1 FROM jobs WHERE id = ?", (job.id,)).fetchone():
                raise JobConflictError("Job IDs are immutable and cannot be enqueued twice.")
            connection.execute("INSERT INTO jobs VALUES (?, ?)", (job.id, json.dumps(job.to_payload())))
            return self._enqueue_attempt(connection, job.id, 1, job.created_at)

    def retry(self, job_id: str, now: datetime) -> JobAttempt:
        require_time(now)
        with self._transaction() as connection:
            row = connection.execute("SELECT id FROM attempts WHERE job_id = ? ORDER BY number DESC LIMIT 1", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            previous = self._attempt(connection, row[0])
            if previous.status not in (AttemptStatus.FAILED, AttemptStatus.CANCELED, AttemptStatus.INTERRUPTED):
                raise JobConflictError("Retry requires a failed, canceled or interrupted latest attempt.")
            return self._enqueue_attempt(connection, job_id, previous.number + 1, max(now, previous.updated_at))

    @property
    def paused(self) -> bool:
        with self._connection() as connection:
            return bool(connection.execute("SELECT paused FROM owner").fetchone()[0])

    def set_paused(self, paused: bool) -> None:
        if type(paused) is not bool:
            raise DomainValidationError("Paused must be a boolean.")
        with self._transaction() as connection:
            connection.execute("UPDATE owner SET paused = ?", (int(paused),))

    def claim_next(self, owner: str, now: datetime) -> JobAttempt | None:
        require_text(owner, "claim owner")
        require_time(now)
        with self._transaction() as connection:
            if connection.execute("SELECT paused FROM owner").fetchone()[0]:
                return None
            row = connection.execute("SELECT id FROM attempts WHERE status = 'queued' ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return None
            attempt = self._attempt(connection, row[0])
            stamp = max(now, attempt.updated_at).isoformat()
            changed = connection.execute("""UPDATE attempts SET status = 'running', owner = ?, claim_token = ?,
                started_at = ?, updated_at = ? WHERE id = ? AND status = 'queued'""",
                                         (owner, new_id("claim"), stamp, stamp, attempt.id)).rowcount
            if changed != 1:
                raise JobConflictError("Attempt was already claimed.")
            return self._attempt(connection, attempt.id)

    @staticmethod
    def _owned(connection, attempt_id, token):
        attempt = JobRepository._attempt(connection, attempt_id)
        if attempt.status != AttemptStatus.RUNNING or not token or attempt.claim_token != token:
            raise JobConflictError("Event requires the current running attempt's claim token.")
        return attempt

    def report_progress(self, attempt_id: str, token: str, progress: JobProgress, now: datetime) -> JobAttempt:
        require_time(now)
        if not isinstance(progress, JobProgress):
            raise DomainValidationError("Progress requires a JobProgress value.")
        with self._transaction() as connection:
            attempt = self._owned(connection, attempt_id, token)
            old = attempt.progress
            if (old is not None and old.phase == progress.phase and old.completed is not None
                    and (progress.completed is None or progress.completed < old.completed)):
                raise JobConflictError("Progress counts cannot regress within a phase.")
            connection.execute("UPDATE attempts SET progress_json = ?, updated_at = ? WHERE id = ?",
                               (json.dumps(asdict(progress)), max(now, attempt.updated_at).isoformat(), attempt_id))
            return self._attempt(connection, attempt_id)

    def cancel(self, attempt_id: str, now: datetime) -> JobAttempt:
        require_time(now)
        with self._transaction() as connection:
            attempt = self._attempt(connection, attempt_id)
            stamp = max(now, attempt.updated_at).isoformat()
            if attempt.status == AttemptStatus.QUEUED:
                connection.execute("""UPDATE attempts SET status = 'canceled', cancel_requested = 1,
                    updated_at = ?, finished_at = ? WHERE id = ?""", (stamp, stamp, attempt_id))
            elif attempt.status == AttemptStatus.RUNNING and not attempt.cancel_requested:
                connection.execute("UPDATE attempts SET cancel_requested = 1, updated_at = ? WHERE id = ?", (stamp, attempt_id))
            return self._attempt(connection, attempt_id)

    def finish(self, attempt_id: str, token: str, status: AttemptStatus, now: datetime, *,
               output_artifact_ids=(), error: str = "") -> JobAttempt:
        require_time(now)
        status = AttemptStatus(status)
        outputs = artifact_ids(output_artifact_ids)
        if status not in (AttemptStatus.COMPLETED, AttemptStatus.FAILED, AttemptStatus.CANCELED):
            raise JobConflictError("Only completion, failure or acknowledged cancellation can finish a claim.")
        if status == AttemptStatus.FAILED:
            require_text(error, "failure error")
        elif error:
            raise DomainValidationError("Only failed attempts accept an error.")
        if status != AttemptStatus.COMPLETED and outputs:
            raise DomainValidationError("Only completed attempts accept result references.")
        with self._transaction() as connection:
            attempt = self._owned(connection, attempt_id, token)
            if status == AttemptStatus.CANCELED and not attempt.cancel_requested:
                raise JobConflictError("Cancellation must be requested before acknowledgement.")
            if status == AttemptStatus.COMPLETED and attempt.cancel_requested:
                raise JobConflictError("A canceled claim cannot publish completion.")
            stamp = max(now, attempt.updated_at).isoformat()
            connection.execute("""UPDATE attempts SET status = ?, updated_at = ?, finished_at = ?,
                output_artifact_ids = ?, error = ? WHERE id = ?""",
                               (status.value, stamp, stamp, json.dumps(outputs), error, attempt_id))
            return self._attempt(connection, attempt_id)

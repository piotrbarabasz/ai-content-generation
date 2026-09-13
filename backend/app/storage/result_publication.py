"""D040 extension of the existing artifact index, sharing a transaction with D006."""

from contextlib import contextmanager
from dataclasses import asdict
import json
import sqlite3

from app.domain.base import new_id, utc_now
from app.domain.dependencies import (
    DependencyDeclaration, DEPENDENCY_METADATA_KEY, Provenance, RequestFingerprint,
    artifact_fingerprint, content_fingerprint,
)
from app.domain.generation_job import AttemptStatus, JobRequest
from app.domain.publication import (
    PUBLICATION_KEY, PublicationConflictError, PublicationResult, PublicationSnapshot,
)
from app.jobs.repository import JobRepository
from .artifact_index import ProjectArtifactIndex
from .project_repository import UnsupportedSchemaError


_SCHEMA = """
CREATE TABLE d040_format (version INTEGER NOT NULL);
INSERT INTO d040_format VALUES (1);
CREATE TABLE d040_heads (
    output_key TEXT PRIMARY KEY, generation_id TEXT NOT NULL,
    artifact_id TEXT REFERENCES artifacts(artifact_id)
);
CREATE TABLE d040_results (
    attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, output_key TEXT NOT NULL,
    artifact_id TEXT NOT NULL UNIQUE REFERENCES artifacts(artifact_id),
    selected_at_publication INTEGER NOT NULL CHECK(selected_at_publication IN (0,1)),
    reason TEXT NOT NULL
);
"""


class ResultArtifactIndex(ProjectArtifactIndex):
    """Compose LocalArtifactStore with this index for coordinator-owned results.

    Base D004 imports keep their semantics. Only this gate writes D040 selections.
    Project revisions remain protected by the D003 exclusive session and SQLite's
    same-thread guard; there is no await/callback between comparison and commit.
    """

    def __init__(self, project, jobs: JobRepository, *, clock=utc_now):
        if jobs.project is not project:
            raise PublicationConflictError("Publication and jobs require the same project session.")
        self.jobs, self.clock = jobs, clock
        super().__init__(project)
        with self._connection() as connection, connection:
            self._check_journal(connection)
            connection.execute("BEGIN IMMEDIATE")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "d040_format" not in tables:
                if any(name.startswith("d040_") for name in tables):
                    raise UnsupportedSchemaError("Incomplete D040 index extension; refusing implicit repair.")
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        connection.execute(statement)
            self._check_extension(connection)

    @staticmethod
    def _check_journal(connection):
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise UnsupportedSchemaError("Result publication requires DELETE rollback journals.")

    @staticmethod
    def _check_extension(connection):
        if [tuple(row) for row in connection.execute("SELECT version FROM d040_format")] != [(1,)]:
            raise UnsupportedSchemaError("Unsupported D040 publication extension.")

    @contextmanager
    def _transaction(self):
        with self._connection() as connection, connection:
            self._check_journal(connection)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            self.jobs.attach_for_publication(connection)
            connection.execute("BEGIN IMMEDIATE")
            self._check_extension(connection)
            yield connection

    def capture(self, expected_sections, script_revision_id=None):
        if not isinstance(expected_sections, dict) or not expected_sections:
            raise PublicationConflictError("Expected section revisions must be explicit.")
        active = self.repository.active_script()
        if script_revision_id is not None and script_revision_id != active.id:
            raise PublicationConflictError("Expected script revision is no longer active.")
        sections = []
        for section_id, revision_id in expected_sections.items():
            try:
                section = active.section(section_id)
            except KeyError as exc:
                raise PublicationConflictError("Expected section is not active in this project.") from exc
            if section.id != revision_id:
                raise PublicationConflictError("Expected section revision is no longer active.")
            sections.append(section)
        return PublicationSnapshot(self.project_id, new_id("generation"), tuple(sections), script_revision_id)

    def _valid_snapshot(self, job):
        snapshot = PublicationSnapshot.from_job(job)
        if snapshot.project_id != self.project_id:
            raise PublicationConflictError("Publication snapshot belongs to another project.")
        for section in snapshot.sections:
            if self.repository.get_section(section.id) != section:
                raise PublicationConflictError("Job changed its immutable section inputs.")
        if snapshot.script_revision_id is not None:
            script = self.repository.get_script(snapshot.script_revision_id)
            if any(script.section(section.section_id) != section for section in snapshot.sections):
                raise PublicationConflictError("Script and section snapshots do not agree.")
        actual = {edge.name: edge for edge in job.request.inputs}
        for edge in snapshot.bind(RequestFingerprint.create("verify", "1")).inputs:
            if actual.get(edge.name) != edge:
                raise PublicationConflictError("Request fingerprint does not bind the enqueued revisions.")
        return snapshot

    def _matches(self, snapshot):
        active = self.repository.active_script()
        current = {s.section_id: s.id for s in active.sections}
        return ((snapshot.script_revision_id is None or snapshot.script_revision_id == active.id)
                and all(current.get(s.section_id) == s.id for s in snapshot.sections))

    def _artifact_inputs_match(self, connection, request, visiting=frozenset()):
        """Compare consumed references, including their known editorial ancestry.

        This is publication eligibility, not a planner/current-settings resolver.
        Manual bytes remain reusable when their editorial context changes (D005).
        """
        matches = True
        active = self.repository.active_script()
        revisions = {"section:" + s.section_id + ":revision": content_fingerprint(asdict(s))
                     for s in active.sections}
        revisions["project:script_revision"] = content_fingerprint(active.id)
        for edge in request.inputs:
            if edge.artifact_id is None:
                if (edge.key == "project:script_revision"
                        or edge.key.startswith("section:") and edge.key.endswith(":revision")):
                    matches = matches and revisions.get(edge.key) == edge.fingerprint
                continue
            if edge.artifact_id in visiting:
                raise PublicationConflictError("Consumed artifact dependency cycle.")
            row = connection.execute("SELECT checksum, manifest_json FROM artifacts WHERE artifact_id=?", (edge.artifact_id,)).fetchone()
            if row is None or artifact_fingerprint(edge.artifact_id, row[0]) != edge.fingerprint:
                raise PublicationConflictError("Consumed artifact is missing or has a different checksum.")
            metadata = json.loads(row[1])["metadata"]
            if DEPENDENCY_METADATA_KEY in metadata:
                declaration = DependencyDeclaration.from_payload(metadata[DEPENDENCY_METADATA_KEY])
                if declaration.output_key != edge.key:
                    raise PublicationConflictError("Consumed artifact belongs to another output binding.")
                if declaration.provenance != Provenance.MANUAL:
                    upstream = self._artifact_inputs_match(connection, declaration.request, visiting | {edge.artifact_id})
                    matches = matches and upstream
            selected = connection.execute("SELECT artifact_id FROM d040_heads WHERE output_key=?", (edge.key,)).fetchone()
            if selected is not None and selected[0] != edge.artifact_id:
                matches = False
        return matches

    def enqueue(self, job):
        with self._transaction() as connection:
            snapshot = self._valid_snapshot(job)
            if not self._matches(snapshot) or not self._artifact_inputs_match(connection, job.request):
                raise PublicationConflictError("Enqueue inputs are no longer selected.")
            attempt = self.jobs.enqueue_attached(connection, job)
            connection.execute("""INSERT INTO d040_heads VALUES (?, ?, NULL)
                ON CONFLICT(output_key) DO UPDATE SET generation_id=excluded.generation_id""",
                               (job.output_key, snapshot.generation_id))
            return attempt

    def selected(self):
        with self._connection() as connection:
            self._check_extension(connection)
            return dict(connection.execute("SELECT output_key, artifact_id FROM d040_heads WHERE artifact_id IS NOT NULL"))

    def prepare(self, claim):
        actual = self.jobs.get_attempt(claim.id)
        if (actual.job_id != claim.job_id or actual.claim_token != claim.claim_token or not claim.claim_token
                or actual.status != AttemptStatus.RUNNING or actual.cancel_requested):
            raise PublicationConflictError("Publication requires the current uncanceled running claim.")
        job = self.jobs.get_job(actual.job_id)
        self._valid_snapshot(job)
        return job

    def result(self, claim):
        actual = self.jobs.get_attempt(claim.id)
        if actual.job_id != claim.job_id or not claim.claim_token or actual.claim_token != claim.claim_token:
            raise PublicationConflictError("Completion replay does not own the attempt.")
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM d040_results WHERE attempt_id=?", (claim.id,)).fetchone()
        if row is None:
            return None
        # SQL order starts with attempt_id; the value contract starts with job_id.
        result = PublicationResult(row[1], row[0], row[3], bool(row[4]), row[5])
        if actual.status != AttemptStatus.COMPLETED or actual.output_artifact_ids != (result.artifact_id,):
            raise PublicationConflictError("Publication and queue outcome do not agree.")
        return result

    def history(self, output_key):
        with self._connection() as connection:
            rows = connection.execute("SELECT job_id, attempt_id, artifact_id, selected_at_publication, reason FROM d040_results WHERE output_key=? ORDER BY rowid", (output_key,)).fetchall()
        return tuple(PublicationResult(row[0], row[1], row[2], bool(row[3]), row[4]) for row in rows)

    def _select(self, connection, output_key, artifact_id):
        connection.execute("UPDATE d040_heads SET artifact_id=? WHERE output_key=?", (artifact_id, output_key))

    def register(self, manifest):
        if PUBLICATION_KEY not in manifest.metadata:
            return super().register(manifest)
        completion = manifest.metadata[PUBLICATION_KEY]
        if (set(completion) != {"version", "job_id", "attempt_id", "claim_token", "input_snapshot"}
                or type(completion["version"]) is not int or completion["version"] != 1):
            raise PublicationConflictError("Invalid result publication metadata.")
        # Resolve injected callbacks before the final comparison/commit window.
        now = self.clock()
        with self._transaction() as connection:
            attempt = self.jobs._owned(connection, completion["attempt_id"], completion["claim_token"], "job_queue")
            if attempt.job_id != completion["job_id"] or attempt.cancel_requested:
                raise PublicationConflictError("Result belongs to a different or canceled attempt.")
            row = connection.execute("SELECT request_json FROM job_queue.jobs WHERE id=?", (attempt.job_id,)).fetchone()
            job = JobRequest.from_payload(json.loads(row[0]))
            snapshot = self._valid_snapshot(job)
            expected = DependencyDeclaration(job.output_key, job.request).to_metadata()[DEPENDENCY_METADATA_KEY]
            if (completion["input_snapshot"] != json.loads(job.input_snapshot_json)
                    or manifest.metadata.get(DEPENDENCY_METADATA_KEY) != expected):
                raise PublicationConflictError("Result metadata differs from its immutable enqueue snapshot.")
            self._insert(connection, manifest, json.dumps(manifest.to_payload(), sort_keys=True))
            head = connection.execute("SELECT generation_id FROM d040_heads WHERE output_key=?", (job.output_key,)).fetchone()
            revisions_match = self._matches(snapshot)
            inputs_match = self._artifact_inputs_match(connection, job.request)
            generation_match = head is not None and head[0] == snapshot.generation_id
            selected = revisions_match and inputs_match and generation_match
            reason = "selected" if selected else "obsolete_revisions" if not revisions_match else "obsolete_inputs" if not inputs_match else "superseded_generation"
            if selected:
                self._select(connection, job.output_key, manifest.artifact_id)
            connection.execute("INSERT INTO d040_results VALUES (?, ?, ?, ?, ?, ?)",
                               (attempt.id, job.id, job.output_key, manifest.artifact_id, int(selected), reason))
            self.jobs.complete_attached(connection, attempt.id, completion["claim_token"], manifest.artifact_id, now)

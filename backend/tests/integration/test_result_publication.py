"""D040 uses real project, artifact and queue SQLite/files; no model or network."""

from dataclasses import replace
from contextlib import closing
import asyncio
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from app.application.projects import ProjectSession
from app.application.result_publication import ResultPublicationService
from app.domain.dependencies import DependencyDeclaration, Provenance, RequestFingerprint, InputEdge
from app.domain.narrative_segment import SectionRevision
from app.domain.publication import PUBLICATION_KEY, PublicationConflictError
from app.domain.generation_job import AttemptStatus
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobConflictError, JobRepository
from app.runtime.supervisor import WorkerLaunch, WorkerSupervisor
from app.storage.dependency_index import ArtifactDependencyIndex
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository, UnsupportedSchemaError
from app.storage.result_publication import ResultArtifactIndex


def composition(session):
    jobs = JobRepository(session.repository)
    index = ResultArtifactIndex(session.repository, jobs)
    store = LocalArtifactStore(index.root, index=index)
    store.recovery_report = store.recover()
    return jobs, index, store, ResultPublicationService(index, store)


@pytest.fixture
def setup(tmp_path):
    with ProjectSession.create(tmp_path, name="Publication gate", repository_factory=ProjectRepository) as session:
        draft = session.active_script
        sections = tuple(SectionRevision.create(project_id=session.project.id, title=name, text=name + "1", role="body")
                         for name in "ABC")
        initial = replace(draft, id="script_ABC", parent_revision_id=draft.id, sections=sections)
        session.save_script(initial, expected_active_revision_id=draft.id)
        yield session, *composition(session)


def enqueue(setup, name="B", *, request=None, script=False):
    session, jobs, index, store, service = setup
    section = next(s for s in session.active_script.sections if s.title == name)
    return service.enqueue(name + ":raw", request or RequestFingerprint.create("fixture", "1"),
                           expected_sections={section.section_id: section.id},
                           expected_script_revision_id=session.active_script.id if script else None,
                           inputs={"fixture": section.text})


def claim(setup):
    return JobCoordinator(setup[1]).claim_next("fixture-worker")


def publish(setup, owned, data=b"result"):
    return setup[-1].publish(owned, "result.bin", io.BytesIO(data))


def manifest(store, result):
    return next(m for m in store.list_artifacts() if m.artifact_id == result.artifact_id)


def test_normal_completion_is_selected_and_atomic_queue_outcome_survives_reopen(setup, tmp_path):
    session, jobs, index, store, service = setup
    queued = enqueue(setup)
    owned = claim(setup)
    result = publish(setup, owned, b"B1 bytes")
    assert result.selected_at_publication and result.reason == "selected"
    assert index.selected() == {"B:raw": result.artifact_id}
    assert jobs.get_attempt(queued.id).status == AttemptStatus.COMPLETED
    assert jobs.get_attempt(queued.id).output_artifact_ids == (result.artifact_id,)
    snapshot = json.loads(jobs.get_job(owned.job_id).input_snapshot_json)
    artifact = manifest(store, result)
    assert artifact.metadata[PUBLICATION_KEY]["input_snapshot"] == snapshot
    records = ArtifactDependencyIndex(index).records()
    assert records[result.artifact_id].declaration.request == jobs.get_job(owned.job_id).request
    session.close()
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        recovered_jobs, recovered, restored, _ = composition(reopened)
        assert recovered.selected() == {"B:raw": result.artifact_id}
        assert recovered.history("B:raw") == (result,)
        assert recovered_jobs.get_attempt(owned.id).status == AttemptStatus.COMPLETED
        assert restored.read_artifact(artifact.storage_key) == b"B1 bytes"


def test_B1_finishes_after_B2_edit_is_historical_and_unrelated_artifacts_unchanged(setup, tmp_path):
    session, jobs, index, store, service = setup
    enqueue(setup, "A")
    a = publish(setup, claim(setup), b"A1 bytes")
    enqueue(setup, "C")
    c = publish(setup, claim(setup), b"C1 bytes")
    before = store.list_artifacts()
    enqueue(setup)
    owned = claim(setup)
    b1 = next(s for s in session.active_script.sections if s.title == "B")
    session.edit_section(b1.section_id, text="B2")
    result = publish(setup, owned, b"B1 historical")
    assert not result.selected_at_publication and result.reason == "obsolete_revisions"
    assert index.selected() == {"A:raw": a.artifact_id, "C:raw": c.artifact_id}
    assert all(m in store.list_artifacts() for m in before)
    assert session.repository.get_section(b1.id) == b1
    assert store.read_artifact(manifest(store, result).storage_key) == b"B1 historical"
    assert ArtifactDependencyIndex(index).records()[result.artifact_id].declaration.request == jobs.get_job(owned.job_id).request
    session.close()
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        _, recovered, restored, _ = composition(reopened)
        assert recovered.selected() == {"A:raw": a.artifact_id, "C:raw": c.artifact_id}
        assert recovered.history("B:raw") == (result,)
        assert manifest(restored, result).metadata[PUBLICATION_KEY]["input_snapshot"][PUBLICATION_KEY]["sections"][0]["text"] == "B1"


@pytest.mark.parametrize("finish_new_first", [False, True])
@pytest.mark.parametrize("edit", [False, True])
def test_competing_generations_have_same_final_selection_in_either_completion_order(setup, finish_new_first, edit):
    session, jobs, index, store, service = setup
    enqueue(setup)
    old = claim(setup)
    if edit:
        section = next(s for s in session.active_script.sections if s.title == "B")
        session.edit_section(section.section_id, text="B2")
    enqueue(setup, request=RequestFingerprint.create("fixture", "1", settings={"variant": 2}))
    new = claim(setup)
    order = [new, old] if finish_new_first else [old, new]
    results = {item.id: publish(setup, item, item.id.encode()) for item in order}
    assert index.selected() == {"B:raw": results[new.id].artifact_id}
    assert results[new.id].selected_at_publication and not results[old.id].selected_at_publication
    assert len(store.list_artifacts()) == 2


def test_duplicate_completion_returns_original_without_reading_replacement_or_reselecting(setup):
    enqueue(setup)
    old = claim(setup)
    original = publish(setup, old)
    enqueue(setup)
    newer = publish(setup, claim(setup), b"new")
    class NoRead:
        def read(self, size):
            pytest.fail("Duplicate completion must not import another artifact")
    assert setup[-1].publish(old, "ignored-replay.bin", NoRead()) == original
    assert setup[2].selected() == {"B:raw": newer.artifact_id}
    assert len(setup[3].list_artifacts()) == 2


@pytest.mark.parametrize("phase", ["transfer", "insert", "select", "queue"])
def test_failed_publication_rolls_back_index_selection_and_completion(setup, monkeypatch, phase):
    session, jobs, index, store, service = setup
    enqueue(setup)
    previous = publish(setup, claim(setup), b"previous")
    enqueue(setup)
    owned = claim(setup)
    original = store.list_artifacts()
    if phase == "transfer":
        class Broken:
            def read(self, size):
                raise OSError("injected transfer")
        with pytest.raises(OSError):
            service.publish(owned, "broken.bin", Broken())
    else:
        target, method = (jobs, "complete_attached") if phase == "queue" else (index, "_" + phase)
        saved = getattr(target, method)
        def fail(*args, **kwargs):
            saved(*args, **kwargs)
            raise RuntimeError("injected before commit")
        with monkeypatch.context() as patch:
            patch.setattr(target, method, fail)
            with pytest.raises(RuntimeError, match="before commit"):
                publish(setup, owned)
    assert index.selected() == {"B:raw": previous.artifact_id}
    assert store.list_artifacts() == original
    assert jobs.get_attempt(owned.id).status == AttemptStatus.RUNNING
    assert index.result(owned) is None
    assert store.read_artifact(manifest(store, previous).storage_key) == b"previous"


def test_edit_during_streaming_is_compared_at_registration_not_service_entry(setup):
    session = setup[0]
    enqueue(setup)
    owned = claim(setup)
    section = next(s for s in session.active_script.sections if s.title == "B")
    class EditingStream(io.BytesIO):
        def read(self, size):
            if not self.tell():
                session.edit_section(section.section_id, text="B2")
            return super().read(size)
    result = setup[-1].publish(owned, "result.bin", EditingStream(b"B1"))
    assert not result.selected_at_publication and setup[2].selected() == {}


def test_injected_clock_runs_before_the_final_revision_comparison(setup):
    enqueue(setup)
    owned = claim(setup)
    session, _, index, _, _ = setup
    b = next(s for s in session.active_script.sections if s.title == "B")
    clock = index.clock
    def editing_clock():
        session.edit_section(b.section_id, text="B2")
        return clock()
    index.clock = editing_clock
    result = publish(setup, owned)
    assert result.reason == "obsolete_revisions" and index.selected() == {}


def test_stale_expected_revision_rejects_enqueue_without_reserving_or_queuing(setup):
    session, jobs, index, store, service = setup
    previous = next(s for s in session.active_script.sections if s.title == "B")
    session.edit_section(previous.section_id, text="B2")
    with pytest.raises(PublicationConflictError):
        service.enqueue("B:raw", RequestFingerprint.create("fixture", "1"),
                        expected_sections={previous.section_id: previous.id})
    assert jobs.jobs() == () and index.selected() == {}


@pytest.mark.parametrize("whole_script", [False, True])
def test_only_consumed_revisions_control_publication(setup, whole_script):
    enqueue(setup, script=whole_script)
    owned = claim(setup)
    session = setup[0]
    other = next(s for s in session.active_script.sections if s.title == "A")
    session.edit_section(other.section_id, text="A2")
    result = publish(setup, owned)
    assert result.selected_at_publication is (not whole_script)


def test_canceled_and_foreign_claims_are_rejected_before_byte_transfer(setup):
    enqueue(setup)
    owned = claim(setup)
    with pytest.raises(PublicationConflictError):
        publish(setup, replace(owned, claim_token="wrong"))
    JobCoordinator(setup[1]).cancel(owned.id)
    with pytest.raises(PublicationConflictError):
        publish(setup, owned)
    assert setup[3].list_artifacts() == () and setup[2].selected() == {}


def test_failed_enqueue_does_not_supersede_running_generation(setup, monkeypatch):
    enqueue(setup)
    owned = claim(setup)
    jobs = setup[1]
    original = jobs.enqueue_attached
    def fail(*args):
        original(*args)
        raise OSError("queue commit failed")
    with monkeypatch.context() as patch:
        patch.setattr(jobs, "enqueue_attached", fail)
        with pytest.raises(OSError):
            enqueue(setup)
    assert len(jobs.jobs()) == 1
    assert publish(setup, owned).selected_at_publication


def test_consumed_artifact_change_retains_old_derivative_and_rejects_stale_enqueue(setup):
    session, jobs, index, store, service = setup
    enqueue(setup)
    raw = publish(setup, claim(setup), b"raw1")
    edge = InputEdge.artifact("raw", "B:raw", raw.artifact_id, manifest(store, raw).checksum)
    request = RequestFingerprint.create("fixture.derived", "1", inputs=[edge])
    b = next(s for s in session.active_script.sections if s.title == "B")
    def derived():
        return service.enqueue("B:derived", request, expected_sections={b.section_id: b.id})
    derived()
    owned = claim(setup)
    enqueue(setup)
    newer = publish(setup, claim(setup), b"raw2")
    result = publish(setup, owned, b"derived from raw1")
    assert not result.selected_at_publication and result.reason == "obsolete_inputs"
    assert index.selected() == {"B:raw": newer.artifact_id}
    assert len(ArtifactDependencyIndex(index).records()) == 3
    with pytest.raises(PublicationConflictError):
        derived()
    assert len(jobs.jobs()) == 3


@pytest.mark.parametrize("manual", [False, True])
def test_editorial_ancestry_is_compared_while_manual_bytes_stay_reusable(setup, manual):
    session, jobs, index, store, service = setup
    enqueue(setup)
    raw = publish(setup, claim(setup))
    artifact = manifest(store, raw)
    output_key = "B:raw"
    if manual:
        output_key = "manual:reference"
        declaration = DependencyDeclaration(output_key, jobs.jobs()[0].request, Provenance.MANUAL)
        artifact = store.import_stream("manual.bin", io.BytesIO(b"owned bytes"), declaration.to_metadata())
    edge = InputEdge.artifact("reference", output_key, artifact.artifact_id, artifact.checksum)
    request = RequestFingerprint.create("fixture.composite", "1", inputs=[edge])
    enqueue(setup, "C", request=request)
    owned = claim(setup)
    b = next(s for s in session.active_script.sections if s.title == "B")
    session.edit_section(b.section_id, text="B2")
    result = publish(setup, owned)
    assert result.selected_at_publication is manual
    if not manual:
        assert result.reason == "obsolete_inputs"
        with pytest.raises(PublicationConflictError):
            enqueue(setup, "C", request=request)
    else:
        enqueue(setup, "C", request=request)


def test_worker_references_cannot_bypass_gate_and_retry_keeps_original_snapshot(setup):
    enqueue(setup)
    owned = claim(setup)
    coordinator = JobCoordinator(setup[1])
    job = setup[1].get_job(owned.job_id)
    with pytest.raises(JobConflictError, match="publication gate"):
        coordinator.complete(owned, ["worker_claimed_active_artifact"])
    coordinator.fail(owned, "fixture failure")
    section = next(s for s in setup[0].active_script.sections if s.title == "B")
    setup[0].edit_section(section.section_id, text="B2")
    coordinator.retry(owned.job_id)
    retried = claim(setup)
    result = publish(setup, retried)
    assert not result.selected_at_publication
    assert setup[1].get_job(owned.job_id) == job
    with pytest.raises(PublicationConflictError):
        publish(setup, owned)


def test_foreign_project_snapshot_and_composition_are_rejected(setup, tmp_path):
    enqueue(setup)
    job = setup[1].jobs()[0]
    with ProjectSession.create(tmp_path / "foreign", name="Other", repository_factory=ProjectRepository) as other:
        other_jobs, index, _, _ = composition(other)
        with pytest.raises(PublicationConflictError):
            ResultArtifactIndex(other.repository, setup[1])
        with pytest.raises(PublicationConflictError):
            index.enqueue(job)
        assert other_jobs.jobs() == () and index.selected() == {}


@pytest.mark.parametrize("field", ["input_snapshot", "desktop_dependencies"])
def test_completion_cannot_substitute_enqueue_inputs(setup, monkeypatch, field):
    enqueue(setup)
    owned = claim(setup)
    index = setup[2]
    register = index.register
    def tamper(artifact):
        if field == "input_snapshot":
            artifact.metadata[PUBLICATION_KEY][field]["inputs"] = {"fixture": "substituted"}
        else:
            artifact.metadata[field]["request"]["settings"] = {"substituted": True}
        register(artifact)
    monkeypatch.setattr(index, "register", tamper)
    with pytest.raises(PublicationConflictError, match="immutable enqueue snapshot"):
        publish(setup, owned)
    assert setup[1].get_attempt(owned.id).status == AttemptStatus.RUNNING
    assert index.selected() == {} and setup[3].list_artifacts() == ()


def test_extension_keeps_base_formats_and_legacy_imports_and_refuses_unknown_version(setup):
    session, jobs, index, store, _ = setup
    legacy = store.save_artifact("legacy.bin", b"retained")
    enqueue(setup)
    result = publish(setup, claim(setup))
    base = LocalArtifactStore.for_project(session.repository)
    assert base.list_artifacts() == store.list_artifacts()
    assert base.read_artifact(legacy.storage_key) == b"retained"
    for path in (index.path, jobs.path):
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert session.repository._connection.execute("PRAGMA user_version").fetchone()[0] == 1
    with closing(sqlite3.connect(index.path)) as connection, connection:
        connection.execute("UPDATE d040_format SET version=99")
    before = index.path.read_bytes()
    with pytest.raises(UnsupportedSchemaError):
        ResultArtifactIndex(session.repository, jobs)
    assert index.path.read_bytes() == before
    assert base.read_artifact(manifest(base, result).storage_key) == b"result"


@pytest.mark.parametrize("phase", ["before-move", "after-move", "index", "selection", "queue", "after-commit"])
def test_process_crash_recovers_file_index_selection_and_queue_as_one_decision(setup, tmp_path, phase):
    session, jobs, index, store, service = setup
    enqueue(setup)
    previous = publish(setup, claim(setup), b"previous")
    queued = enqueue(setup)
    session.close()
    code = r'''
import io, os, sys
from app.application.projects import ProjectSession
from app.application.result_publication import ResultPublicationService
from app.jobs.repository import JobRepository
from app.jobs.coordinator import JobCoordinator
from app.storage.project_repository import ProjectRepository
from app.storage.result_publication import ResultArtifactIndex
from app.storage.local_store import LocalArtifactStore
from app.storage import local_store
session = ProjectSession.open(sys.argv[1], repository_factory=ProjectRepository)
jobs = JobRepository(session.repository)
index = ResultArtifactIndex(session.repository, jobs)
store = LocalArtifactStore(index.root, index=index)
service = ResultPublicationService(index, store)
claim = JobCoordinator(jobs).claim_next('crash-fixture')
phase = sys.argv[2]
move = local_store._publish_file
def moving(source, destination):
    if phase == 'before-move': os._exit(17)
    move(source, destination)
    if phase == 'after-move': os._exit(17)
local_store._publish_file = moving
def crash_after(target, method):
    original = getattr(target, method)
    def wrapped(*args):
        original(*args)
        os._exit(17)
    setattr(target, method, wrapped)
if phase == 'index': crash_after(index, '_insert')
if phase == 'selection': crash_after(index, '_select')
if phase == 'queue': crash_after(jobs, 'complete_attached')
if phase == 'after-commit':
    store._discard_stage = lambda stage: os._exit(17)
service.publish(claim, 'result.bin', io.BytesIO(b'new committed bytes'))
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    child = subprocess.run([sys.executable, "-c", code, str(tmp_path), phase], env=env,
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 17, child.stderr
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        recovered_jobs, recovered, restored, publication = composition(reopened)
        current = recovered_jobs.get_attempt(queued.id)
        committed = phase == "after-commit"
        assert current.status == (AttemptStatus.COMPLETED if committed else AttemptStatus.INTERRUPTED)
        expected = "committed" if committed else "discarded" if phase == "before-move" else "orphan"
        assert len(restored.recovery_report) == 1 and restored.recovery_report[0].state == expected
        assert restored.read_artifact(manifest(restored, previous).storage_key) == b"previous"
        if committed:
            result = recovered.result(current)
            assert recovered.selected() == {"B:raw": result.artifact_id}
            assert restored.read_artifact(manifest(restored, result).storage_key) == b"new committed bytes"
            assert publication.publish(current, "duplicate.bin", None) == result
            assert len(restored.list_artifacts()) == 2
        else:
            assert recovered.selected() == {"B:raw": previous.artifact_id}
            assert recovered.history("B:raw") == (previous,)
            assert len(restored.list_artifacts()) == 1
            assert current.output_artifact_ids == ()
            if expected == "orphan":
                with pytest.raises(FileNotFoundError):
                    restored.open_artifact(restored.recovery_report[0].storage_key)


@pytest.mark.parametrize("mode", ["success", "edit", "fail", "post-commit", "no-handler"])
def test_supervisor_publishes_only_through_coordinator_after_worker_exit(setup, tmp_path, monkeypatch, mode):
    session, jobs, index, store, service = setup
    enqueue(setup)
    worker_file = tmp_path / "fixture_worker.py"
    backend = Path(__file__).resolve().parents[2]
    worker_file.write_text('''import sys, os
sys.path.insert(0, sys.argv[1])
'''.replace("sys.argv[1]", repr(str(backend))) + '''
from app.runtime.protocol import read_message, encode_frame, message
hello = read_message(sys.stdin.buffer)
job, attempt = hello['job_id'], hello['attempt_id']
sys.stdout.buffer.write(encode_frame(message('ready', job, attempt, {'pid': os.getpid()})))
sys.stdout.buffer.flush()
run = read_message(sys.stdin.buffer)
assert run['payload']['job']['input_snapshot']['inputs']['fixture'] == 'B1'
sys.stdout.buffer.write(encode_frame(message('completed', job, attempt, {'artifact_ids': ['untrusted_reference']})))
sys.stdout.buffer.flush()
''', encoding="utf-8")
    def handler(owned, references):
        assert worker.process.returncode == 0 and worker._process_job is None
        assert jobs.get_attempt(owned.id).status == AttemptStatus.RUNNING
        assert references == ("untrusted_reference",)
        if mode == "edit":
            b = next(s for s in session.active_script.sections if s.title == "B")
            session.edit_section(b.section_id, text="B2")
        if mode == "fail":
            raise OSError("output validation failed")
        if mode == "post-commit":
            def fail_cleanup(stage):
                raise OSError("after commit cleanup")
            monkeypatch.setattr(store, "_discard_stage", fail_cleanup)
        # A trusted operation opens its own validated result, not a path or
        # selection supplied by the worker's terminal frame.
        service.publish(owned, "verified.bin", io.BytesIO(b"validated fixture bytes"))
    worker = WorkerSupervisor(JobCoordinator(jobs),
                              WorkerLaunch(Path(getattr(sys, "_base_executable", sys.executable)), worker_file),
                              completion_handler=None if mode == "no-handler" else handler)
    outcome = asyncio.run(worker.run_next())
    assert outcome.returncode == 0
    failed = mode in ("fail", "no-handler")
    assert outcome.attempt.status == (AttemptStatus.FAILED if failed else AttemptStatus.COMPLETED)
    assert "untrusted_reference" not in outcome.attempt.output_artifact_ids
    if failed:
        assert index.selected() == {} and store.list_artifacts() == ()
    else:
        assert bool(index.selected()) is (mode != "edit")
        assert len(store.list_artifacts()) == 1


def test_application_import_is_independent_of_database_ui_and_providers():
    code = '''
import sys
from app.application.result_publication import ResultPublicationService
for name in ('sqlite3', 'app.storage', 'app.jobs.repository', 'app.runtime', 'PySide6', 'fastapi', 'torch', 'onnxruntime'):
    assert name not in sys.modules, name
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    child = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr


def test_publication_refuses_wal_without_changing_existing_selections(setup):
    session, jobs, index, store, service = setup
    enqueue(setup)
    previous = publish(setup, claim(setup))
    with closing(sqlite3.connect(index.path)) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    with pytest.raises(UnsupportedSchemaError, match="DELETE"):
        ResultArtifactIndex(session.repository, jobs)
    with pytest.raises(UnsupportedSchemaError, match="DELETE"):
        enqueue(setup)
    assert index.selected() == {"B:raw": previous.artifact_id}
    assert len(jobs.jobs()) == 1 and len(store.list_artifacts()) == 1

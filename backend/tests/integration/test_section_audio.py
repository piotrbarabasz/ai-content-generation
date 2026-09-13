"""D010 offline integration across editorial revisions, WAV files, jobs and D040."""

from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import wave
from hashlib import sha256

import pytest

from app.application.projects import ProjectSession
from app.application.result_publication import ResultPublicationService
from app.application.section_audio import SectionAudioService
from app.domain.narrative_segment import SectionRevision
from app.domain.section_audio import SectionAudio
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.providers.mock_tts import MockTTSProvider
from app.providers.tts_result import TTSSynthesisResult
from app.runtime.section_synthesis import generate, validated_output, workspace
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.result_publication import ResultArtifactIndex


class Provider(MockTTSProvider):
    def __init__(self, *, fail=None, variant="one"):
        super().__init__()
        self.calls, self.fail, self.variant = [], fail, variant

    def effective_synthesis_identity(self, config=None):
        return {"provider": "mock", "variant": self.variant, "language_id": "pl"}

    def synthesize(self, text, voice_config=None):
        self.calls.append(text)
        if self.fail == text:
            raise RuntimeError("fixture interrupted")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x01\x00" * (800 + len(text)))
        # Deliberately false provider duration: the service must measure PCM.
        return TTSSynthesisResult(buffer.getvalue(), sample_rate=8000, duration_seconds=999,
                                  audio_format="wav", provider_name="mock")


class Voices:
    def prepare(self, selection, max_words):
        provider = Provider(variant=selection.get("variant", "one"))
        return {"selection": selection, "max_words": max_words, "voice_config": {},
                "effective_identity": {"synthesis": provider.effective_synthesis_identity(), "runtime": "fixture"}}


class Outputs:
    def __init__(self, root):
        self.root = root

    def validated(self, job):
        return validated_output(self.root, job)


@pytest.fixture
def setup(tmp_path):
    with ProjectSession.create(tmp_path / "project", name="Audio", language="pl", repository_factory=ProjectRepository) as session:
        draft = session.active_script
        sections = tuple(SectionRevision.create(project_id=session.project.id, title=name, text="One. Two. Three.", role="body") for name in "ABC")
        session.save_script(replace(draft, id="abc", parent_revision_id=draft.id, sections=sections), expected_active_revision_id=draft.id)
        jobs = JobRepository(session.repository)
        index = ResultArtifactIndex(session.repository, jobs)
        store = LocalArtifactStore(index.root, index=index)
        output = Outputs(tmp_path / "work")
        service = SectionAudioService(ResultPublicationService(index, store), jobs, Voices(), output)
        yield session, jobs, index, store, output, service


def enqueue(setup, *, section=None, variant="one"):
    section = section or setup[0].active_script.sections[1]
    queued = setup[-1].enqueue(section, {"variant": variant}, max_words=1)
    owned = JobCoordinator(setup[1]).claim_next("fixture")
    return setup[1].get_job(queued.job_id), owned


def test_one_section_measured_wav_and_history_survive_reopen(setup):
    session, jobs, index, store, output, service = setup
    section = session.active_script.sections[1]
    job, owned = enqueue(setup)
    provider = Provider()
    generate(job, provider, output.root)
    result = service.complete(owned, ["../../untrusted-worker-path"])
    artifact = store.list_artifacts()[0]
    audio = SectionAudio.from_manifest(artifact)
    assert audio.section_id == section.section_id and audio.revision_id == section.id
    assert audio.duration_seconds == (2400 + len("One.Two.Three.")) / 8000
    assert result.selected_at_publication
    assert len(index.selected()) == 1 and provider.calls == ["One.", "Two.", "Three."]
    previous_bytes = store.read_artifact(artifact.storage_key)
    session.close()
    with ProjectSession.open(session.repository.workspace, repository_factory=ProjectRepository) as reopened:
        restored = LocalArtifactStore.for_project(reopened.repository)
        assert restored.read_artifact(artifact.storage_key) == previous_bytes
        assert SectionAudio.from_manifest(restored.list_artifacts()[0]) == audio


def test_interrupted_generation_retries_only_missing_chunks_and_retains_old_audio(setup):
    session, jobs, index, store, output, service = setup
    job, owned = enqueue(setup)
    generate(job, Provider(), output.root)
    original = service.complete(owned)
    original_manifest = store.list_artifacts()[0]
    original_bytes = store.read_artifact(original_manifest.storage_key)
    b = session.active_script.sections[1]
    session.edit_section(b.section_id, text="One. Two. Four.")
    new_job, owned = enqueue(setup)
    with pytest.raises(ValueError, match="incomplete"):
        generate(new_job, Provider(fail="Two."), output.root)
    coordinator = JobCoordinator(jobs)
    coordinator.fail(owned, "interrupted synthesis")
    coordinator.retry(new_job.id)
    retry = coordinator.claim_next("retry")
    provider = Provider()
    generate(new_job, provider, output.root)
    result = service.complete(retry)
    assert provider.calls == ["Two."]
    assert result.artifact_id != original.artifact_id and result.selected_at_publication
    assert workspace(output.root, job) != workspace(output.root, new_job)
    assert store.read_artifact(original_manifest.storage_key) == original_bytes
    current = next(m for m in store.list_artifacts() if m.artifact_id == result.artifact_id)
    assert current.metadata["section_audio"]["reused_chunks"] == 2


def test_edit_while_generating_retains_stale_audio_without_selecting_it(setup):
    session, jobs, index, store, output, service = setup
    for section in (session.active_script.sections[0], session.active_script.sections[2]):
        job, owned = enqueue(setup, section=section)
        generate(job, Provider(), output.root)
        service.complete(owned)
    before, selected = store.list_artifacts(), index.selected()
    job, owned = enqueue(setup)
    old_b = session.active_script.sections[1]
    generate(job, Provider(), output.root)
    session.edit_section(old_b.section_id, text="New B.")
    result = service.complete(owned)
    assert not result.selected_at_publication and index.selected() == selected
    assert all(m in store.list_artifacts() for m in before)
    assert any(SectionAudio.from_manifest(m).revision_id == old_b.id for m in store.list_artifacts())


def test_corrupted_chunk_is_regenerated_and_identity_drift_is_rejected(setup):
    _, _, _, _, output, service = setup
    job, owned = enqueue(setup)
    generate(job, Provider(), output.root)
    path = next((workspace(output.root, job) / "chunks").glob("*.wav"))
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="identity changed"):
        generate(job, Provider(variant="changed"), output.root)
    provider = Provider()
    generate(job, provider, output.root)
    assert len(provider.calls) == 1
    service.complete(owned)


@pytest.mark.parametrize("corrupt", ["wav", "duration", "identity", "chunks", "request", "substituted", "counts"])
def test_bad_output_stays_unpublished(setup, corrupt):
    _, _, index, store, output, service = setup
    job, owned = enqueue(setup)
    generate(job, Provider(), output.root)
    root = workspace(output.root, job)
    if corrupt == "wav":
        (root / "voiceover.wav").write_bytes(b"not a wav")
    elif corrupt == "request":
        (root / "request.json").write_text("{}")
    else:
        path = root / "synthesis-manifest.json"
        data = json.loads(path.read_text())
        if corrupt == "duration": data["final_duration_seconds"] = 12345
        if corrupt == "identity": data["effective_synthesis_identity"] = {}
        if corrupt == "chunks": data["chunks"] = []
        if corrupt == "counts": data["reused_chunk_count"] = 99
        if corrupt == "substituted":
            wav = root / "voiceover.wav"
            payload = wav.read_bytes()[:-1] + b"\x7f"
            wav.write_bytes(payload)
            data["final_checksum"] = sha256(payload).hexdigest()
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        service.complete(owned)
    assert index.selected() == {} and store.list_artifacts() == ()


def test_cancel_between_chunks_preserves_valid_prefix_for_retry(setup):
    _, _, _, _, output, service = setup
    job, owned = enqueue(setup)
    stop = False
    def progress(*args):
        nonlocal stop
        stop = True
    with pytest.raises(ValueError, match="incomplete"):
        generate(job, Provider(), output.root, report=progress, canceled=lambda: stop)
    provider = Provider()
    generate(job, provider, output.root)
    assert provider.calls == ["Two.", "Three."]
    service.complete(owned)


def test_duplicate_completion_does_not_require_workspace_or_reselect(setup):
    _, _, _, store, output, service = setup
    job, owned = enqueue(setup)
    generate(job, Provider(), output.root)
    result = service.complete(owned)
    (workspace(output.root, job) / "voiceover.wav").unlink()
    assert service.complete(owned) == result and len(store.list_artifacts()) == 1


def test_stale_section_fails_enqueue_without_job(setup):
    session, jobs, _, _, _, service = setup
    b = session.active_script.sections[1]
    session.edit_section(b.section_id, text="Changed")
    with pytest.raises(ValueError):
        service.enqueue(b, {}, max_words=1)
    assert jobs.jobs() == ()


def test_publication_metadata_cannot_override_gate(setup):
    job, owned = enqueue(setup)
    with pytest.raises(ValueError, match="cannot replace"):
        setup[-1].publication.publish(owned, "bad.wav", io.BytesIO(b"bad"), metadata={"desktop_publication": {}})


def test_application_import_does_not_load_database_or_optional_runtime():
    code = "import sys; from app.application.section_audio import SectionAudioService; assert not any(x in sys.modules for x in ('sqlite3','torch','piper','onnxruntime','PySide6','app.runtime','app.providers'))"
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_process_death_after_persisted_chunk_resumes_without_regenerating_it(setup, tmp_path):
    _, jobs, _, _, output, service = setup
    job, owned = enqueue(setup)
    request = tmp_path / "request.json"
    request.write_text(json.dumps(job.to_payload()), encoding="utf-8")
    code = '''
import json, os, sys
from pathlib import Path
from app.domain.generation_job import JobRequest
from app.runtime.section_synthesis import generate
from tests.integration.test_section_audio import Provider
job = JobRequest.from_payload(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
generate(job, Provider(), Path(sys.argv[2]), report=lambda *args: os._exit(17))
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    child = subprocess.run([sys.executable, "-c", code, str(request), str(output.root)], env=env,
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 17, child.stderr
    coordinator = JobCoordinator(jobs)
    coordinator.fail(owned, "worker process terminated")
    coordinator.retry(job.id)
    retry = coordinator.claim_next("retry")
    provider = Provider()
    generate(job, provider, output.root)
    assert provider.calls == ["Two.", "Three."]
    assert service.complete(retry).selected_at_publication

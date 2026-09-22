"""Real processes/queues/publication, fake model; never claims hardware evidence."""

import asyncio
from dataclasses import replace
from hashlib import sha256
import io
from pathlib import Path
import sys
import threading
import wave
from types import SimpleNamespace

import pytest

from app.application.projects import ProjectSession
from app.desktop.audio_composition import compose_candidate_chatterbox_audio
from app.domain.narrative_segment import SectionRevision
from app.runtime.chatterbox_assets import ChatterboxAssets
from app.runtime import chatterbox_assets as assets_module
from app.runtime.chatterbox_audio import CandidateChatterboxAudio, probe_chatterbox
from app.runtime.chatterbox_profile import ChatterboxHealth, profile_fingerprint
from app.runtime.resources import APPLICATION_GPU_RESOURCES
from app.runtime.supervisor import WorkerLaunch
from app.storage.project_repository import ProjectRepository
from app.storage.local_store import LocalArtifactStore
from app.storage.reference_audio import ProjectReferenceAudio


PYTHON = Path(getattr(sys, "_base_executable", sys.executable))
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/chatterbox_worker.py"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    data = b"fake public model"
    monkeypatch.setattr(assets_module, "MODEL_FILES", (("ve.pt", len(data), sha256(data).hexdigest()),))
    root = tmp_path / "models"
    ChatterboxAssets(root).install(SimpleNamespace(open=lambda name: io.BytesIO(data)))
    health = ChatterboxHealth(profile_fingerprint(), "cuda:0", ("en", "pl"), "fixture", 1024)
    launch = WorkerLaunch(PYTHON, FIXTURE)
    managed = CandidateChatterboxAudio(launch, health, "a" * 64, root, tmp_path / "work")
    with ProjectSession.create(tmp_path / "project", name="Chatterbox", language="pl", repository_factory=ProjectRepository) as session:
        draft = session.active_script
        section = SectionRevision.create(project_id=session.project.id, title="B", text="One. Two. Three.", role="body")
        session.save_script(replace(draft, id="script-one", parent_revision_id=draft.id, sections=(section,)),
                            expected_active_revision_id=draft.id)
        services = compose_candidate_chatterbox_audio(session, managed=managed, preview_root=tmp_path / "previews")
        yield session, managed, services, section


def test_private_probe_uses_owned_process_and_reads_fixed_health(tmp_path):
    health = asyncio.run(probe_chatterbox(WorkerLaunch(PYTHON, FIXTURE), "cuda:0", tmp_path))
    assert health.device == "cuda:0" and health.profile == profile_fingerprint()
    assert list(tmp_path.iterdir()) == []


def test_missing_runtime_probe_fails_honestly(tmp_path):
    with pytest.raises(ValueError, match="health failed"):
        asyncio.run(probe_chatterbox(WorkerLaunch(tmp_path / "missing.exe"), "cuda:0", tmp_path / "probe"))
    assert APPLICATION_GPU_RESOURCES.availability().available


def test_health_probe_respects_gpu_ownership_without_claiming_positive_health(tmp_path):
    held = APPLICATION_GPU_RESOURCES.acquire("synthesizing", "cuda:0")
    try:
        with pytest.raises(ValueError, match="worker unavailable"):
            asyncio.run(probe_chatterbox(WorkerLaunch(PYTHON, FIXTURE), "cuda:0", tmp_path))
        assert APPLICATION_GPU_RESOURCES.availability().owner == "synthesizing"
    finally:
        APPLICATION_GPU_RESOURCES.release(held)


def test_section_resume_preview_publication_and_reopen(setup):
    session, managed, services, section = setup
    choice, = services.choices("pl")
    assert choice.provider == "chatterbox_v3" and services.choices("de") == ()
    queued = services.production.enqueue(section, services.selection(choice), max_words=1)
    services.supervisor.launch = replace(managed.worker_launch(), environment=managed.worker_launch().environment | {"CHATTERBOX_CASE": "partial"})
    first = asyncio.run(services.supervisor.run_next())
    assert first.attempt.status == "failed" and APPLICATION_GPU_RESOURCES.availability().available
    assert not services.index.selected()
    services.coordinator.retry(queued.job_id)
    services.supervisor.launch = managed.worker_launch()
    result = asyncio.run(services.supervisor.run_next())
    assert result.attempt.status == "completed", result
    assert "Model log" in result.stderr_tail
    assert "Native model log" in result.stderr_tail and "Native init log" in result.stderr_tail
    manifest, = services.store.list_artifacts()
    assert manifest.metadata["section_audio"]["reused_chunks"] == 2
    assert manifest.metadata["section_audio"]["generated_chunks"] == 1
    payload = services.playback(section, "original", choice).payload
    assert asyncio.run(services.preview(choice, "A preview.")).payload.startswith(b"RIFF")
    assert APPLICATION_GPU_RESOURCES.availability().available
    session.close()
    with ProjectSession.open(session.repository.workspace, repository_factory=ProjectRepository) as reopened:
        from app.storage.local_store import LocalArtifactStore
        store = LocalArtifactStore.for_project(reopened.repository)
        assert store.read_artifact(manifest.storage_key) == payload


def test_chatterbox_oom_code_survives_worker_protocol_and_bounded_retry(setup):
    _, managed, services, section = setup
    choice, = services.choices("en")
    services.production.enqueue(section, services.selection(choice))
    launch = managed.worker_launch()
    services.supervisor.launch = replace(launch, environment=launch.environment | {"CHATTERBOX_CASE": "oom"})
    failed = asyncio.run(services.supervisor.run_next())
    assert failed.attempt.error.startswith("gpu_oom:")
    services.supervisor.retry_oom(failed)
    services.supervisor.launch = launch
    assert asyncio.run(services.supervisor.run_next()).attempt.status == "completed"
    assert APPLICATION_GPU_RESOURCES.availability().available


def test_busy_gpu_prevents_preview_and_leaves_queue_unclaimed(setup):
    _, _, services, section = setup
    choice, = services.choices("pl")
    queued = services.production.enqueue(section, services.selection(choice))
    held = APPLICATION_GPU_RESOURCES.acquire("other-project", "cuda:0")
    try:
        assert asyncio.run(services.supervisor.run_next()) is None
        assert services.coordinator.repository.get_attempt(queued.id).status == "queued"
        from app.tts.preview import TTSPreviewError
        with pytest.raises(TTSPreviewError) as error:
            asyncio.run(services.preview(choice, "Busy preview"))
        assert "did not run" in str(error.value.__cause__)
    finally:
        APPLICATION_GPU_RESOURCES.release(held)


def test_device_change_cannot_publish_previous_output(setup):
    _, managed, services, section = setup
    for key in ("HF_HOME", "HF_HUB_CACHE", "TORCH_HOME", "PKUSEG_HOME", "TEMP", "TMP"):
        assert Path(managed.worker_launch().environment[key]).is_relative_to(managed.work_root)
    choice, = services.choices("pl")
    queued = services.production.enqueue(section, services.selection(choice))
    managed.health = replace(managed.health, device="cuda:1")
    with pytest.raises(ValueError, match="CUDA"):
        managed.validated(services.coordinator.repository.get_job(queued.job_id))


def test_same_approved_reference_id_drives_preview_and_production_without_path_leak(setup):
    session, managed, services, section = setup
    references = ProjectReferenceAudio(
        session.repository, LocalArtifactStore.for_project(session.repository),
        managed.work_root.parent / "reference-cache")
    source_path = managed.work_root.parent / "private-speaker.wav"
    with wave.open(str(source_path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x01\x00" * 16000)
    source = references.import_file(source_path)
    references.approve(source.artifact_id, "speaker-consent")
    managed.references, managed.reference_cache = references, references.runtime_root
    services = compose_candidate_chatterbox_audio(
        session,
        managed=managed,
        preview_root=managed.work_root.parent / "reference-previews",
        reference_audio=references,
    )
    choice = next(item for item in services.choices("pl") if item.voice == "reference")
    selection = services.selection(choice)
    assert selection["reference_audio_artifact_id"] == source.artifact_id
    assert str(source_path) not in str(selection)
    assert asyncio.run(services.preview(choice, "Próba głosu.")).payload.startswith(b"RIFF")
    queued = services.production.enqueue(section, selection)
    job = services.coordinator.repository.get_job(queued.job_id)
    assert str(source_path) not in job.input_snapshot_json
    result = asyncio.run(services.supervisor.run_next())
    assert result.attempt.status == "completed"
    manifest = next(item for item in services.store.list_artifacts()
                    if "section_audio" in item.metadata)
    assert manifest.metadata["section_audio"]["checksum"]
    prepared = managed.prepare(selection, 120)
    assert prepared["effective_identity"]["synthesis"]["voice"] == {
        "mode": "reference", "content_checksum": source.checksum}
    references.reject(source.artifact_id, "consent-withdrawn")
    with pytest.raises(ValueError, match="approval|checksum"):
        managed.prepare(selection, 120)


def test_real_handler_loads_only_local_v3_and_preserves_chunks_on_native_oom(setup, monkeypatch):
    from app.runtime import chatterbox_worker
    from app.runtime.worker import WorkerFailure
    session, managed, services, section = setup
    choice, = services.choices("pl")
    queued = services.production.enqueue(section, services.selection(choice), max_words=1)
    job = services.coordinator.repository.get_job(queued.job_id)
    monkeypatch.setattr(chatterbox_worker, "check_private_runtime", lambda device: managed.health)
    for key, value in managed.worker_launch().environment.items():
        monkeypatch.setenv(key, value)
    calls, loads = [], []
    fail = [True]

    class OutOfMemoryError(RuntimeError):
        pass

    class Model:
        @staticmethod
        def from_local(directory, *, device, t3_model):
            assert directory == ChatterboxAssets(managed.model_root).installed()
            assert device == "cuda:0" and t3_model == "v3"
            loads.append(device)
            return Model()
        def generate(self, text, **kwargs):
            calls.append(text)
            if text == "Two." and fail[0]:
                raise OutOfMemoryError("fake native OOM")
            return text

    def save(buffer, audio, sample_rate, **kwargs):
        with wave.open(buffer, "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x01\x00" * 2400)

    cuda = SimpleNamespace(OutOfMemoryError=OutOfMemoryError, reset_peak_memory_stats=lambda device: None,
                           max_memory_allocated=lambda device: 128, max_memory_reserved=lambda device: 256)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda))
    monkeypatch.setitem(sys.modules, "torchaudio", SimpleNamespace(save=save))
    monkeypatch.setitem(sys.modules, "chatterbox", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", SimpleNamespace(ChatterboxMultilingualTTS=Model))
    claim = services.coordinator.claim_next("native-fixture")
    with pytest.raises(WorkerFailure) as failure:
        chatterbox_worker.handle(job.to_payload(), lambda *args: None, threading.Event())
    assert failure.value.code == "gpu_oom"
    assert calls == ["One.", "Two."]  # No more model calls after OOM in this process.
    services.coordinator.fail(claim, "gpu_oom: fixture")
    services.coordinator.retry(job.id)
    claim = services.coordinator.claim_next("native-restart")
    fail[0] = False
    chatterbox_worker.handle(job.to_payload(), lambda *args: None, threading.Event())
    services.production.complete(claim)
    assert calls == ["One.", "Two.", "Two.", "Three."] and len(loads) == 2
    manifest, = services.store.list_artifacts()
    assert manifest.metadata["section_audio"]["reused_chunks"] == 1

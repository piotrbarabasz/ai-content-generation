"""D011 offline behavior through real projects, immutable WAVs and D040 publication."""

from dataclasses import replace
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import wave

import pytest

from app.application.projects import ProjectSession
from app.application.result_publication import ResultPublicationService
from app.application.section_audio import SectionAudioService
from app.application.section_tempo import SectionTempoService
from app.domain.dependencies import RequestFingerprint
from app.domain.generation_job import AttemptStatus
from app.domain.narrative_segment import SectionRevision
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.result_publication import ResultArtifactIndex
from app.storage.section_tempo import SectionTempoArtifacts
from app.tts.assembly import inspect_pcm_wav


def wav(frames=16000, rate=8000, channels=1, width=2):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x01" * frames * channels * width)
    return buffer.getvalue()


class FFmpeg:
    def __init__(self):
        self.calls = []
        self.before_output = lambda: None
        self.mode = "ok"

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        self.before_output()
        if self.mode == "timeout":
            raise subprocess.TimeoutExpired(command, 120)
        if self.mode == "fail":
            return SimpleNamespace(returncode=1, stderr=b"fixture encoder failure")
        source = Path(command[command.index("-i") + 1]).read_bytes()
        pcm, _ = inspect_pcm_wav(source)
        tempo = float(command[command.index("-filter:a") + 1].split("=")[1])
        output = Path(command[-1])
        if self.mode != "missing":
            payload = wav(round(pcm.frame_count / tempo), pcm.sample_rate)
            if self.mode == "corrupt": payload = b"not a WAV"
            if self.mode == "empty": payload = wav(0)
            if self.mode == "rate": payload = wav(16000, 44100)
            if self.mode == "channels": payload = wav(16000, channels=2)
            if self.mode == "width": payload = wav(16000, width=1)
            if self.mode == "duration": payload = wav(80000)
            output.write_bytes(payload)
        return SimpleNamespace(returncode=0, stderr=b"")


@pytest.fixture
def setup(tmp_path):
    with ProjectSession.create(tmp_path, name="Tempo", repository_factory=ProjectRepository) as session:
        draft = session.active_script
        sections = tuple(SectionRevision.create(project_id=session.project.id, title=s, text=s, role="body") for s in "ABC")
        session.save_script(replace(draft, id="abc", parent_revision_id=draft.id, sections=sections), expected_active_revision_id=draft.id)
        jobs = JobRepository(session.repository)
        coordinator = JobCoordinator(jobs)
        index = ResultArtifactIndex(session.repository, jobs)
        store = LocalArtifactStore(index.root, index=index)
        publication = ResultPublicationService(index, store)
        runner = FFmpeg()
        adapter = SectionTempoArtifacts(index, store, process_runner=runner, ffmpeg_locator=lambda _: "fixture-ffmpeg")
        service = SectionTempoService(publication, coordinator, adapter)
        yield session, coordinator, index, store, publication, runner, adapter, service


def raw(setup, section=None, data=None):
    session, coordinator, index, store, publication, *_ = setup
    section = section or session.active_script.sections[1]
    data = data if data is not None else wav()
    pcm, _ = inspect_pcm_wav(data)
    publication.enqueue("section:" + section.section_id + ":audio:raw", RequestFingerprint.create("fixture.raw", "1"),
                        expected_sections={section.section_id: section.id})
    claim = coordinator.claim_next("fixture")
    metadata = {"section_audio": {"version": 1, "section_id": section.section_id, "revision_id": section.id,
                                  "checksum": sha256(data).hexdigest(), "audio_parameters": pcm.to_payload(),
                                  "duration_seconds": pcm.duration_seconds}}
    result = publication.publish(claim, "raw.wav", io.BytesIO(data), metadata=metadata)
    return next(m for m in store.list_artifacts() if m.artifact_id == result.artifact_id)


def enqueue(setup, original, tempo=1.25, section=None):
    section = section or setup[0].active_script.sections[1]
    setup[-1].enqueue(section, original.artifact_id, tempo)
    return setup[1].claim_next("tempo")


def test_derivative_retains_original_other_sections_and_reopens_both_choices(setup, tmp_path):
    session, coordinator, index, store, publication, runner, adapter, service = setup
    raw(setup, session.active_script.sections[0])
    original = raw(setup)
    raw(setup, session.active_script.sections[2])
    before = store.list_artifacts()
    before_bytes = {m.storage_key: store.read_artifact(m.storage_key) for m in before}
    section = session.active_script.sections[1]
    assert service.selected(section, variant="processed") is None
    first = service.run(enqueue(setup, original, 1.25))
    second = service.run(enqueue(setup, original, 0.8))
    assert first.artifact_id != second.artifact_id and second.selected_at_publication
    assert service.selected(section).artifact_id == original.artifact_id
    processed = service.selected(section, variant="processed")
    assert processed.artifact_id == second.artifact_id and processed.duration_seconds == 2.5
    assert all(m in store.list_artifacts() for m in before)
    assert all(store.read_artifact(key) == value for key, value in before_bytes.items())
    manifests = [m for m in store.list_artifacts() if "audio_derivative" in m.metadata]
    assert len({m.metadata["audio_derivative"]["derivative_key"] for m in manifests}) == 2
    assert all(m.metadata["audio_derivative"]["raw_checksum"] == original.checksum for m in manifests)
    assert len(runner.calls) == 2 and not list(adapter.work_root.iterdir())
    session.close()
    with ProjectSession.open(tmp_path, repository_factory=ProjectRepository) as reopened:
        jobs = JobRepository(reopened.repository)
        restored_index = ResultArtifactIndex(reopened.repository, jobs)
        restored = LocalArtifactStore(restored_index.root, index=restored_index)
        choices = SectionTempoArtifacts(restored_index, restored)
        assert choices.selected(section, "original").artifact_id == original.artifact_id
        assert choices.selected(section, "processed") == processed
        assert len(restored.list_artifacts()) == 5


def test_real_D010_raw_can_change_tempo_without_any_further_provider_call(tmp_path):
    # Exercise the actual D010 service, not just compatible fixture metadata.
    from tests.integration.test_section_audio import Provider, Voices, Outputs
    from app.runtime.section_synthesis import generate
    with ProjectSession.create(tmp_path, name="No second synthesis", repository_factory=ProjectRepository) as session:
        draft = session.active_script
        section = SectionRevision.create(project_id=session.project.id, title="B", text="One. Two.", role="body")
        session.save_script(replace(draft, id="b", parent_revision_id=draft.id, sections=(section,)), expected_active_revision_id=draft.id)
        jobs = JobRepository(session.repository)
        coordinator = JobCoordinator(jobs)
        index = ResultArtifactIndex(session.repository, jobs)
        store = LocalArtifactStore(index.root, index=index)
        publication = ResultPublicationService(index, store)
        output = Outputs(tmp_path / "synthesis")
        synthesis = SectionAudioService(publication, jobs, Voices(), output)
        queued = synthesis.enqueue(section, {}, max_words=1)
        claim = coordinator.claim_next("synthesis")
        provider = Provider()
        generate(jobs.get_job(queued.job_id), provider, output.root)
        original = synthesis.complete(claim)
        count = len(provider.calls)
        runner = FFmpeg()
        adapter = SectionTempoArtifacts(index, store, process_runner=runner, ffmpeg_locator=lambda _: "fixture")
        tempo = SectionTempoService(publication, coordinator, adapter)
        for value in (0.8, 1.25, 1.0):
            tempo.enqueue(section, original.artifact_id, value)
            tempo.run(coordinator.claim_next("tempo"))
        assert len(provider.calls) == count == 2 and len(runner.calls) == 2


@pytest.mark.parametrize("failure", ["corrupt", "missing", "empty", "rate", "channels", "width", "duration", "fail", "timeout"])
def test_invalid_ffmpeg_keeps_prior_selection_and_records_failed_job(setup, failure):
    original = raw(setup)
    service, runner, coordinator, store = setup[-1], setup[5], setup[1], setup[3]
    first = service.run(enqueue(setup, original))
    before = store.list_artifacts()
    runner.mode = failure
    claim = enqueue(setup, original, 0.75)
    with pytest.raises((ValueError, subprocess.TimeoutExpired)):
        service.run(claim)
    assert coordinator.repository.get_attempt(claim.id).status == AttemptStatus.FAILED
    assert store.list_artifacts() == before
    section = setup[0].active_script.sections[1]
    assert service.selected(section, variant="processed").artifact_id == first.artifact_id
    assert service.selected(section).artifact_id == original.artifact_id
    assert not list(setup[6].work_root.iterdir())


def test_stale_edit_during_processing_is_retained_but_not_selected(setup):
    session, _, index, store, _, runner, _, service = setup
    original = raw(setup)
    section = session.active_script.sections[1]
    claim = enqueue(setup, original)
    runner.before_output = lambda: session.edit_section(section.section_id, text="B2")
    result = service.run(claim)
    assert not result.selected_at_publication
    assert len(store.list_artifacts()) == 2
    current = session.active_script.sections[1]
    assert service.selected(current) is None and service.selected(current, variant="processed") is None
    assert index.history("section:" + section.section_id + ":audio:processed") == (result,)


def test_replaced_raw_and_competing_tempos_cannot_select_old_derivative(setup):
    original = raw(setup)
    old = enqueue(setup, original, 0.8)
    new = enqueue(setup, original, 1.2)
    newer = setup[-1].run(new)
    assert not setup[-1].run(old).selected_at_publication
    section = setup[0].active_script.sections[1]
    assert setup[-1].selected(section, variant="processed").artifact_id == newer.artifact_id
    old_raw_claim = enqueue(setup, original, 1.5)
    replacement = raw(setup, data=wav(24000))
    assert not setup[-1].run(old_raw_claim).selected_at_publication
    assert setup[-1].selected(section, variant="processed") is None
    assert setup[-1].selected(section).artifact_id == replacement.artifact_id


def test_duplicate_completion_does_not_run_ffmpeg_again(setup):
    original = raw(setup)
    claim = enqueue(setup, original)
    result = setup[-1].run(claim)
    assert setup[-1].run(claim) == result and len(setup[5].calls) == 1
    assert len(setup[3].list_artifacts()) == 2


def test_original_choice_is_a_read_and_tempo_one_does_not_locate_ffmpeg(setup):
    original = raw(setup)
    setup[6].ffmpeg_locator = lambda _: pytest.fail("Tempo one must not locate FFmpeg")
    section = setup[0].active_script.sections[1]
    before = setup[1].repository.jobs()
    assert setup[-1].selected(section).artifact_id == original.artifact_id
    assert setup[1].repository.jobs() == before
    result = setup[-1].run(enqueue(setup, original, 1))
    output = next(m for m in setup[3].list_artifacts() if m.artifact_id == result.artifact_id)
    assert output.checksum == original.checksum and setup[5].calls == []
    assert output.metadata["audio_derivative"]["processor"] == "none"


@pytest.mark.parametrize("tempo", [True, float("nan"), float("inf"), 0.49, 2.01, "1.25"])
def test_invalid_tempo_never_enqueues_or_processes(setup, tempo):
    original = raw(setup)
    before = setup[1].repository.jobs()
    with pytest.raises(ValueError):
        enqueue(setup, original, tempo)
    assert setup[1].repository.jobs() == before and setup[5].calls == []


def test_derivative_key_binds_checksum_tempo_and_version(setup, monkeypatch):
    from app.storage import section_tempo
    first = raw(setup)
    audio = setup[6].raw(setup[0].active_script.sections[1], first.artifact_id)
    key = setup[6].settings(audio, 1)["derivative_key"]
    assert setup[6].settings(audio, 1.0)["derivative_key"] == key
    assert setup[6].settings(audio, 1.1)["derivative_key"] != key
    assert setup[6].settings(replace(audio, checksum="0" * 64), 1)["derivative_key"] != key
    claim = enqueue(setup, first)
    monkeypatch.setattr(section_tempo, "TEMPO_PROCESSOR_VERSION", "new-contract")
    assert setup[6].settings(audio, 1)["derivative_key"] != key
    with pytest.raises(ValueError, match="identity"):
        setup[-1].run(claim)
    assert setup[5].calls == []


def test_raw_corruption_or_wrong_section_rejects_before_enqueue(setup):
    original = raw(setup)
    with pytest.raises(ValueError):
        enqueue(setup, original, section=setup[0].active_script.sections[0])
    path = setup[3].root / original.storage_key
    path.write_bytes(wav(24000))
    with pytest.raises(ValueError, match="bytes differ"):
        enqueue(setup, original)
    assert len(setup[1].repository.jobs()) == 1 and setup[5].calls == []


def test_canceled_claim_is_acknowledged_without_processing(setup):
    original = raw(setup)
    claim = enqueue(setup, original)
    setup[1].cancel(claim.id)
    with pytest.raises(ValueError):
        setup[-1].run(claim)
    assert setup[1].repository.get_attempt(claim.id).status == AttemptStatus.CANCELED
    assert setup[5].calls == []


def test_layer_import_does_not_load_providers_or_database():
    code = "import sys; from app.application.section_tempo import SectionTempoService; assert not any(x in sys.modules for x in ('sqlite3','app.providers','app.tts','torch','piper','PySide6'))"
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    child = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr


def test_empty_output_for_very_short_input_is_rejected_even_with_duration_tolerance(setup):
    original = raw(setup, data=wav(10))
    setup[5].mode = "empty"
    with pytest.raises(ValueError, match="contain measured PCM frames"):
        setup[-1].run(enqueue(setup, original))
    assert len(setup[3].list_artifacts()) == 1


@pytest.mark.parametrize("phase", ["selection", "after-commit"])
def test_publication_failure_preserves_previous_or_committed_selection_truthfully(setup, monkeypatch, phase):
    original = raw(setup)
    first = setup[-1].run(enqueue(setup, original))
    claim = enqueue(setup, original, 0.8)
    if phase == "selection":
        original_select = setup[2]._select
        def fail(*args):
            original_select(*args)
            raise OSError("selection failed")
        monkeypatch.setattr(setup[2], "_select", fail)
        with pytest.raises(OSError):
            setup[-1].run(claim)
        assert setup[1].repository.get_attempt(claim.id).status == AttemptStatus.FAILED
        expected = first.artifact_id
    else:
        def fail(stage):
            raise OSError("post-commit cleanup failed")
        monkeypatch.setattr(setup[3], "_discard_stage", fail)
        result = setup[-1].run(claim)
        assert setup[1].repository.get_attempt(claim.id).status == AttemptStatus.COMPLETED
        expected = result.artifact_id
    section = setup[0].active_script.sections[1]
    assert setup[-1].selected(section, variant="processed").artifact_id == expected


def test_cancel_during_ffmpeg_does_not_publish_output(setup):
    original = raw(setup)
    claim = enqueue(setup, original)
    setup[5].before_output = lambda: setup[1].cancel(claim.id)
    with pytest.raises(ValueError):
        setup[-1].run(claim)
    assert setup[1].repository.get_attempt(claim.id).status == AttemptStatus.CANCELED
    assert len(setup[3].list_artifacts()) == 1


def test_default_process_runner_is_bounded_hidden_and_uses_configured_workspace(setup, monkeypatch):
    from app.storage import section_tempo
    original = raw(setup)
    runner = setup[5]
    setup[6].process_runner = section_tempo._run_ffmpeg
    monkeypatch.setattr(section_tempo.subprocess, "run", runner)
    setup[-1].run(enqueue(setup, original))
    command, kwargs = runner.calls[0]
    assert kwargs["timeout"] == 120
    assert kwargs["creationflags"] == (subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert "shell" not in kwargs
    assert Path(command[-1]).is_relative_to(setup[6].work_root)

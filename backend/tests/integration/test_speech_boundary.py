"""D012 maps actual chunk frames through publication, reopen and tempo."""

from dataclasses import replace
import io
import json
import subprocess
import sys
import wave

import pytest

from app.application.projects import ProjectSession
from app.application.section_tempo import SectionTempoService
from app.domain.section_audio import SectionAudio
from app.jobs.coordinator import JobCoordinator
from app.providers.tts_result import TTSSynthesisResult
from app.runtime.section_synthesis import generate, workspace
from app.runtime.worker_bundle import source_files
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.section_tempo import SectionTempoArtifacts
from app.tts.assembly import inspect_pcm_wav
from tests.integration.test_section_audio import Provider, setup  # noqa: F401 - shared real project fixture
from tests.integration.test_section_tempo import FFmpeg


class VariableProvider(Provider):
    def __init__(self, *, bad_rate=False):
        super().__init__()
        self.payloads = []
        self.bad_rate = bad_rate

    def synthesize(self, text, voice_config=None):
        self.calls.append(text)
        frames = 801 + 377 * len(self.calls)
        rate = 44100 if self.bad_rate and len(self.calls) == 2 else 8000
        out = io.BytesIO()
        with wave.open(out, "wb") as wav:
            wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            # Leading/trailing provider silence remains part of each measured block.
            wav.writeframes(b"\0\0" * 100 + b"\x01\0" * (frames - 300) + b"\0\0" * 200)
        self.payloads.append(out.getvalue())
        return TTSSynthesisResult(out.getvalue(), sample_rate=rate, audio_format="wav", duration_seconds=999,
                                  provider_name="mock")


def publish(setup, text="  First two three four five six.\n\nSecond sentence!  ", max_words=2, provider=None):
    session, jobs, index, store, output, service = setup
    section_id = session.active_script.sections[1].section_id
    session.edit_section(section_id, text=text)
    section = session.active_script.section(section_id)
    queued = service.enqueue(section, {}, max_words=max_words)
    claim = JobCoordinator(jobs).claim_next("fixture")
    job = jobs.get_job(queued.job_id)
    provider = provider or VariableProvider()
    generate(job, provider, output.root)
    result = service.complete(claim)
    manifest = next(m for m in store.list_artifacts() if m.artifact_id == result.artifact_id)
    return section, manifest, provider, job


def test_measured_silence_and_variable_subchunks_survive_publication_and_reopen(setup):
    section, manifest, provider, job = publish(setup)
    audio = SectionAudio.from_manifest(manifest)
    boundary = audio.speech_boundary_map
    counts = [inspect_pcm_wav(p)[0].frame_count for p in provider.payloads]
    assert [c.end_frame - c.start_frame for c in boundary.chunks] == counts
    assert audio.frame_count == sum(counts) and len(boundary.blocks) == 2
    assert len(boundary.blocks[0].chunk_ids) == 3
    _, actual_frames = inspect_pcm_wav(setup[3].read_artifact(manifest.storage_key))
    assert actual_frames == b"".join(inspect_pcm_wav(p)[1] for p in provider.payloads)
    boundary.validate_source(section.text, audio.checksum, audio.sample_rate, audio.frame_count)
    payload = json.loads((workspace(setup[4].root, job) / "synthesis-manifest.json").read_text())
    assert all(r["source_span"] is not None for r in payload["chunks"])
    session = setup[0]
    path = session.repository.workspace
    session.close()
    with ProjectSession.open(path, repository_factory=ProjectRepository) as reopened:
        restored = LocalArtifactStore.for_project(reopened.repository)
        assert SectionAudio.from_manifest(restored.list_artifacts()[0]) == audio


@pytest.mark.parametrize("tempo", [0.8, 1.0, 1.25])
def test_selected_tempo_maps_measured_output_without_tts_or_raw_changes(setup, tempo):
    section, manifest, provider, _ = publish(setup)
    _, jobs, index, store, _, synthesis = setup
    before = store.read_artifact(manifest.storage_key)
    original = SectionAudio.from_manifest(manifest)
    runner = FFmpeg()
    adapter = SectionTempoArtifacts(index, store, process_runner=runner, ffmpeg_locator=lambda _: "fixture")
    coordinator = JobCoordinator(jobs)
    service = SectionTempoService(synthesis.publication, coordinator, adapter)
    calls = list(provider.calls)
    service.enqueue(section, manifest.artifact_id, tempo)
    service.run(coordinator.claim_next("tempo"))
    selected = service.selected(section, variant="processed")
    boundary = selected.speech_boundary_map
    assert boundary.frame_count == selected.frame_count and boundary.chunks[-1].end_frame == selected.frame_count
    assert boundary == original.speech_boundary_map.retime(checksum=selected.checksum,
                                                         sample_rate=selected.sample_rate, frame_count=selected.frame_count)
    assert service.selected(section) == original and store.read_artifact(manifest.storage_key) == before
    assert provider.calls == calls and len(runner.calls) == (0 if tempo == 1 else 1)


def test_incompatible_chunk_parameters_never_publish_a_map(setup):
    with pytest.raises(ValueError, match="incomplete"):
        publish(setup, provider=VariableProvider(bad_rate=True))
    assert setup[3].list_artifacts() == ()


def test_manifest_source_tampering_is_rejected_before_publication(setup):
    _, jobs, _, store, output, service = setup
    section = setup[0].active_script.sections[1]
    queued = service.enqueue(section, {}, max_words=1)
    claim = JobCoordinator(jobs).claim_next("fixture")
    job = jobs.get_job(queued.job_id)
    generate(job, Provider(), output.root)
    path = workspace(output.root, job) / "synthesis-manifest.json"
    payload = json.loads(path.read_text())
    payload["chunks"][0]["source_span"]["sentence_id"] = "tampered"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Chunk evidence"):
        service.complete(claim)
    assert store.list_artifacts() == ()


def test_retry_retains_sentence_spans_and_reuses_valid_audio(setup):
    section, manifest, provider, job = publish(setup)
    resumed = VariableProvider()
    generate(job, resumed, setup[4].root)
    assert resumed.calls == []
    with setup[4].validated(job) as (_, metadata):
        assert metadata["speech_boundary_map"] == manifest.metadata["section_audio"]["speech_boundary_map"]


def test_version_one_jobs_keep_legacy_packing_and_no_invented_map(setup, monkeypatch):
    monkeypatch.setattr("app.application.section_audio.VERSION", "1")
    _, manifest, provider, job = publish(setup, "First sentence. Second sentence.", max_words=120)
    assert job.request.algorithm_version == "1" and len(provider.calls) == 1
    assert SectionAudio.from_manifest(manifest).speech_boundary_map is None
    assert "speech_boundary_map" not in manifest.metadata["section_audio"]


def test_audio_rejects_map_for_different_wav(setup):
    _, manifest, _, _ = publish(setup)
    metadata = json.loads(json.dumps(manifest.metadata))
    metadata["section_audio"]["speech_boundary_map"]["audio_checksum"] = "0" * 64
    with pytest.raises(ValueError, match="measurements"):
        SectionAudio.from_manifest(replace(manifest, metadata=metadata))


def test_worker_bundle_contains_boundary_domain_and_runs_in_isolation(tmp_path):
    bundle = source_files()
    assert "app/domain/speech_boundary.py" in bundle
    for name, data in bundle.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    script = "import sys; sys.path.insert(0, sys.argv[1]); from app.runtime.section_synthesis import generate; from app.tts.chunking import sentence_chunks; assert len(sentence_chunks('First. Second.')) == 2"
    result = subprocess.run([sys.executable, "-I", "-c", script, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

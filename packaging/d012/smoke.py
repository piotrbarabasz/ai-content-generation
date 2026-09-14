"""Explicit real Piper/FFmpeg boundary smoke and an A/B manual listening fixture."""

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.application.projects import ProjectSession
from app.application.result_publication import ResultPublicationService
from app.application.section_audio import SectionAudioService
from app.application.section_tempo import SectionTempoService
from app.domain.dependencies import RequestFingerprint
from app.domain.generation_job import AttemptStatus
from app.domain.narrative_segment import SectionRevision
from app.domain.section_audio import SectionAudio
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.runtime.profile_catalog import load_approved_profile
from app.runtime.profiles import HostCapabilities
from app.runtime.provisioning import DirectorySource, PiperProvisioner
from app.runtime.section_audio import ManagedSectionAudio
from app.runtime.supervisor import WorkerSupervisor
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.result_publication import ResultArtifactIndex
from app.storage.section_tempo import SectionTempoArtifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--runtime-cache", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    runtime = PiperProvisioner(args.runtime_root, HostCapabilities("windows", "x86_64", ("cpu",)))
    profile = load_approved_profile("piper-cpu-windows-x64", runtime.host)
    runtime.install(profile, DirectorySource(args.runtime_cache))
    text = ("To jest krótki wstęp. Czy wszystko słychać wyraźnie?\n\n"
            "Teraz sprawdzamy bardzo długie zdanie, które musi zostać podzielone na kilka technicznych fragmentów, "
            "ale nadal zachowuje wspólną tożsamość i pełną treść w mapie nagrania. "
            "Na końcu porównujemy tempo oraz przerwy między zdaniami.")
    choice = {"provider": "piper", "model": "pl_PL-gosia-medium", "voice": "gosia", "language": "pl"}
    report = {"text": text, "manual_prosody": "pending", "max_words": 12, "artifacts": {}}
    workspace = args.output / "project"
    with ProjectSession.create(workspace, name="D012 boundary smoke", language="pl", repository_factory=ProjectRepository) as session:
        draft = session.active_script
        section = SectionRevision.create(project_id=session.project.id, title="Speech", text=text, role="body")
        session.save_script(replace(draft, id="smoke-script", parent_revision_id=draft.id, sections=(section,)), expected_active_revision_id=draft.id)
        jobs = JobRepository(session.repository)
        coordinator = JobCoordinator(jobs)
        index = ResultArtifactIndex(session.repository, jobs)
        store = LocalArtifactStore(index.root, index=index)
        store.recovery_report = store.recover()
        publication = ResultPublicationService(index, store)
        managed = ManagedSectionAudio(runtime, args.model_root, workspace / "work/section-audio")
        synthesis = SectionAudioService(publication, jobs, managed, managed)
        worker = WorkerSupervisor(coordinator, managed.worker_launch(), completion_handler=synthesis.complete)

        def retain(name, artifact_id):
            manifest = next(m for m in store.list_artifacts() if m.artifact_id == artifact_id)
            audio = SectionAudio.from_manifest(manifest)
            (args.output / (name + ".wav")).write_bytes(store.read_artifact(manifest.storage_key))
            if audio.speech_boundary_map is not None:
                audio.speech_boundary_map.validate_source(text, audio.checksum, audio.sample_rate, audio.frame_count)
            report["artifacts"][name] = {"checksum": audio.checksum, "sample_rate": audio.sample_rate,
                                         "frame_count": audio.frame_count, "duration_seconds": audio.duration_seconds,
                                         "boundary_map": audio.speech_boundary_map.to_payload() if audio.speech_boundary_map else None}
            return audio

        prepared = managed.prepare(choice, 12)
        request = RequestFingerprint.create("section_audio.synthesize", "1", settings=prepared,
                                            effective_identity=prepared["effective_identity"])
        publication.enqueue("section:" + section.section_id + ":audio:raw", request,
                            expected_sections={section.section_id: section.id}, inputs={"section_audio": prepared})
        legacy = asyncio.run(worker.run_next())
        assert legacy.attempt.status == AttemptStatus.COMPLETED, legacy
        retain("legacy-packed", index.selected()["section:" + section.section_id + ":audio:raw"])
        synthesis.enqueue(section, choice, max_words=12)
        generated = asyncio.run(worker.run_next())
        assert generated.attempt.status == AttemptStatus.COMPLETED, generated
        original = retain("original", index.selected()["section:" + section.section_id + ":audio:raw"])
        boundary = original.speech_boundary_map
        assert boundary is not None and len(boundary.blocks) == 4
        assert any(len(block.chunk_ids) > 1 for block in boundary.blocks)
        before = {m.storage_key: store.read_artifact(m.storage_key) for m in store.list_artifacts()}
        adapter = SectionTempoArtifacts(index, store)
        tempo = SectionTempoService(publication, coordinator, adapter)
        for value in (0.8, 1.25):
            tempo.enqueue(section, original.artifact_id, value)
            result = tempo.run(coordinator.claim_next("tempo"))
            processed = retain("tempo-" + str(value), result.artifact_id)
            assert processed.speech_boundary_map == boundary.retime(
                checksum=processed.checksum, sample_rate=processed.sample_rate, frame_count=processed.frame_count)
        assert all(store.read_artifact(key) == data for key, data in before.items())
        report["raw_history_unchanged"] = True
        selected = tempo.selected(section, variant="processed")
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as session:
        jobs = JobRepository(session.repository)
        index = ResultArtifactIndex(session.repository, jobs)
        restored = SectionTempoArtifacts(index, LocalArtifactStore(index.root, index=index))
        assert restored.selected(section, "original") == original
        assert restored.selected(section, "processed") == selected
        report["reopen_pass"] = True
    report["automated_pass"] = True
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "LISTENING.md").write_text(
        "# D012 manual prosody comparison\n\n"
        "Compare legacy-packed.wav with original.wav, then tempo-0.8.wav and tempo-1.25.wav.\n"
        "Listen for missing/repeated words, abrupt joins inside the long sentence, excessive pauses, "
        "intonation changes and tempo artifacts. Source text and exact block ranges are in report.json.\n"
        "Record PASS/FAIL, reviewer and observations; automated_pass does not certify prosody.\n",
        encoding="utf-8")
    print(json.dumps({"automated_pass": True, "manual_prosody": "pending", "artifacts": {
        name: {k: v for k, v in data.items() if k != "boundary_map"} for name, data in report["artifacts"].items()
    }}, indent=2))


if __name__ == "__main__":
    main()

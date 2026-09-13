"""Explicit managed Piper section-audio smoke; no model calls in default CI."""

import argparse
import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.application.projects import ProjectSession
from app.application.result_publication import ResultPublicationService
from app.application.section_audio import SectionAudioService
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--runtime-cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    runtime = PiperProvisioner(args.runtime_root, HostCapabilities("windows", "x86_64", ("cpu",)))
    if args.runtime_cache:
        profile = load_approved_profile("piper-cpu-windows-x64", runtime.host)
        runtime.install(profile, DirectorySource(args.runtime_cache))
    workspace = args.output / "project"
    with ProjectSession.create(workspace, name="D010 managed smoke", language="pl", repository_factory=ProjectRepository) as session:
        draft = session.active_script
        text = ("To jest rzeczywisty test syntezy pojedynczej sekcji. "
                "Pierwsza proba zostanie przerwana po zapisaniu poprawnego fragmentu. "
                "Ponowienie wykorzysta gotowe fragmenty i dokonczy nagranie. "
                "Czas trwania wynika z liczby probek pliku audio.")
        section = SectionRevision.create(project_id=session.project.id, title="B", text=text, role="body")
        session.save_script(replace(draft, id="smoke-script", parent_revision_id=draft.id, sections=(section,)), expected_active_revision_id=draft.id)
        jobs = JobRepository(session.repository)
        coordinator = JobCoordinator(jobs)
        index = ResultArtifactIndex(session.repository, jobs)
        store = LocalArtifactStore(index.root, index=index)
        store.recovery_report = store.recover()
        managed = ManagedSectionAudio(runtime, args.model_root, workspace / "work/section-audio")
        service = SectionAudioService(ResultPublicationService(index, store), jobs, managed, managed)
        choice = {"provider": "piper", "model": "pl_PL-gosia-medium", "voice": "gosia", "language": "pl"}
        queued = service.enqueue(section, choice, max_words=5)
        worker = WorkerSupervisor(coordinator, managed.worker_launch(), completion_handler=service.complete)
        async def interrupt():
            task = asyncio.create_task(worker.run_next())
            while not task.done():
                attempt = jobs.get_attempt(queued.id)
                if attempt.progress and attempt.progress.completed >= 1:
                    worker.request_cancel()
                    break
                await asyncio.sleep(0.01)
            return await task
        interrupted = asyncio.run(interrupt())
        assert interrupted.attempt.status == AttemptStatus.CANCELED, interrupted
        assert store.list_artifacts() == () and index.selected() == {}
        coordinator.retry(queued.job_id)
        completed = asyncio.run(worker.run_next())
        assert completed.attempt.status == AttemptStatus.COMPLETED, completed
        artifact = store.list_artifacts()[0]
        audio = SectionAudio.from_manifest(artifact)
        assert audio.duration_seconds > 0 and artifact.metadata["section_audio"]["reused_chunks"] >= 1
        assert list(index.selected().values()) == [artifact.artifact_id]
        report = {"automated_pass": True, "canceled_attempt": interrupted.attempt.status.value,
                  "retry_status": completed.attempt.status.value, "section_audio": asdict(audio),
                  "duration_seconds": audio.duration_seconds, "measurements": artifact.metadata["section_audio"],
                  "identity": json.loads(jobs.get_job(queued.job_id).request.effective_identity_json),
                  "worker_exit": completed.returncode}
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as session:
        restored = LocalArtifactStore.for_project(session.repository)
        assert SectionAudio.from_manifest(restored.list_artifacts()[0]) == audio
        report["reopen_pass"] = True
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

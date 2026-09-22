"""Run the D029 hardware acceptance scenarios and retain evidence outside Git."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import time

from app.application.projects import ProjectSession
from app.desktop.audio_composition import compose_candidate_chatterbox_audio
from app.domain.narrative_segment import SectionRevision
from app.runtime.chatterbox_audio import CandidateChatterboxAudio
from app.runtime.chatterbox_distribution import load_approved_chatterbox_distribution
from app.runtime.chatterbox_profile import MODEL_REVISION, SOURCE_REVISION, profile_fingerprint
from app.runtime.chatterbox_provisioning import ChatterboxProvisioner
from app.runtime.resources import APPLICATION_GPU_RESOURCES
from app.runtime.section_synthesis import workspace
from app.storage.project_repository import ProjectRepository
from app.tts.assembly import inspect_pcm_wav


def tree_hash(root: Path) -> str:
    digest = sha256()
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        with path.open("rb") as stream:
            digest.update(sha256(stream.read()).digest())
    return digest.hexdigest()


async def run(args) -> dict:
    distribution = load_approved_chatterbox_distribution()
    installed = ChatterboxProvisioner(args.runtime).active(distribution)
    if installed is None:
        raise RuntimeError("Approved Chatterbox runtime is not active.")
    work = args.evidence / "work"
    models = args.models.resolve()
    managed = CandidateChatterboxAudio(
        installed.worker_launch(), installed.health, installed.distribution_fingerprint,
        models, work, runtime_cache=installed.cache_root,
    )
    project = args.evidence / "project"
    timings = {}
    records = {}
    contention = None
    interruption = None
    with ProjectSession.create(project, name="D029 hardware acceptance", language="pl",
                               repository_factory=ProjectRepository) as session:
        draft = session.active_script
        english = SectionRevision.create(project_id=session.project.id, title="English",
                                         text="This is a real offline English synthesis test.", role="body")
        polish = SectionRevision.create(
            project_id=session.project.id, title="Polski",
            text=("To jest pierwszy rzeczywisty test syntezy. "
                  "To jest drugi rzeczywisty test syntezy. "
                  "To jest trzeci rzeczywisty test syntezy."), role="body")
        session.save_script(replace(draft, id="d029-hardware-script", parent_revision_id=draft.id,
                                    sections=(english, polish)), expected_active_revision_id=draft.id)
        services = compose_candidate_chatterbox_audio(session, managed=managed,
                                                       preview_root=args.evidence / "previews")

        en_choice, = services.choices("en")
        en_attempt = services.production.enqueue(english, services.selection(en_choice), max_words=20)
        en_job = services.coordinator.repository.get_job(en_attempt.job_id)
        started = time.monotonic()
        en_result = await services.supervisor.run_next("d029-en")
        timings["english_wall_seconds"] = time.monotonic() - started
        if en_result is None or en_result.attempt.status.value != "completed":
            raise RuntimeError("English synthesis failed: " + repr(en_result))

        pl_choice, = services.choices("pl")
        pl_attempt = services.production.enqueue(polish, services.selection(pl_choice), max_words=20)
        pl_job = services.coordinator.repository.get_job(pl_attempt.job_id)
        started = time.monotonic()
        running = asyncio.create_task(services.supervisor.run_next("d029-pl-interrupted"))
        for _ in range(900):
            if not APPLICATION_GPU_RESOURCES.availability().available:
                break
            if running.done():
                break
            await asyncio.sleep(.1)
        try:
            await services.preview(pl_choice, "Próba blokady podglądu podczas produkcji.")
        except Exception as exc:
            contention = {"blocked": True, "error": f"{type(exc).__name__}: {exc}"}
        else:
            contention = {"blocked": False, "error": None}
        for _ in range(1800):
            current = services.coordinator.repository.get_attempt(pl_attempt.id)
            if current.progress is not None and (current.progress.completed or 0) >= 1:
                services.supervisor.request_cancel()
                break
            if running.done():
                break
            await asyncio.sleep(.25)
        first = await running
        timings["polish_interrupted_wall_seconds"] = time.monotonic() - started
        interruption = {"first_status": first.attempt.status.value,
                        "first_error": first.attempt.error}
        if first.attempt.status.value != "canceled":
            raise RuntimeError("Polish interruption was not acknowledged: " + repr(first))
        services.coordinator.retry(pl_job.id)
        started = time.monotonic()
        resumed = await services.supervisor.run_next("d029-pl-resumed")
        timings["polish_resume_wall_seconds"] = time.monotonic() - started
        if resumed is None or resumed.attempt.status.value != "completed":
            raise RuntimeError("Polish resume failed: " + repr(resumed))
        await services.supervisor.unload()

        manifests = services.store.list_artifacts()
        for language, section, job in (("en", english, en_job), ("pl", polish, pl_job)):
            manifest = next(item for item in manifests
                            if item.metadata["section_audio"]["section_id"] == section.section_id)
            payload = services.store.read_artifact(manifest.storage_key)
            measured, _ = inspect_pcm_wav(payload)
            gpu = json.loads((workspace(work, job) / "gpu-evidence.json").read_text(encoding="utf-8"))
            records[language] = {
                "audio_sha256": sha256(payload).hexdigest(),
                "duration_seconds": measured.duration_seconds,
                "sample_rate": measured.sample_rate,
                "generated_chunks": manifest.metadata["section_audio"]["generated_chunks"],
                "reused_chunks": manifest.metadata["section_audio"]["reused_chunks"],
                "gpu": gpu,
            }

    nvidia = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=20,
    )
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "distribution_fingerprint": distribution.fingerprint,
        "candidate_profile_fingerprint": profile_fingerprint(),
        "source_revision": SOURCE_REVISION,
        "model_revision": MODEL_REVISION,
        "packages": distribution.package_versions,
        "runtime_directory": str(installed.directory),
        "model_root": str(models),
        "project_tree_sha256": tree_hash(project),
        "timings": timings,
        "audio": records,
        "interruption": interruption,
        "preview_production_contention": contention,
        "post_exit": {
            "application_gpu_available": APPLICATION_GPU_RESOURCES.availability().available,
            "nvidia_compute_processes": nvidia.stdout.strip().splitlines() if nvidia.returncode == 0 else [],
            "nvidia_smi_error": nvidia.stderr.strip() if nvidia.returncode else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.evidence = args.evidence.resolve()
    args.evidence.mkdir(parents=True, exist_ok=False)
    result = asyncio.run(run(args))
    evidence = args.evidence / "evidence.json"
    evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

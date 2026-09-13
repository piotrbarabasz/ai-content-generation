"""Explicit real FFmpeg PCM-fixture smoke for D011; no TTS or model dependency."""

import argparse
from dataclasses import replace
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.application.projects import ProjectSession
from app.application.result_publication import ResultPublicationService
from app.application.section_tempo import SectionTempoService
from app.domain.dependencies import RequestFingerprint
from app.domain.narrative_segment import SectionRevision
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from app.storage.result_publication import ResultArtifactIndex
from app.storage.section_tempo import SectionTempoArtifacts
from app.tts.assembly import inspect_pcm_wav


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path)
    args = parser.parse_args()
    executable = str(args.ffmpeg.resolve()) if args.ffmpeg else shutil.which("ffmpeg")
    if not executable:
        parser.error("Configure an installed FFmpeg executable.")
    args.output.mkdir(parents=True, exist_ok=False)
    version = subprocess.run([executable, "-version"], capture_output=True, check=True, timeout=15,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0).stdout.decode().splitlines()[0]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
        stream.writeframes(b"".join(struct.pack("<h", round(8000 * math.sin(2 * math.pi * 440 * i / 22050))) for i in range(88200)))
    payload = buffer.getvalue()
    pcm, _ = inspect_pcm_wav(payload)
    workspace = args.output / "project"
    with ProjectSession.create(workspace, name="D011 PCM smoke", repository_factory=ProjectRepository) as session:
        draft = session.active_script
        sections = tuple(SectionRevision.create(project_id=session.project.id, title=s, text=s, role="body") for s in "ABC")
        session.save_script(replace(draft, id="abc", parent_revision_id=draft.id, sections=sections), expected_active_revision_id=draft.id)
        jobs = JobRepository(session.repository)
        coordinator = JobCoordinator(jobs)
        index = ResultArtifactIndex(session.repository, jobs)
        store = LocalArtifactStore(index.root, index=index)
        publication = ResultPublicationService(index, store)
        raw_ids = []
        for section in sections:
            publication.enqueue("section:" + section.section_id + ":audio:raw", RequestFingerprint.create("fixture.raw-pcm", "1"),
                                expected_sections={section.section_id: section.id})
            metadata = {"section_audio": {"version": 1, "section_id": section.section_id, "revision_id": section.id,
                                          "checksum": sha256(payload).hexdigest(), "audio_parameters": pcm.to_payload(),
                                          "duration_seconds": pcm.duration_seconds}}
            raw_ids.append(publication.publish(coordinator.claim_next("fixture"), "raw.wav", io.BytesIO(payload), metadata=metadata).artifact_id)
        raw_manifests = store.list_artifacts()
        adapter = SectionTempoArtifacts(index, store, ffmpeg_locator=lambda _: executable)
        service = SectionTempoService(publication, coordinator, adapter)
        evidence = []
        for tempo in (0.8, 1.25):
            service.enqueue(sections[1], raw_ids[1], tempo)
            result = service.run(coordinator.claim_next("tempo"))
            assert result.selected_at_publication
            manifest = next(m for m in store.list_artifacts() if m.artifact_id == result.artifact_id)
            evidence.append(manifest.metadata)
            assert service.selected(sections[1], variant="processed").artifact_id == result.artifact_id
        assert all(store.read_artifact(m.storage_key) == payload for m in raw_manifests)
        assert [service.selected(s).artifact_id for s in sections] == raw_ids
        assert "app.providers.tts_factory" not in sys.modules
        report = {"automated_pass": True, "ffmpeg": version, "raw_duration_seconds": 4.0,
                  "raw_checksum": sha256(payload).hexdigest(), "variants": evidence,
                  "raw_and_unrelated_unchanged": True, "tts_factory_loaded": False}
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as session:
        jobs = JobRepository(session.repository)
        index = ResultArtifactIndex(session.repository, jobs)
        store = LocalArtifactStore(index.root, index=index)
        adapter = SectionTempoArtifacts(index, store)
        assert adapter.selected(sections[1], "processed").checksum == evidence[-1]["section_audio"]["checksum"]
        assert adapter.selected(sections[1], "original").artifact_id == raw_ids[1]
        report["reopen_pass"] = True
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

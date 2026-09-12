"""Relocated D007 handshake/queue smoke plus repeat of D002's packaged child ping."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.application.projects import ProjectSession
from app.domain.dependencies import RequestFingerprint
from app.domain.generation_job import AttemptStatus
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.runtime.supervisor import WorkerLaunch, WorkerSupervisor
from app.storage.project_repository import ProjectRepository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--d002-bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    relocated = output / "Żółty worker test"
    shutil.copytree(args.bundle.resolve(), relocated)
    legacy = output / "D002 żółty test"
    shutil.copytree(args.d002_bundle.resolve(), legacy)
    executable = relocated / "d007-worker.exe"
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PYTHON", "QT", "QML")) and key.upper() != "VIRTUAL_ENV"}
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    with ProjectSession.create(output / "project", repository_factory=ProjectRepository, name="Packaged worker") as session:
        queue = JobRepository(session.repository)
        coordinator = JobCoordinator(queue)
        coordinator.enqueue("diagnostic", RequestFingerprint.create("diagnostic.echo", "1"), {"text": "B1"})
        worker = WorkerSupervisor(coordinator, WorkerLaunch(executable, environment=environment, cwd=output))
        result = asyncio.run(worker.run_next())
        assert result.attempt.status == AttemptStatus.COMPLETED, result
        assert result.returncode == 0
    process = subprocess.Popen([str(legacy / "spike.exe"), "--worker"], cwd=output, env=environment,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        stdout, stderr = process.communicate(b'{"op":"ping","value":"D007-repeat"}\n', timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    reply = json.loads(stdout)
    assert process.returncode == 0 and reply == {"ok": True, "echo": "D007-repeat", "pid": process.pid}, (reply, stderr)
    report = {"automated_pass": True, "d007_status": result.attempt.status.value,
              "d007_pid": result.pid, "d007_exit": result.returncode,
              "d007_exe_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
              "d002_reply": reply, "d002_exit": process.returncode,
              "unicode_spaces_relocation": True, "unrelated_cwd": True, "path_only_system32": True,
              "clean_windows_verified": False, "manual_media_observed": False}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

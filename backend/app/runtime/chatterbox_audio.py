"""Explicit candidate composition, never discovery/approval of arbitrary runtimes.

A future approved distribution installer supplies the verified WorkerLaunch and
its complete content fingerprint. Health alone is NOT installation approval.
No automatic installation or activation is exposed by this candidate module.
"""

from dataclasses import replace
from pathlib import Path
import json
import re
import tempfile

from app.domain.base import new_id, utc_now
from app.domain.dependencies import RequestFingerprint, canonical_json
from app.domain.generation_job import AttemptStatus, JobRequest
from app.storage.paths import contained_path
from .chatterbox_assets import ChatterboxAssets
from .chatterbox_profile import ChatterboxHealth, profile_fingerprint
from .chatterbox_voice import prepare_voice
from .section_preview import _OneJobCoordinator
from .section_synthesis import inputs, validated_output
from .supervisor import WorkerLimits, WorkerSupervisor


CHATTERBOX_LIMITS = WorkerLimits(handshake=90, execution=900, cancel_grace=2, exit_grace=10, reap=10)
OFFLINE_ENVIRONMENT = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}


def _private_cache_environment(root):
    """Never borrow a user's global model/auxiliary caches as readiness evidence."""
    environment = dict(OFFLINE_ENVIRONMENT)
    for key, name in (("HF_HOME", "hf"), ("HF_HUB_CACHE", "hf/hub"), ("TORCH_HOME", "torch"),
                      ("PKUSEG_HOME", "pkuseg"), ("TEMP", "temp"), ("TMP", "temp")):
        path = contained_path(root, "runtime-cache/" + name)
        path.mkdir(parents=True, exist_ok=True)
        environment[key] = str(path)
    return environment


async def probe_chatterbox(launch, device, work_root):
    """Probe an explicitly trusted private launch without loading model weights.

    CUDA initialization also reserves D028 ownership, without claiming a positive
    health result in advance. D007 owns/reaps the child on all terminal paths.
    """
    if not isinstance(device, str) or not re.fullmatch(r"cuda:(0|[1-9][0-9]*)", device):
        raise ValueError("Select an explicit CUDA index for the Chatterbox probe.")
    root = Path(work_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="chatterbox-health-", dir=root) as temporary:
        report = Path(temporary) / "health.json"
        launch = replace(launch, environment=launch.environment | _private_cache_environment(Path(temporary)) | {
            "AICS_CHATTERBOX_DEVICE": device, "AICS_CHATTERBOX_HEALTH": str(report)})
        request = RequestFingerprint.create("runtime.chatterbox.health", "1")
        job = JobRequest(new_id("health"), "runtime:health", request, "{}", utc_now())
        supervisor = WorkerSupervisor(_OneJobCoordinator(job), launch, limits=CHATTERBOX_LIMITS,
                                      reservation_device=device)
        try:
            result = await supervisor.run_next("chatterbox-health")
        except BaseException:
            # Keep the owner available for one cleanup recovery; unsuccessful
            # recovery must retain quarantine, not free an uncertain process.
            await supervisor.unload()
            raise
        if result is None or result.attempt.status != AttemptStatus.COMPLETED:
            raise ValueError("Private Chatterbox health failed: " + (result.attempt.error if result else "worker unavailable"))
        if not report.is_file() or report.stat().st_size > 8192:
            raise ValueError("Missing or oversized Chatterbox health report.")
        health = ChatterboxHealth.from_payload(json.loads(report.read_text(encoding="utf-8")))
        if health.device != device:
            raise ValueError("Health worker changed the requested device.")
        return health


class CandidateChatterboxAudio:
    """D010 ports for an explicitly supplied private runtime, pending distribution.

    This constructor is a trusted composition boundary, not an import/approval API.
    It must never be called with paths or health supplied by a project/job snapshot.
    """

    def __init__(self, launch, health, runtime_fingerprint, model_root, work_root):
        if not isinstance(health, ChatterboxHealth) or not re.fullmatch(r"[0-9a-f]{64}", runtime_fingerprint):
            raise ValueError("A verified runtime fingerprint and fixed health result are required.")
        self.launch, self.health = launch, health
        self.device = health.decision()
        self.model_root, self.work_root = Path(model_root).resolve(), Path(work_root).resolve()
        self.identity = {"profile": profile_fingerprint(), "distribution": runtime_fingerprint}

    def prepare(self, selection, max_words):
        if ChatterboxAssets(self.model_root).installed() is None:
            raise ValueError("Install verified Chatterbox model assets first.")
        prepared, _ = prepare_voice(selection, max_words, self.health, self.identity)
        return prepared

    def worker_launch(self):
        return replace(self.launch, environment=self.launch.environment | _private_cache_environment(self.work_root) | {
            "AICS_CHATTERBOX_DEVICE": self.health.device, "AICS_SECTION_MODELS": str(self.model_root),
            "AICS_SECTION_WORK": str(self.work_root), "AICS_SECTION_RUNTIME": canonical_json(self.identity)})

    def validated(self, job):
        _, expected = inputs(job)
        actual = self.prepare(expected["selection"], expected["max_words"])
        if canonical_json(actual) != canonical_json(expected):
            raise ValueError("Chatterbox runtime, assets or device changed since enqueue.")
        return validated_output(self.work_root, job)

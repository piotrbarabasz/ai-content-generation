"""Trusted coordinator composition for D010's managed worker and result workspace."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from app.domain.dependencies import canonical_json
from .profile_catalog import load_approved_profile
from .provisioning import _worker_files
from .section_synthesis import inputs, validated_output
from .section_voice import prepare_voice


class ManagedSectionAudio:
    def __init__(self, runtime, model_root, work_root):
        self.runtime = runtime
        self.model_root = Path(model_root).resolve()
        self.work_root = Path(work_root).resolve()

    def _runtime(self):
        profile = load_approved_profile("piper-cpu-windows-x64", self.runtime.host)
        installed = self.runtime.active(profile)
        if installed is None:
            raise ValueError("Install a healthy managed Piper runtime first.")
        sources = {name: sha256(data).hexdigest() for name, data in _worker_files().items()}
        identity = {"profile": profile.fingerprint, "worker": sha256(canonical_json(sources).encode()).hexdigest()}
        return installed, identity

    def prepare(self, selection, max_words):
        _, identity = self._runtime()
        prepared, _ = prepare_voice(selection, max_words, self.model_root, identity)
        return prepared

    def worker_launch(self):
        runtime, identity = self._runtime()
        launch = runtime.worker_launch()
        return replace(launch, environment=launch.environment | {
            "AICS_SECTION_MODELS": str(self.model_root), "AICS_SECTION_WORK": str(self.work_root),
            "AICS_SECTION_RUNTIME": canonical_json(identity)})

    def validated(self, job):
        _, expected = inputs(job)
        actual = self.prepare(expected["selection"], expected["max_words"])
        if canonical_json(expected) != canonical_json(actual):
            raise ValueError("Managed runtime or voice identity changed since enqueue.")
        return validated_output(self.work_root, job)

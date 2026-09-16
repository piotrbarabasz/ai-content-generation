"""Managed preview reuses the private D007/D010 execution boundary."""

from contextlib import contextmanager
import io
from types import SimpleNamespace

from app.domain.generation_job import AttemptStatus
from app.runtime.section_preview import ManagedSectionPreviewProvider
from tests.unit.test_t086 import _wav


def test_managed_preview_runs_frozen_section_audio_job_and_returns_validated_wav(monkeypatch, tmp_path):
    prepared = {
        "selection": {"provider": "piper", "model": "voice", "voice": "builtin",
                      "language": "en", "settings": {"device": "cpu"}},
        "max_words": 120,
        "voice_config": {"voice_id": "builtin", "voice_mode": "builtin", "language_id": "en"},
        "effective_identity": {"synthesis": {"provider": "piper", "voice": {"id": "voice"}},
                               "runtime": {"profile": "pinned"}, "voice_fingerprint": "abc"},
    }
    seen = {}

    class Managed:
        work_root = tmp_path

        @contextmanager
        def validated(self, job):
            seen["job"] = job
            yield io.BytesIO(_wav()), {}

    class Supervisor:
        def __init__(self, coordinator, launch):
            seen["coordinator"] = coordinator

        async def run_next(self, owner):
            coordinator = seen["coordinator"]
            claim = coordinator.claim_next(owner)
            return SimpleNamespace(attempt=coordinator.complete(claim))

    monkeypatch.setattr("app.runtime.section_preview.WorkerSupervisor", Supervisor)
    provider = ManagedSectionPreviewProvider(Managed(), object(), prepared)
    result = provider.synthesize("Private worker preview", prepared["voice_config"])

    assert result.audio_bytes == _wav()
    assert result.provider_name == "piper"
    assert seen["job"].request.operation == "section_audio.synthesize"
    assert seen["coordinator"].attempt.status == AttemptStatus.COMPLETED

"""No Piper imports/downloads: actual pipes and bounded lightweight child processes."""

import asyncio
import os
from pathlib import Path
import sys

import pytest

from app.runtime import piper_health
from app.runtime.profile_catalog import load_approved_profile
from app.runtime.profiles import HealthStatus, HostCapabilities


@pytest.mark.skipif(os.name != "nt", reason="Private profile targets Windows")
@pytest.mark.parametrize("case,expected", [
    ("success", HealthStatus.READY), ("stderr", HealthStatus.READY),
    ("wrong-pid", HealthStatus.FAILED), ("failed", HealthStatus.FAILED),
    ("hang", HealthStatus.FAILED), ("flood", HealthStatus.FAILED),
    ("extra", HealthStatus.FAILED), ("nonzero", HealthStatus.FAILED),
    ("terminal-hang", HealthStatus.FAILED),
])
def test_probe_truthful_outcomes_and_reaps_process(tmp_path, monkeypatch, case, expected):
    profile = load_approved_profile("piper-cpu-windows-x64", HostCapabilities("windows", "x86_64", ("cpu",)))
    create = asyncio.create_subprocess_exec
    children = []
    fixture = Path(__file__).resolve().parents[1] / "fixtures/piper_health_worker.py"

    async def start(*args, **kwargs):
        assert args[1:4] == ("-I", "-B", "-u")
        assert not any(k.upper() in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV") for k in kwargs["env"])
        assert kwargs["env"]["PATH"] == str(piper_health.system_directory())
        child = await create(str(Path(getattr(sys, "_base_executable", sys.executable))),
                             "-I", "-B", "-u", str(fixture), case, **kwargs)
        children.append(child)
        return child

    monkeypatch.setenv("PYTHONPATH", "invalid-developer-path")
    monkeypatch.setenv("PYTHONHOME", "invalid-developer-home")
    monkeypatch.setenv("VIRTUAL_ENV", "invalid-developer-venv")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    result = piper_health.probe_runtime(tmp_path, profile, timeout=2)
    assert result.status == expected, result
    assert result.profile_fingerprint == profile.fingerprint
    assert len(children) == 1 and children[0].returncode is not None

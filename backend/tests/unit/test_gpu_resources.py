"""D028 offline capability decisions and shared ownership across threads."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Barrier
from types import SimpleNamespace
import subprocess
import sys

import pytest

from app.domain.dependencies import RequestFingerprint
from app.runtime.resources import DeviceDecision, GPUResourceManager


def test_contention_across_preview_and_project_threads_has_one_winner():
    resources = GPUResourceManager()
    start, finish = Barrier(8), Barrier(8)

    def contender(number):
        start.wait(timeout=5)
        lease = resources.acquire(f"project-or-preview-{number}", f"cuda:{number % 2}")
        finish.wait(timeout=5)
        return lease

    with ThreadPoolExecutor(max_workers=8) as pool:
        leases = list(pool.map(contender, range(8)))
    winner, = [lease for lease in leases if lease is not None]
    assert not resources.availability().available
    assert resources.availability().owner == winner.owner
    resources.release(winner)
    assert resources.availability().available


def test_live_worker_and_stale_token_cannot_release_a_new_owner():
    resources = GPUResourceManager()
    lease = resources.acquire("A", "cuda:0")
    process = SimpleNamespace(pid=7, returncode=None)
    resources.attach(lease, process)
    assert resources.availability().worker_pid == 7
    with pytest.raises(ValueError, match="exit"):
        resources.release(lease)
    resources.quarantine(lease)
    assert resources.availability().quarantined
    assert resources.acquire("B", "cuda:0") is None
    process.returncode = 1
    resources.release(lease)
    second = resources.acquire("B", "cuda:0")
    with pytest.raises(ValueError, match="stale"):
        resources.release(lease)
    resources.release(second)


@pytest.mark.parametrize("changes", [
    {"effective": "cpu"}, {"tested_devices": ()}, {"effective": "cuda:1"},
    {"requested": "cuda"}, {"profile": ""}, {"fallback_approved": "yes"},
    {"tested_devices": ["cuda:0"]},
])
def test_unsupported_or_implicit_fallback_decision_is_rejected(changes):
    values = dict(profile="profile-hash", requested="cuda:0", effective="cuda:0",
                  tested_devices=("cuda:0", "cpu"))
    with pytest.raises(ValueError):
        DeviceDecision(**(values | changes))


def test_explicit_tested_fallback_changes_identity_without_rewriting_original_request():
    gpu = DeviceDecision("profile-hash", "cuda:0", "cuda:0", ("cuda:0", "cpu"))
    cpu = replace(gpu, effective="cpu", fallback_approved=True)
    source = RequestFingerprint.create("voice", "1", effective_identity={"provider": "fixture"})
    on_gpu, on_cpu = gpu.bind(source), cpu.bind(source)
    assert on_gpu.fingerprint != on_cpu.fingerprint
    assert json.loads(source.effective_identity_json) == {"provider": "fixture"}
    assert json.loads(on_cpu.effective_identity_json)["runtime_device"]["fallback_approved"]
    cpu.validate(on_cpu)
    with pytest.raises(ValueError):
        gpu.validate(on_cpu)
    restored = RequestFingerprint.from_payload(json.loads(json.dumps(on_cpu.to_payload())))
    cpu.validate(restored)


def test_availability_discovery_does_not_load_models_or_probe_hardware():
    code = "import sys; from app.runtime.resources import APPLICATION_GPU_RESOURCES; assert APPLICATION_GPU_RESOURCES.availability().available; assert not any(n.startswith(('torch', 'onnxruntime', 'PySide6', 'app.providers')) for n in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

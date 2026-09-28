"""D059 offline worker ownership and failure cleanup."""

import json
from pathlib import Path

import pytest

from app.providers.image_upscale import ImageUpscaleRequest
from app.providers.local_upscale import LocalUpscaleProvider
from app.runtime.resources import GPUResourceManager
from app.runtime.upscale_runtime import InstalledUpscaler, MODEL_SHA256


class Process:
    pid = 12345

    def __init__(self, *, error=b"gpu_oom: CUDA memory exhausted."):
        self.error = error
        self.returncode = None

    def poll(self):
        return self.returncode

    def communicate(self, data, timeout):
        request = json.loads(data)
        assert request["version"] == 2 and request["target_width"] == 16
        self.returncode = 1
        return b"", self.error

    def kill(self):
        self.returncode = 1

    def wait(self, timeout):
        return self.returncode


def provider(tmp_path, monkeypatch, resources):
    monkeypatch.setattr("app.runtime.windows_job.WindowsJob", lambda pid: type("Job", (), {"close": lambda self: None})())
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "python.exe").write_bytes(b"fixture")
    (runtime / "python311._pth").write_text("python311.zip\n.\npackages\n", encoding="ascii")
    model = tmp_path / "model.pth"
    model.write_bytes(b"fixture")
    installed = InstalledUpscaler(runtime, model, "a" * 64, tmp_path / "cache")
    value = LocalUpscaleProvider(tmp_path, resources=resources, process_factory=lambda *a, **k: Process())
    monkeypatch.setattr(value.installation, "active", lambda: installed)
    return value


def request():
    from io import BytesIO
    from PIL import Image
    stream = BytesIO()
    Image.new("RGB", (8, 8)).save(stream, format="PNG")
    return ImageUpscaleRequest(stream.getvalue(), "PNG", 8, 8, target_width=16, target_height=16)


def test_pinned_model_and_gpu_contention(tmp_path, monkeypatch):
    assert MODEL_SHA256 == "8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292"
    resources = GPUResourceManager()
    value = provider(tmp_path, monkeypatch, resources)
    lease = resources.acquire("generation", "cuda:0")
    with pytest.raises(RuntimeError, match="occupied"):
        value.upscale(request())
    resources.release(lease)


def test_oom_releases_gpu_ownership(tmp_path, monkeypatch):
    resources = GPUResourceManager()
    value = provider(tmp_path, monkeypatch, resources)
    with pytest.raises(RuntimeError, match="tile=128"):
        value.upscale(request())
    assert resources.availability().available


def test_cancel_releases_gpu_ownership(tmp_path, monkeypatch):
    resources = GPUResourceManager()
    value = provider(tmp_path, monkeypatch, resources)

    class CanceledProcess(Process):
        def communicate(self, data, timeout):
            value.cancel()
            self.returncode = 1
            return b"", b""

    value.process_factory = lambda *a, **k: CanceledProcess()
    with pytest.raises(RuntimeError, match="canceled"):
        value.upscale(request())
    assert resources.availability().available


def test_upscale_worker_accepts_exact_targets_without_resolution_labels():
    from app.runtime.upscale_worker import validate_request
    from app.runtime.upscale_runtime import PROFILE

    assert PROFILE.endswith("v2-exact-target")
    request = {"version": 2, "image": "c291cmNl", "target_width": 1920, "target_height": 1080}
    assert validate_request(request) == request
    assert not any("profile" in key for key in request)
    with pytest.raises(ValueError, match="Invalid upscale worker request"):
        validate_request(request | {"target_width": 0})
    with pytest.raises(ValueError, match="Invalid upscale worker request"):
        validate_request(request | {"version": 1, "factor": 2, "target_width": None, "target_height": None})

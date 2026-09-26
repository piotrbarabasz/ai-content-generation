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
        assert json.loads(data)["factor"] == 2
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
    return ImageUpscaleRequest(stream.getvalue(), "PNG", 8, 8, 2)


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

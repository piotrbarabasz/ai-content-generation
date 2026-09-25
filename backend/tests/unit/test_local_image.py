"""D057 offline runtime, provider and GPU ownership contract tests."""

from hashlib import sha256
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from app.desktop.image_composition import compose_installed_image
from app.providers.image_factory import build_image_provider
from app.providers.image_generation import ImageGenerationRequest
from app.providers.local_image import LocalImageProvider
from app.providers.mock_image import MockImageProvider
from app.runtime import local_image_runtime as managed
from app.runtime.resources import GPUResourceManager


def _installed(tmp_path):
    runtime = tmp_path / "runtime"
    model = tmp_path / "model"
    runtime.mkdir()
    model.mkdir()
    (runtime / "python.exe").write_bytes(b"private fixture")
    (runtime / "python311._pth").write_text("python311.zip\n.\npackages\n", encoding="ascii")
    (runtime / "local_image_worker.py").write_bytes(Path(managed.__file__).with_name("local_image_worker.py").read_bytes())
    return managed.InstalledLocalImage(runtime, model, "a" * 64, tmp_path / "cache")


class Process:
    def __init__(self, output, *, returncode=0, errors=b""):
        self.pid = 12345
        self.output, self.errors, self.terminal = output, errors, returncode
        self.returncode = None
        self.input = None

    def communicate(self, data, timeout):
        self.input = json.loads(data)
        self.returncode = self.terminal
        return self.output, self.errors

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9

    def wait(self, timeout):
        return self.returncode


def test_factory_and_composition_keep_openai_and_absent_local_independent(tmp_path):
    assert compose_installed_image(environment={}) is None
    assert compose_installed_image(environment={
        "AICS_IMAGE_PROVIDER": "openai", "AICS_OPENAI_IMAGE_MODEL": "gpt-image-1",
    }).capabilities().provider == "openai"
    provider = compose_installed_image(environment={
        "AICS_IMAGE_PROVIDER": "local", "AICS_LOCAL_IMAGE_ROOT": str(tmp_path),
    })
    assert isinstance(provider, LocalImageProvider)
    with pytest.raises(ValueError, match="Install"):
        provider.capabilities()
    assert build_image_provider("mock").capabilities().provider == "mock"


def test_profile_capabilities_identity_seed_negative_prompt_and_output(tmp_path, monkeypatch):
    installed = _installed(tmp_path)
    payload = MockImageProvider().generate(ImageGenerationRequest("fixture", 512, 512)).image_bytes
    process = Process(payload)
    resources = GPUResourceManager()
    provider = LocalImageProvider(tmp_path, resources=resources, process_factory=lambda *a, **k: process)
    monkeypatch.setattr(provider.installation, "active", lambda: installed)
    monkeypatch.setattr("app.runtime.windows_job.WindowsJob", lambda pid: SimpleNamespace(close=lambda: None))
    capabilities = provider.capabilities()
    assert capabilities.provider == "local" and capabilities.seeded and capabilities.negative_prompt
    assert capabilities.supported_sizes == ((512, 512),) and capabilities.formats == ("PNG",)
    assert capabilities.settings["installation"] == installed.fingerprint
    request = ImageGenerationRequest("a satellite", 512, 512, seed=43, negative_prompt="letters")
    result = provider.generate(request)
    assert result.image_bytes == payload and result.format == "PNG"
    assert process.input == request.to_payload() | {"version": 1}
    assert resources.availability().available
    assert (installed.cache / "hf").is_dir()
    with pytest.raises(ValueError, match="supported"):
        provider.generate(ImageGenerationRequest("too small", 256, 256))


def test_gpu_contention_oom_failure_and_invalid_output_release(tmp_path, monkeypatch):
    installed = _installed(tmp_path)
    resources = GPUResourceManager()
    monkeypatch.setattr("app.runtime.windows_job.WindowsJob", lambda pid: SimpleNamespace(close=lambda: None))
    holder = resources.acquire("chatterbox", "cuda:0")
    provider = LocalImageProvider(tmp_path, resources=resources,
                                  process_factory=lambda *a, **k: Process(b""))
    monkeypatch.setattr(provider.installation, "active", lambda: installed)
    request = ImageGenerationRequest("satellite", 512, 512)
    with pytest.raises(RuntimeError, match="occupied"):
        provider.generate(request)
    resources.release(holder)

    for process, expected in ((Process(b"", returncode=1, errors=b"gpu_oom: CUDA memory exhausted"), "memory exhausted"),
                              (Process(b"broken png"), "Invalid")):
        provider.process_factory = lambda *a, **k: process
        with pytest.raises((RuntimeError, ValueError), match=expected):
            provider.generate(request)
        assert resources.availability().available


def test_cancel_terminates_worker_and_releases_gpu(tmp_path, monkeypatch):
    installed = _installed(tmp_path)
    resources = GPUResourceManager()
    entered = threading.Event()
    stopped = threading.Event()

    class WaitingProcess(Process):
        def communicate(self, data, timeout):
            entered.set()
            assert stopped.wait(2)
            return b"", b""

        def kill(self):
            super().kill()
            stopped.set()

    process = WaitingProcess(b"")
    provider = LocalImageProvider(tmp_path, resources=resources, process_factory=lambda *a, **k: process)
    monkeypatch.setattr(provider.installation, "active", lambda: installed)
    monkeypatch.setattr("app.runtime.windows_job.WindowsJob", lambda pid: SimpleNamespace(close=lambda: None))
    errors = []
    thread = threading.Thread(target=lambda: _capture_error(
        errors, lambda: provider.generate(ImageGenerationRequest("satellite", 512, 512))))
    thread.start()
    assert entered.wait(2)
    provider.cancel()
    thread.join(3)
    assert not thread.is_alive()
    assert errors and "canceled" in str(errors[0]).lower()
    assert resources.availability().available


def _capture_error(errors, action):
    try:
        action()
    except Exception as exc:
        errors.append(exc)


def test_restart_verification_after_third_party_cache_writes(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "python.exe").write_bytes(b"isolated interpreter fixture")
    (source / "python311._pth").write_text("python311.zip\n.\npackages\n", encoding="ascii")
    model = tmp_path / "snapshot"
    model.mkdir()
    content = b'{"_class_name":"StableDiffusionPipeline"}'
    (model / "model_index.json").write_bytes(content)
    monkeypatch.setattr(managed, "MODEL_HASHES", {"model_index.json": sha256(content).hexdigest()})
    monkeypatch.setattr(managed, "_packages", lambda root: {"python": [3, 11, 9], "packages": managed.PACKAGE_PINS})
    root = tmp_path / "managed"
    installed = managed.LocalImageInstallation(root).install(source, model)
    image = MockImageProvider().generate(ImageGenerationRequest("fixture", 512, 512)).image_bytes
    monkeypatch.setattr("app.runtime.windows_job.WindowsJob", lambda pid: SimpleNamespace(close=lambda: None))

    def spawn(*args, **kwargs):
        environment = kwargs["env"]
        process = Process(image)
        original = process.communicate

        def infer(request, timeout):
            for name in ("HF_HOME", "TORCH_HOME", "TRITON_CACHE_DIR", "TEMP"):
                path = Path(environment[name]) / "third-party-cache.bin"
                path.write_bytes(b"mutable inference cache")
                assert not path.is_relative_to(installed.runtime)
            return original(request, timeout)

        process.communicate = infer
        return process

    provider = LocalImageProvider(root, resources=GPUResourceManager(), process_factory=spawn)
    provider.generate(ImageGenerationRequest("satellite", 512, 512))
    reopened = managed.LocalImageInstallation(root).active()
    assert reopened.fingerprint == installed.fingerprint
    (reopened.runtime / "python.exe").write_bytes(b"changed")
    with pytest.raises(ValueError, match="failed verification"):
        managed.LocalImageInstallation(root).active()


def test_incomplete_model_and_package_mismatch_never_activate(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "python.exe").write_bytes(b"fixture")
    (source / "python311._pth").write_text("python311.zip\n.\npackages\n", encoding="ascii")
    model = tmp_path / "snapshot"
    model.mkdir()
    monkeypatch.setattr(managed, "MODEL_HASHES", {"model_index.json": "0" * 64})
    manager = managed.LocalImageInstallation(tmp_path / "managed")
    monkeypatch.setattr(managed, "_packages", lambda root: {"python": [3, 11, 9], "packages": managed.PACKAGE_PINS})
    with pytest.raises(ValueError, match="missing"):
        manager.install(source, model)
    assert manager.active() is None

    (model / "model_index.json").write_bytes(b'{"_class_name":"StableDiffusionPipeline"}')
    with pytest.raises(ValueError, match="pinned revision"):
        manager.install(source, model)
    assert manager.active() is None

    monkeypatch.setattr(managed, "MODEL_HASHES", {
        "model_index.json": sha256((model / "model_index.json").read_bytes()).hexdigest()})
    monkeypatch.setattr(managed, "_packages", lambda root: (_ for _ in ()).throw(
        ValueError("Private image runtime package pins differ")))
    with pytest.raises(ValueError, match="package pins"):
        manager.install(source, model)
    assert manager.active() is None

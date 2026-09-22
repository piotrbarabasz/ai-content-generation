"""Fixed private-runtime probe with fake modules; no CUDA allocation or network."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.runtime import chatterbox_health as health_module
from app.runtime.chatterbox_profile import PACKAGES, SOURCE_REVISION


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "private"
    root.mkdir()
    monkeypatch.setattr(health_module, "sys", SimpleNamespace(
        executable=str(root / "python.exe"), platform="win32", version="3.11.9 test",
        flags=SimpleNamespace(isolated=True, no_site=True), prefix=str(root), path=[str(root)], modules=sys.modules))
    versions = dict(PACKAGES)
    source = {"url": "https://github.com/resemble-ai/chatterbox.git", "vcs_info": {"commit_id": SOURCE_REVISION}}

    class Distribution:
        def __init__(self, name):
            self.version = versions[name]
        def locate_file(self, name):
            return root
        def read_text(self, name):
            return json.dumps(source)

    monkeypatch.setattr(health_module.metadata, "distribution", Distribution)

    class Model:
        __module__ = "chatterbox.mtl_tts"
        @staticmethod
        def from_local(ckpt_dir, device, t3_model=None):
            pytest.fail("health must not load a model")
        @staticmethod
        def from_pretrained(device, t3_model=None):
            pytest.fail("health must not download weights")
        @staticmethod
        def get_supported_languages():
            return {"en": "English", "pl": "Polish"}

    cuda = SimpleNamespace(is_available=lambda: True, device_count=lambda: 1,
                           get_device_properties=lambda device: SimpleNamespace(name="fake", total_memory=1024))
    torch = SimpleNamespace(__file__=str(root / "torch.py"), version=SimpleNamespace(cuda="12.4"), cuda=cuda)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torchaudio", SimpleNamespace(__file__=str(root / "torchaudio.py")))
    monkeypatch.setitem(sys.modules, "chatterbox", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", SimpleNamespace(__file__=str(root / "chatterbox.py"), ChatterboxMultilingualTTS=Model))
    return versions, source, cuda, Model


def test_probe_confirms_language_and_device_without_model_load(runtime):
    health = health_module.check_private_runtime("cuda:0")
    assert health.device == "cuda:0" and health.languages == ("en", "pl")


@pytest.mark.parametrize("case", ["version", "source", "cuda", "index", "language", "isolation"])
def test_probe_rejects_wrong_runtime_source_device_and_language(runtime, case):
    versions, source, cuda, model = runtime
    if case == "version":
        versions["torch"] = "2.7.0"
    elif case == "source":
        source["vcs_info"]["commit_id"] = "0" * 40
    elif case == "cuda":
        cuda.is_available = lambda: False
    elif case == "language":
        model.get_supported_languages = staticmethod(lambda: {"en": "English"})
    elif case == "isolation":
        health_module.sys.flags.no_site = False
    with pytest.raises(ValueError):
        health_module.check_private_runtime("cuda:1" if case == "index" else "cuda:0")

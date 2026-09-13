import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.providers.piper_tts import PiperConfigurationError
from app.providers.tts_factory import TTSFactoryError, build_tts_provider
from app.domain.provider_config import ProviderConfig
from app.domain.enums import ProviderType
from app.runtime import section_voice
from app.runtime.worker_bundle import source_files


CHOICE = {"provider": "piper", "model": "pl_PL-gosia-medium", "voice": "gosia", "language": "pl"}


@pytest.fixture
def voice(tmp_path, monkeypatch):
    voice = SimpleNamespace(model_path=tmp_path / "voice.onnx", config_path=tmp_path / "voice.onnx.json", fingerprint="verified-fixture")
    monkeypatch.setattr(section_voice.ModelIndex, "installed", lambda self, entry: voice)
    return voice


def test_catalog_factory_identity_are_lazy_and_managed_loader_uses_verified_paths(voice, tmp_path, monkeypatch):
    calls = []
    class Native:
        @staticmethod
        def load(model, **kwargs):
            calls.append((model, kwargs))
            return Native()
        def synthesize_wav(self, text, output, *, syn_config):
            calls.append((text, syn_config))
            output.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            output.writeframes(b"\x01\x00" * 2205)
    monkeypatch.setitem(sys.modules, "piper.voice", SimpleNamespace(PiperVoice=Native))
    monkeypatch.setitem(sys.modules, "piper.config", SimpleNamespace(SynthesisConfig=lambda **kwargs: kwargs))
    prepared, provider = section_voice.prepare_voice(CHOICE | {"settings": {"length_scale": 1.2}}, 10, tmp_path, {"profile": "fixture"})
    assert calls == []
    assert prepared["effective_identity"]["voice_fingerprint"] == voice.fingerprint
    assert "post_processing" not in prepared["voice_config"]
    result = provider.synthesize("Test.", prepared["voice_config"])
    assert calls[0] == (str(voice.model_path), {"config_path": str(voice.config_path), "use_cuda": False})
    assert calls[1] == ("Test.", {"length_scale": 1.2})
    assert result.duration_seconds == 0.1
    assert str(tmp_path) not in json.dumps(prepared)


@pytest.mark.parametrize("change", [{"language": "en"}, {"provider": "xtts_v2_eval"}, {"tempo": 1.2}, {"settings": {"device": "cuda"}}, {"voice": "unknown"}])
def test_unsupported_selection_rejected(voice, tmp_path, change):
    with pytest.raises(ValueError):
        section_voice.prepare_voice(CHOICE | change, 10, tmp_path, {})


def test_missing_installed_voice_cannot_enqueue(tmp_path, monkeypatch):
    monkeypatch.setattr(section_voice.ModelIndex, "installed", lambda *args: None)
    with pytest.raises(ValueError, match="Download"):
        section_voice.prepare_voice(CHOICE, 10, tmp_path, {})


def test_factory_wraps_provider_configuration_failure():
    config = ProviderConfig.create(workflow_config_id="test", provider_type=ProviderType.TTS, provider_name="piper",
                                   settings={"model_key": "pl_PL-gosia-medium"})
    def fail(settings):
        raise PiperConfigurationError("invalid installed model")
    with pytest.raises(TTSFactoryError, match="invalid installed model"):
        build_tts_provider(config, provider_factories={"piper": fail})


def test_private_source_bundle_imports_without_application_checkout_or_optional_packages(tmp_path):
    for name, data in source_files().items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    code = "import sys; sys.path.insert(0, sys.argv[1]); import app.runtime.section_worker; assert not any(x in sys.modules for x in ('sqlite3','torch','piper','onnxruntime','PySide6'))"
    child = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr


def test_packaged_bundle_retains_exact_source_bytes(tmp_path):
    payload = source_files()
    for name, data in payload.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    packed = tmp_path / "app/runtime/worker_sources.json"
    packed.write_text(json.dumps({name: data.decode("utf-8") for name, data in payload.items()}), encoding="utf-8")
    code = "import sys,json; sys.path.insert(0, sys.argv[1]); from app.runtime.worker_bundle import source_files; print(json.dumps({k:v.decode('utf-8') for k,v in source_files().items()}))"
    child = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(tmp_path)], capture_output=True, encoding="utf-8", timeout=15)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == {name: data.decode("utf-8") for name, data in payload.items()}

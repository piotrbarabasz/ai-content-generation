"""D029 profile/assets/selection are deterministic and import no model libraries."""

from dataclasses import replace
from hashlib import sha256
import io
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.runtime import chatterbox_assets as assets_module
from app.runtime.chatterbox_assets import ChatterboxAssets
from app.runtime.chatterbox_profile import ChatterboxHealth, candidate_profile, profile_fingerprint
from app.runtime.chatterbox_voice import prepare_voice
from app.runtime.profile_catalog import approved_profile_ids


HEALTH = ChatterboxHealth(profile_fingerprint(), "cuda:0", ("en", "pl"), "fixture", 6 * 1024**3)
SELECTION = {"provider": "chatterbox_v3", "model": "v3", "voice": "builtin", "language": "pl",
             "settings": {"device": "cuda:0", "temperature": .8}}


@pytest.fixture
def small_models(monkeypatch):
    files = {"ve.pt": b"voice", "conds.pt": b"builtin"}
    monkeypatch.setattr(assets_module, "MODEL_FILES", tuple((name, len(data), sha256(data).hexdigest()) for name, data in files.items()))
    return files


def test_profile_is_explicit_candidate_not_approved_installation():
    profile = candidate_profile()
    assert profile["profile_id"] not in approved_profile_ids()
    assert profile["packages"]["torch"] == profile["packages"]["torchaudio"] == "2.6.0+cu124"
    assert profile["source_revision"] != profile["model_revision"]
    profile["packages"]["torch"] = "changed"
    assert candidate_profile()["packages"]["torch"] == "2.6.0+cu124"
    assert ChatterboxHealth.from_payload(HEALTH.to_payload()) == HEALTH


@pytest.mark.parametrize("changes", [{"profile": "bad"}, {"device": "cpu"}, {"device": "cuda"},
                                      {"languages": ("pl",)}, {"total_memory": 0}, {"total_memory": True}])
def test_incomplete_health_cannot_compose_gpu_worker(changes):
    with pytest.raises(ValueError):
        replace(HEALTH, **changes)


@pytest.mark.parametrize("changes", [{"voice": "reference"}, {"language": "de"}, {"model": "v2"},
                                      {"settings": {"device": "cpu"}}, {"settings": {}},
                                      {"settings": {"device": "cuda:0", "audio_prompt_path": "private.wav"}}])
def test_unsupported_selection_rejected_without_loading(changes):
    with pytest.raises(ValueError):
        prepare_voice(SELECTION | changes, 120, HEALTH, {"distribution": "fixture"})


def test_prepared_identity_includes_profile_device_language_and_settings():
    loaded = []
    prepared, provider = prepare_voice(SELECTION, 120, HEALTH, {"distribution": "fixture"},
                                       model_loader=lambda device: loaded.append(device))
    assert loaded == []
    assert prepared["effective_identity"]["runtime_device"] == HEALTH.decision().to_payload()
    assert prepared["effective_identity"]["synthesis"]["language_id"] == "pl"
    assert prepared["effective_identity"]["synthesis"]["generation_settings"]["temperature"] == .8
    assert provider.device == "cuda:0"


def test_asset_intake_checksum_reopen_and_tamper_detection(tmp_path, small_models):
    models = ChatterboxAssets(tmp_path / "models")
    source = SimpleNamespace(open=lambda name: io.BytesIO(small_models[name]))
    assert models.installed() is None
    installed = models.install(source)
    assert models.installed() == installed
    assert models.install(SimpleNamespace(open=lambda name: pytest.fail("already installed"))) == installed
    (installed / "ve.pt").write_bytes(b"other")
    with pytest.raises(ValueError, match="checksum"):
        models.installed()


@pytest.mark.parametrize("kind", ["corrupt", "oversize", "cancel", "disk"])
def test_failed_asset_intake_never_activates(tmp_path, small_models, monkeypatch, kind):
    models = ChatterboxAssets(tmp_path / "models")
    if kind == "disk":
        monkeypatch.setattr(assets_module.shutil, "disk_usage", lambda root: SimpleNamespace(free=0))
    source = SimpleNamespace(open=lambda name: io.BytesIO(
        b"x" * len(small_models[name]) if kind == "corrupt" else small_models[name] + (b"!" if kind == "oversize" else b"")))
    with pytest.raises((ValueError, InterruptedError)):
        models.install(source, canceled=lambda: kind == "cancel")
    assert models.installed() is None


def test_discovery_imports_no_optional_runtime():
    result = subprocess.run([sys.executable, "-c", "from app.runtime.chatterbox_audio import CandidateChatterboxAudio; import sys; assert not any(n.startswith(('torch', 'torchaudio', 'chatterbox.', 'onnxruntime')) for n in sys.modules)"],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_private_worker_denies_python_network_before_any_connection():
    code = """
from app.runtime.chatterbox_worker import enforce_offline
import socket
enforce_offline()
for operation in (lambda: socket.getaddrinfo('example.invalid', 443),
                  lambda: socket.socket().connect(('127.0.0.1', 9))):
    try:
        operation()
    except OSError as error:
        assert 'cannot access the network' in str(error)
    else:
        raise AssertionError('network operation was not blocked')
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr

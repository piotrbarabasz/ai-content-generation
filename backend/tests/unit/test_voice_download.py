"""Offline behavioral tests for resumable curated downloads and model activation."""

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.providers import piper_catalog
from app.runtime.model_index import voice_fingerprint
from app.runtime.profiles import HealthStatus, HostCapabilities
from app.runtime.voice_download import PiperVoiceDownloader
from app.runtime.voice_http import DownloadInterrupted, VoiceDownloadError


KEY = "pl_PL-gosia-medium"


class Response(io.BytesIO):
    def __init__(self, body, *, status, headers, disconnect=False):
        super().__init__(body)
        self.status, self.headers = status, headers
        self.disconnect = disconnect

    def read(self, size=-1):
        if self.disconnect and self.tell() >= 4096:
            raise OSError("Offline connection interruption")
        return super().read(min(size, 4096) if size >= 0 else 4096)


class FakeHTTP:
    def __init__(self, bodies):
        self.bodies = bodies
        self.calls = []
        self.responses = []
        self.disconnect = False
        self.ignore_range = False
        self.change = lambda response, name, probe: None
        self.missing = None

    @contextmanager
    def open(self, url, *, offset=0, etag=None, probe=False):
        name = url.rsplit("/", 1)[-1]
        self.calls.append((name, offset, probe, etag))
        if name == self.missing:
            raise VoiceDownloadError("HTTP 404 fixture")
        data = self.bodies[name]
        if not probe and self.ignore_range:
            response = Response(data, status=200, headers={"Content-Length": str(len(data))})
        else:
            end = 0 if probe else len(data) - 1
            body = data[offset:end + 1]
            response = Response(body, status=206,
                headers={"Content-Length": str(len(body)), "Content-Range": f"bytes {offset}-{end}/{len(data)}",
                         "ETag": '"fixture-version"'}, disconnect=not probe and self.disconnect)
        self.change(response, name, probe)
        self.responses.append(response)
        with response:
            yield response


@pytest.fixture
def setup(tmp_path, monkeypatch):
    entry = piper_catalog.get_piper_voice_catalog_entry(KEY)
    config = json.dumps({"language": {"code": "pl_PL"}, "audio": {"sample_rate": 22050}}).encode()
    bodies = {KEY + ".onnx": b"model-bytes" * 1500, KEY + ".onnx.json": config, "MODEL_CARD": b"Fixture provenance"}

    def catalog_for(values):
        return replace(entry, checksums=tuple((path, hashlib.md5(values[path.rsplit('/', 1)[-1]], usedforsecurity=False).hexdigest())
                                             for path in entry.required_files))

    fixture_entry = catalog_for(bodies)
    monkeypatch.setitem(piper_catalog._PIPER_VOICE_BY_KEY, KEY, fixture_entry)
    transport = FakeHTTP(bodies)
    runtime = SimpleNamespace(host=HostCapabilities("windows", "x86_64", ("cpu",)),
                              active=lambda profile: SimpleNamespace(health=SimpleNamespace(status=HealthStatus.READY)))
    service = PiperVoiceDownloader(tmp_path / "Modele \u017c\u00f3\u0142te", runtime, transport=transport,
                                   free_bytes=lambda _: 1024 * 1024 * 1024, reserve_bytes=1024)
    return service, fixture_entry, transport, catalog_for


def run(service, **kwargs):
    return service.download(KEY, language_id="pl_PL", **kwargs)


def partial(service, entry):
    return service.index.root / "downloads" / voice_fingerprint(entry) / (KEY + ".onnx.part")


def test_complete_voice_restart_and_idempotence_use_verified_version(setup):
    service, entry, http, _ = setup
    assert service.installed(KEY, language_id="pl_PL") is None
    voice = run(service)
    assert voice.language_id == "pl_PL" and voice.fingerprint == voice_fingerprint(entry)
    assert voice.model_path.read_bytes() == http.bodies[KEY + ".onnx"]
    assert voice.config_path.is_file()
    receipt = json.loads((voice.directory / "voice.json").read_text())
    assert receipt["catalog"] == entry.to_catalog_payload()
    assert all(len(identity["sha256"]) == 64 for identity in receipt["files"].values())
    calls = len(http.calls)
    restarted = PiperVoiceDownloader(service.index.root, service.runtime, transport=http)
    assert restarted.installed(KEY, language_id="pl_PL") == voice
    assert run(restarted) == voice and len(http.calls) == calls
    assert all(response.closed for response in http.responses)


def test_interrupted_connection_resumes_exact_prefix_in_new_service(setup):
    service, entry, http, _ = setup
    http.disconnect = True
    with pytest.raises(DownloadInterrupted):
        run(service)
    assert partial(service, entry).stat().st_size == 4096
    assert service.installed(KEY, language_id="pl_PL") is None
    http.disconnect = False
    restarted = PiperVoiceDownloader(service.index.root, service.runtime, transport=http)
    voice = run(restarted)
    assert (KEY + ".onnx", 4096, False, '"fixture-version"') in http.calls
    assert voice.model_path.read_bytes() == http.bodies[KEY + ".onnx"]
    assert all(response.closed for response in http.responses)


def test_cancel_preserves_prefix_and_ignored_range_restarts_without_append(setup):
    service, entry, http, _ = setup
    stop = False
    def progress(name, done, total):
        nonlocal stop
        stop = done >= 4096
    with pytest.raises(DownloadInterrupted):
        run(service, canceled=lambda: stop, progress=progress)
    assert partial(service, entry).stat().st_size == 4096
    http.ignore_range = True
    assert run(service).model_path.read_bytes() == http.bodies[KEY + ".onnx"]


@pytest.mark.parametrize("target", [KEY + ".onnx", KEY + ".onnx.json", "MODEL_CARD"])
def test_bad_hash_prevents_activation_and_retains_bad_bytes(setup, target):
    service, entry, http, _ = setup
    http.bodies[target] = b"wrong-bytes"
    with pytest.raises(VoiceDownloadError, match="hash mismatch"):
        run(service)
    assert service.installed(KEY, language_id="pl_PL") is None
    assert list(service.index.root.glob("downloads/*/*.bad-*"))


def test_missing_companion_prevents_activation(setup):
    service, _, http, _ = setup
    http.missing = KEY + ".onnx.json"
    with pytest.raises(VoiceDownloadError, match="404"):
        run(service)
    assert service.installed(KEY, language_id="pl_PL") is None
    assert not any(not probe for _, _, probe, _ in http.calls)


@pytest.mark.parametrize("language", ["en", "en_US", "", None])
def test_language_mismatch_rejected_before_network_and_storage(setup, language):
    service, _, http, _ = setup
    with pytest.raises(VoiceDownloadError, match="language"):
        service.download(KEY, language_id=language)
    assert http.calls == [] and not service.index.root.exists()


@pytest.mark.parametrize("language", ["pl", "pl_PL"])
def test_existing_provider_language_and_catalog_locale_resolve_same_voice(setup, language):
    service, _, _, _ = setup
    voice = service.download(KEY, language_id=language)
    assert voice.language_id == "pl_PL"
    assert service.installed(KEY, language_id=language) == voice


@pytest.mark.parametrize("config", [b"invalid json", b"{}",
    b'{"language":{"code":"en_US"},"audio":{"sample_rate":22050}}',
    b'{"language":{"code":"pl_PL"},"audio":{"sample_rate":16000}}'])
def test_hash_valid_but_incompatible_config_prevents_activation(setup, monkeypatch, config):
    service, _, http, catalog_for = setup
    http.bodies[KEY + ".onnx.json"] = config
    monkeypatch.setitem(piper_catalog._PIPER_VOICE_BY_KEY, KEY, catalog_for(http.bodies))
    with pytest.raises(VoiceDownloadError, match="config"):
        run(service)
    assert service.installed(KEY, language_id="pl_PL") is None


def test_insufficient_space_prevents_body_download(setup):
    service, _, http, _ = setup
    service.free_bytes = lambda _: 100
    with pytest.raises(VoiceDownloadError, match="free space"):
        run(service)
    assert not any(not probe for _, _, probe, _ in http.calls)
    assert service.installed(KEY, language_id="pl_PL") is None


def test_space_exhausted_midstream_retains_resumable_prefix(setup):
    service, entry, http, _ = setup
    def progress(name, done, total):
        if done >= 4096:
            service.free_bytes = lambda _: 0
    with pytest.raises(VoiceDownloadError, match="free space"):
        run(service, progress=progress)
    assert partial(service, entry).stat().st_size == 4096
    service.free_bytes = lambda _: 10**9
    assert run(service).model_path.is_file()


@pytest.mark.parametrize("case", ["range", "length", "encoding", "status", "changed-total", "truncated"])
def test_malformed_response_or_disconnect_never_activates(setup, case):
    service, _, http, _ = setup
    def change(response, name, probe):
        if probe:
            return
        if case == "range":
            response.headers["Content-Range"] = "invalid"
        elif case == "length":
            response.headers.pop("Content-Length")
        elif case == "encoding":
            response.headers["Content-Encoding"] = "gzip"
        elif case == "status":
            response.status = 416
        elif case == "changed-total":
            n = len(http.bodies[name])
            response.headers["Content-Range"] = f"bytes 0-{n - 1}/{n + 1}"
        elif case == "truncated":
            response.truncate(100)
    http.change = change
    with pytest.raises(VoiceDownloadError):
        run(service)
    assert service.installed(KEY, language_id="pl_PL") is None
    assert all(response.closed for response in http.responses)


def test_unhealthy_runtime_and_unknown_voice_do_not_download(setup):
    service, _, http, _ = setup
    with pytest.raises(ValueError, match="Unknown"):
        service.download("other-repo", language_id="pl_PL")
    service.runtime.active = lambda _: None
    with pytest.raises(VoiceDownloadError, match="healthy"):
        run(service)
    assert not service.index.root.exists() and not http.calls


@pytest.mark.parametrize("damage", ["model", "config", "card", "receipt", "extra", "pointer"])
def test_restart_rejects_corruption_instead_of_selecting_invalid_voice(setup, damage):
    service, _, _, _ = setup
    voice = run(service)
    targets = {"model": voice.model_path, "config": voice.config_path, "card": voice.directory / "MODEL_CARD",
               "receipt": voice.directory / "voice.json", "extra": voice.directory / "unexpected",
               "pointer": service.index.root / "active" / (KEY + ".json")}
    targets[damage].write_bytes(b"bad")
    with pytest.raises(VoiceDownloadError):
        service.installed(KEY, language_id="pl_PL")


def test_atomic_activation_interruption_recovers_complete_version_without_network(setup, monkeypatch):
    import app.runtime.model_index as index
    service, _, http, _ = setup
    original = index.os.replace
    def replace(source, destination):
        if Path(destination).parent.name == "active":
            raise OSError("Interrupted pointer publication")
        return original(source, destination)
    with monkeypatch.context() as patch:
        patch.setattr(index.os, "replace", replace)
        with pytest.raises(OSError, match="publication"):
            run(service)
    assert service.installed(KEY, language_id="pl_PL") is None
    calls = len(http.calls)
    assert run(service).model_path.is_file() and len(http.calls) == calls


def test_active_voice_survives_storage_relocation(setup, tmp_path):
    import shutil
    service, _, http, _ = setup
    voice = run(service)
    other = tmp_path / "relocated"
    shutil.copytree(service.index.root, other)
    recovered = PiperVoiceDownloader(other, service.runtime, transport=http).installed(KEY, language_id="pl_PL")
    assert recovered.fingerprint == voice.fingerprint and recovered.directory.is_relative_to(other)


def test_concurrent_install_is_rejected_without_network(setup):
    service, _, http, _ = setup
    with service.index.lock():
        with pytest.raises(ValueError, match="lock"):
            run(service)
    assert not http.calls
    assert run(service).model_path.is_file()


def test_failed_second_voice_preserves_existing_voice(setup):
    service, _, http, _ = setup
    previous = run(service)
    http.missing = "pl_PL-darkman-medium.onnx"
    with pytest.raises(VoiceDownloadError, match="404"):
        service.download("pl_PL-darkman-medium", language_id="pl_PL")
    assert service.installed(KEY, language_id="pl_PL") == previous


def test_canceled_activation_is_not_published_by_canceled_retry(setup):
    service, _, http, _ = setup
    stop = False
    def progress(name, done, total):
        nonlocal stop
        if name == "MODEL_CARD" and done == total:
            stop = True
    with pytest.raises(DownloadInterrupted):
        run(service, canceled=lambda: stop, progress=progress)
    assert service.installed(KEY, language_id="pl_PL") is None
    calls = len(http.calls)
    with pytest.raises(DownloadInterrupted):
        run(service, canceled=lambda: True)
    assert service.installed(KEY, language_id="pl_PL") is None and len(http.calls) == calls
    assert run(service).model_path.is_file() and len(http.calls) == calls


def test_partial_hash_corruption_never_becomes_active(setup):
    service, entry, http, _ = setup
    http.disconnect = True
    with pytest.raises(DownloadInterrupted):
        run(service)
    path = partial(service, entry)
    path.write_bytes(b"x" * path.stat().st_size)
    http.disconnect = False
    with pytest.raises(VoiceDownloadError, match="hash mismatch"):
        run(service)
    assert service.installed(KEY, language_id="pl_PL") is None
    assert run(service).model_path.read_bytes() == http.bodies[KEY + ".onnx"]


def test_probe_size_limit_rejects_unbounded_download(setup):
    service, _, http, _ = setup
    def change(response, name, probe):
        if probe:
            response.headers["Content-Range"] = "bytes 0-0/9999999999"
    http.change = change
    with pytest.raises(VoiceDownloadError, match="size"):
        run(service)
    assert service.installed(KEY, language_id="pl_PL") is None


def test_downloader_discovery_never_imports_optional_runtime_or_connects():
    import subprocess
    import sys
    code = """
import builtins, sys
sys.path.insert(0, sys.argv[1])
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'numpy', 'piper', 'onnxruntime', 'torch', 'PySide6'}:
        raise AssertionError('Optional runtime imported')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
def audit(event, args):
    if event in {'socket.connect', 'subprocess.Popen'}:
        raise AssertionError('External operation during discovery')
sys.addaudithook(audit)
from app.runtime.voice_download import PiperVoiceDownloader
from app.providers.piper_catalog import list_piper_voice_catalog
assert len(list_piper_voice_catalog()) == 5
"""
    result = subprocess.run([getattr(sys, "_base_executable", sys.executable), "-I", "-c", code,
                             str(Path(__file__).resolve().parents[2])], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr

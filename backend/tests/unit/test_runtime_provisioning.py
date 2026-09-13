"""Offline synthetic archives exercise the real install/publication path."""

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile

import pytest

from app.runtime import profile_catalog, provisioning as install
from app.runtime.profiles import (
    HealthCheck, HealthObservation, HealthStatus, HostCapabilities, ProfileHealth,
)


HOST = HostCapabilities("windows", "x86_64", ("cpu",))


def archive(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            info.filename = name  # Preserve hostile backslashes on Windows too.
            bundle.writestr(info, data)
    return out.getvalue()


def ready(root, profile):
    assert (root / "python.exe").is_file()
    return ProfileHealth(profile.profile_id, profile.fingerprint, HealthStatus.READY,
                         datetime(2026, 9, 13, tzinfo=UTC),
                         tuple(HealthObservation(check, True, "Offline fixture probe.") for check in HealthCheck))


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    profile = profile_catalog.load_approved_profile("piper-cpu-windows-x64", HOST)
    cache = tmp_path / "cache"
    cache.mkdir()

    def pin(original, data):
        (cache / original.filename).write_bytes(data)
        return replace(original, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))

    profile = replace(profile,
        interpreter=pin(profile.interpreter, archive({"python.exe": b"fixture", "python311.zip": b"stdlib",
                                                     "python311._pth": b"old"})),
        packages=tuple(replace(p, artifact=pin(p.artifact, archive({p.name + "/fixture.txt": p.version})))
                       for p in profile.packages),
        native_runtime=replace(profile.native_runtime, artifact=pin(profile.native_runtime.artifact, b"native")))
    # Fixture trust is test-local. Production always calls the shipped exact allowlist.
    monkeypatch.setitem(profile_catalog._APPROVED, profile.profile_id, ("fixture.json", profile.fingerprint))
    monkeypatch.setattr(install, "extract_native", lambda source, dest, scratch: (dest / "msvcp140.dll").write_bytes(b"fixture"))
    monkeypatch.setattr(install, "probe_runtime", ready)
    return install.PiperProvisioner(tmp_path / "Piper \u017c\u00f3\u0142ty", HOST), profile, install.DirectorySource(cache)


def test_install_restart_and_idempotence_do_not_read_source_or_probe_again(fixture, monkeypatch):
    service, profile, source = fixture
    assert service.active(profile) is None
    installed = service.install(profile, source)
    assert installed.health.status == HealthStatus.READY
    assert (installed.directory / "python311._pth").read_text() == "python311.zip\n.\npackages\nworker\n"
    assert not (installed.directory / "pyvenv.cfg").exists()
    monkeypatch.setattr(install, "probe_runtime", lambda *_: pytest.fail("Unrequested probe"))
    restarted = install.PiperProvisioner(service.root, HOST)
    assert restarted.active(profile) == installed
    assert restarted.install(profile, None) == installed


@pytest.mark.parametrize("damage", [b"", b"corrupt", b"x" * 10000])
def test_size_and_hash_failure_never_activates(fixture, damage):
    service, profile, source = fixture
    (source.directory / profile.interpreter.filename).write_bytes(damage)
    with pytest.raises(install.ProvisioningError, match="size|hash"):
        service.install(profile, source)
    assert service.active(profile) is None


def test_unapproved_and_incompatible_rejected_before_source_or_storage(fixture):
    service, profile, _ = fixture
    with pytest.raises(ValueError, match="allowlist"):
        service.install(replace(profile, profile_version="9.0"), None)
    incompatible = install.PiperProvisioner(service.root, HostCapabilities("linux", "x86_64", ("cpu",)))
    with pytest.raises(ValueError, match="Windows"):
        incompatible.install(profile, None)
    assert not service.root.exists()


@pytest.mark.parametrize("stage", ["source", "native", "probe", "publish"])
def test_interrupted_install_stays_inactive_and_restart_installs_new_candidate(fixture, monkeypatch, stage):
    service, profile, source = fixture
    def stop(*args):
        raise KeyboardInterrupt("Simulated interruption")
    with monkeypatch.context() as patch:
        if stage == "source":
            patch.setattr(install.DirectorySource, "open", stop)
        else:
            owner, name = (install.os, "replace") if stage == "publish" else (install, {"native": "extract_native", "probe": "probe_runtime"}[stage])
            patch.setattr(owner, name, stop)
        with pytest.raises(KeyboardInterrupt):
            service.install(profile, source)
    abandoned = set((service.root / "envs").iterdir())
    assert service.active(profile) is None
    new = install.PiperProvisioner(service.root, HOST).install(profile, source)
    assert new.directory not in abandoned
    assert abandoned <= set((service.root / "envs").iterdir())


def test_failed_probe_is_retained_but_inactive(fixture, monkeypatch):
    service, profile, source = fixture
    failure = ProfileHealth(profile.profile_id, profile.fingerprint, HealthStatus.FAILED,
                            datetime(2026, 9, 13, tzinfo=UTC),
                            (HealthObservation(HealthCheck.CPU_BACKEND, False, "DLL unavailable"),))
    monkeypatch.setattr(install, "probe_runtime", lambda *_: failure)
    with pytest.raises(install.ProvisioningError, match="health check failed"):
        service.install(profile, source)
    assert service.active(profile) is None
    evidence = next(service.root.glob("envs/*/failed-health.json"))
    assert json.loads(evidence.read_text())["status"] == "failed"


@pytest.mark.parametrize("damage", ["remove", "change", "extra", "pointer", "receipt", "worker"])
def test_restart_fails_closed_for_corrupted_install(fixture, damage):
    service, profile, source = fixture
    root = service.install(profile, source).directory
    if damage == "remove":
        (root / "python.exe").unlink()
    elif damage == "change":
        (root / "python.exe").write_bytes(b"changed")
    elif damage == "extra":
        (root / "packages/rogue.py").write_text("pass")
    elif damage == "pointer":
        (service.root / "active" / (profile.profile_id + ".json")).write_text('{"generation":"../../escape"}')
    elif damage == "worker":
        (root / "worker/app/runtime/worker.py").write_text("pass")
    else:
        (root / "ready.json").write_text("{}")
    with pytest.raises(install.ProvisioningError, match="verification"):
        service.active(profile)


def test_storage_can_relocate_and_private_scratch_is_not_install_identity(fixture, tmp_path):
    import shutil
    service, profile, source = fixture
    root = service.install(profile, source).directory
    (root / "temp/cache.txt").write_text("scratch")
    relocated = tmp_path / "relocated"
    shutil.copytree(service.root, relocated)
    recovered = install.PiperProvisioner(relocated, HOST).active(profile)
    assert recovered.directory.parent.parent == relocated
    assert recovered.health.status == HealthStatus.READY


def test_concurrent_install_is_rejected_and_lock_is_reusable(fixture):
    service, profile, source = fixture
    service.root.mkdir()
    with install._install_lock(service.root):
        with pytest.raises(install.ProvisioningError, match="lock"):
            service.install(profile, source)
    assert service.install(profile, source).health.status == HealthStatus.READY


def test_process_death_releases_lock_and_never_publishes_candidate(fixture, tmp_path):
    import subprocess
    import sys
    service, profile, source = fixture
    manifest = tmp_path / "fixture-profile.json"
    manifest.write_text(json.dumps(profile.to_payload()), encoding="utf-8")
    code = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app.runtime import provisioning as p, profile_catalog as c
from app.runtime.profiles import RuntimeProfile, HostCapabilities
profile = RuntimeProfile.from_json(Path(sys.argv[3]).read_text(encoding='utf-8'))
c._APPROVED[profile.profile_id] = ('fixture', profile.fingerprint)
p.extract_native = lambda source, dest, scratch: None
p.probe_runtime = lambda *_: os._exit(17)
p.PiperProvisioner(Path(sys.argv[2]), HostCapabilities('windows', 'x86_64', ('cpu',))).install(
    profile, p.DirectorySource(Path(sys.argv[4])))
"""
    result = subprocess.run([getattr(sys, "_base_executable", sys.executable), "-I", "-c", code,
                             str(Path(__file__).resolve().parents[2]), str(service.root), str(manifest),
                             str(source.directory)], capture_output=True, timeout=20)
    assert result.returncode == 17, result.stderr
    abandoned = set((service.root / "envs").iterdir())
    assert service.active(profile) is None
    recovered = service.install(profile, source)
    assert recovered.directory not in abandoned


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/drive", "x\\escape", "CON", "x.",
                                 "x ", "x:a", "a/../../b", "a//b", "bad.pth", "x.data/scripts/run"])
def test_unsafe_wheel_paths_rejected(tmp_path, name):
    path = tmp_path / "wheel.zip"
    path.write_bytes(archive({name: b"bad"}))
    with pytest.raises(install.ProvisioningError):
        install._extract_zip(path, tmp_path / "dest", wheel=True)


def test_piper_license_data_relocated_without_execution(tmp_path):
    path = tmp_path / "wheel.zip"
    path.write_bytes(archive({"piper_tts-1.6.0.data/data/COPYING": b"license"}))
    install._extract_zip(path, tmp_path / "dest", wheel=True)
    assert (tmp_path / "dest/share/piper_tts-1.6.0.data/COPYING").read_bytes() == b"license"


def test_zip_symlink_and_case_collisions_rejected(tmp_path):
    path = tmp_path / "wheel.zip"
    link = zipfile.ZipInfo("link")
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(link, "../outside")
    with pytest.raises(install.ProvisioningError, match="links"):
        install._extract_zip(path, tmp_path / "links")
    path.write_bytes(archive({"A": b"1", "a": b"2"}))
    with pytest.raises(install.ProvisioningError, match="Duplicate"):
        install._extract_zip(path, tmp_path / "collision")

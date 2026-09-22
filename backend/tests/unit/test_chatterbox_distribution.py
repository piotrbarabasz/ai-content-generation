"""Approved D029 distribution and isolated provisioner acceptance tests."""

from dataclasses import replace
from hashlib import sha256
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from app.runtime import chatterbox_assets as assets_module
from app.runtime import chatterbox_provisioning as provisioning
from app.runtime.chatterbox_assets import ChatterboxAssets
from app.runtime.chatterbox_distribution import (
    ChatterboxArtifact, ChatterboxDistribution, ChatterboxPackage,
    distribution_from_payload, load_approved_chatterbox_distribution,
)
from app.runtime.chatterbox_profile import ChatterboxHealth, MODEL_REVISION, profile_fingerprint
from app.runtime.profiles import ProfileError
from app.runtime.provisioning import DirectorySource


def archive(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    return out.getvalue()


def test_approved_distribution_closes_reviewed_windows_cuda_runtime():
    profile = load_approved_chatterbox_distribution()
    assert profile.profile_id == "chatterbox-v3-cu124-windows-x64"
    assert len(profile.packages) == 111
    assert profile.package_versions["chatterbox-tts"] == "0.1.7"
    assert profile.package_versions["torch"] == profile.package_versions["torchaudio"] == "2.6.0+cu124"
    assert profile.package_versions["spacy-pkuseg"] == "1.0.1"
    assert len(profile.payload["native_libraries"]) == 37
    assert profile.auxiliary.filename == "spacy_ontonotes.zip"


def test_valid_schema_is_not_approval_when_one_byte_of_content_changes():
    payload = load_approved_chatterbox_distribution().to_payload()
    payload["packages"][0]["license"]["declared"] = ["changed"]
    with pytest.raises(ProfileError, match="allowlist"):
        distribution_from_payload(payload)


def test_approved_distribution_rejects_non_x64_host_before_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(provisioning.platform, "machine", lambda: "ARM64")
    service = provisioning.ChatterboxProvisioner(tmp_path / "runtime")
    with pytest.raises(ProfileError, match="Windows x64"):
        service.install(load_approved_chatterbox_distribution(), None)
    assert not service.root.exists()


def test_mutated_or_forged_distribution_object_is_rejected_before_storage(tmp_path):
    profile = load_approved_chatterbox_distribution()
    profile.payload["packages"][0]["version"] = "0"
    service = provisioning.ChatterboxProvisioner(tmp_path / "runtime")
    with pytest.raises(ProfileError, match="allowlist|approved"):
        service.install(profile, None)
    assert not service.root.exists()


def test_cangjie_is_materialized_as_verified_offline_hf_cache(tmp_path, monkeypatch):
    content = b"fixed cangjie"
    monkeypatch.setattr(assets_module, "MODEL_FILES",
                        (("Cangjie5_TC.json", len(content), sha256(content).hexdigest()),))
    monkeypatch.setattr(assets_module, "profile_fingerprint", lambda: "fixture")
    models = ChatterboxAssets(tmp_path / "models")
    installed = models.install(SimpleNamespace(open=lambda name: io.BytesIO(content)))
    cached = installed / "models--ResembleAI--chatterbox/snapshots" / MODEL_REVISION / "Cangjie5_TC.json"
    assert cached.read_bytes() == content
    assert (installed / "models--ResembleAI--chatterbox/refs/main").read_text() == MODEL_REVISION
    cached.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Cangjie"):
        models.installed()


@pytest.fixture
def tiny_distribution(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    interpreter_bytes = archive({"python.exe": b"python", "python311.zip": b"stdlib"})
    wheel_bytes = archive({"chatterbox_tts/__init__.py": b"",
                           "chatterbox_tts-0.1.7.dist-info/METADATA": b"Name: chatterbox-tts\nVersion: 0.1.7\n"})
    native_bytes = b"native"
    auxiliary_bytes = b"auxiliary"

    def artifact(filename, data, unpacked=None):
        (cache / filename).write_bytes(data)
        return ChatterboxArtifact(filename, sha256(data).hexdigest(), len(data), unpacked)

    package = ChatterboxPackage("chatterbox-tts", "0.1.7",
                                artifact("chatterbox_tts-0.1.7-py3-none-any.whl", wheel_bytes,
                                         sum(item.file_size for item in zipfile.ZipFile(io.BytesIO(wheel_bytes)).infolist())))
    distribution = ChatterboxDistribution(
        {"native_libraries": []}, "a" * 64, (package,),
        artifact("python-3.11.9-embed-amd64.zip", interpreter_bytes),
        artifact("VC_redist.x64.exe", native_bytes),
        artifact("spacy_ontonotes.zip", auxiliary_bytes),
    )
    monkeypatch.setattr(provisioning.ChatterboxProvisioner, "_require_host", staticmethod(lambda profile: None))
    monkeypatch.setattr(provisioning, "extract_native",
                        lambda source, destination, scratch: (destination / "vcruntime140.dll").write_bytes(b"native"))
    monkeypatch.setattr(provisioning, "_extract_auxiliary",
                        lambda source, destination: (destination / "runtime-cache").mkdir())
    health = ChatterboxHealth(profile_fingerprint(), "cuda:0", ("en", "pl"), "fixture GPU", 1024)

    async def probe(*args):
        return health

    monkeypatch.setattr(provisioning, "probe_chatterbox", probe)
    return provisioning.ChatterboxProvisioner(tmp_path / "managed"), distribution, DirectorySource(cache)


def test_chatterbox_install_activates_only_after_health_and_survives_restart(tiny_distribution):
    service, profile, source = tiny_distribution
    installed = service.install(profile, source)
    assert installed.health.gpu_name == "fixture GPU"
    assert installed.worker_launch().executable == installed.directory / "python.exe"
    restarted = provisioning.ChatterboxProvisioner(service.root).active(profile)
    assert restarted == installed
    assert service.install(profile, None) == installed


def test_chatterbox_restart_rejects_changed_private_runtime(tiny_distribution):
    service, profile, source = tiny_distribution
    installed = service.install(profile, source)
    (installed.directory / "packages/chatterbox_tts/__init__.py").write_bytes(b"changed")
    with pytest.raises(provisioning.ProvisioningError, match="verification"):
        service.active(profile)


def test_failed_health_is_retained_but_never_activated(tiny_distribution, monkeypatch):
    service, profile, source = tiny_distribution

    async def fail(*args):
        raise ValueError("fixture CUDA unavailable")

    monkeypatch.setattr(provisioning, "probe_chatterbox", fail)
    with pytest.raises(provisioning.ProvisioningError, match="health check"):
        service.install(profile, source)
    assert service.active(profile) is None
    evidence = next(service.root.glob("envs/*/failed-health.json"))
    assert json.loads(evidence.read_text())["error"] == "fixture CUDA unavailable"


def test_reviewed_wheel_extractor_omits_only_setuptools_distutils_hook(tmp_path):
    wheel = tmp_path / "setuptools.whl"
    payload = archive({"setuptools/__init__.py": b"", "distutils-precedence.pth": b"import bad"})
    wheel.write_bytes(payload)
    unpacked = sum(item.file_size for item in zipfile.ZipFile(io.BytesIO(payload)).infolist())
    provisioning._extract_wheel(wheel, tmp_path / "packages", unpacked, "setuptools")
    assert not (tmp_path / "packages/distutils-precedence.pth").exists()
    with pytest.raises(provisioning.ProvisioningError, match="hooks"):
        provisioning._extract_wheel(wheel, tmp_path / "other", unpacked, "other")

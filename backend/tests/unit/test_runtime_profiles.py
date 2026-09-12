"""Offline contract tests: no downloads, installation or runtime imports."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.runtime.profile_catalog import approved_profile_ids, load_approved_profile, require_approved
from app.runtime.profiles import (
    HealthCheck, HealthObservation, HealthStatus, HostCapabilities, ProfileError, ProfileHealth, RuntimeProfile,
)


HOST = HostCapabilities("windows", "x86_64", ("cpu",))
PROFILE_ID = "piper-cpu-windows-x64"


@pytest.fixture
def profile():
    return load_approved_profile(PROFILE_ID, HOST)


def test_approved_manifest_is_complete_immutable_and_order_independent(profile):
    assert approved_profile_ids() == (PROFILE_ID,)
    assert profile.python_version == "3.11.9"
    assert profile.native_runtime.version == "14.44.35211.0"
    assert {p.name: p.version for p in profile.packages} == {
        "piper-tts": "1.6.0", "onnxruntime": "1.28.0", "pathvalidate": "3.3.1", "numpy": "2.4.6",
        "flatbuffers": "25.12.19", "protobuf": "7.35.1", "packaging": "26.3"}
    payload = profile.to_payload()
    payload["packages"].reverse()
    equivalent = RuntimeProfile.from_json(json.dumps(payload, sort_keys=False))
    assert equivalent == profile and equivalent.fingerprint == profile.fingerprint
    payload["packages"][0]["artifact"]["sha256"] = "0" * 64
    assert equivalent == profile
    with pytest.raises(FrozenInstanceError):
        profile.packages[0].version = "latest"
    assert profile.to_payload()["model_requirement"]["bundled"] is False
    assert profile.to_payload()["health_check"] == {"id": "piper-cpu-imports-v1", "worker_protocol": 1}


@pytest.mark.parametrize("path,value", [
    (("schema_version",), 2), (("schema_version",), True),
    (("target", "os"), "linux"), (("target", "architecture"), "aarch64"), (("target", "device"), "cuda"),
    (("interpreter", "version"), "3.11.*"), (("interpreter", "implementation"), "pypy"),
    (("interpreter", "abi"), "cp312"), (("profile_version",), "latest"),
    (("interpreter", "artifact", "sha256"), "unverified"),
    (("interpreter", "artifact", "filename"), "../python.zip"),
    (("interpreter", "artifact", "size_bytes"), True), (("interpreter", "artifact", "size_bytes"), 0),
    (("interpreter", "artifact", "url"), "file:///tmp/python.zip"),
    (("interpreter", "artifact", "url"), "https://user:password@www.python.org/python-3.11.9-embed-amd64.zip"),
    (("interpreter", "artifact", "url"), "https://evil.invalid/python-3.11.9-embed-amd64.zip"),
    (("interpreter", "artifact", "source_url"), ""),
    (("interpreter", "artifact", "license_expression"), "unknown"),
    (("interpreter", "artifact", "license_url"), "http://example.com/license"),
    (("model_requirement", "bundled"), True), (("model_requirement", "bundled"), 0),
    (("model_requirement", "catalog"), "arbitrary-plugin"),
    (("health_check", "id"), "python -c exec(...)"), (("health_check", "worker_protocol"), True),
    (("native_runtime", "version"), "latest"), (("native_runtime", "required_libraries"), []),
    (("native_runtime", "artifact", "sha256"), ""),
])
def test_invalid_or_incompatible_profile_is_rejected(profile, path, value):
    payload = profile.to_payload()
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ProfileError):
        RuntimeProfile.from_payload(payload)


@pytest.mark.parametrize("host", [HostCapabilities("linux", "x86_64", ["cpu"]),
                                  HostCapabilities("windows", "aarch64", ["cpu"]),
                                  HostCapabilities("windows", "x86_64", ["cuda"])])
def test_host_compatibility_checked_before_approval_or_installation(host):
    with pytest.raises(ProfileError, match="requires Windows"):
        load_approved_profile(PROFILE_ID, host)


@pytest.mark.parametrize("problem", ["missing", "duplicate", "wrong-version", "cycle", "extra", "incompatible-wheel"])
def test_package_closure_and_target_wheels_must_match(profile, problem):
    payload = profile.to_payload()
    packages = payload["packages"]
    piper = next(p for p in packages if p["name"] == "piper-tts")
    if problem == "missing":
        packages[:] = [p for p in packages if p["name"] != "numpy"]
    elif problem == "duplicate":
        packages.append(deepcopy(piper))
    elif problem == "wrong-version":
        piper["dependencies"]["onnxruntime"] = "1.0.0"
    elif problem == "cycle":
        next(p for p in packages if p["name"] == "onnxruntime")["dependencies"]["piper-tts"] = piper["version"]
    elif problem == "extra":
        piper["dependencies"].pop("pathvalidate")
    else:
        pin = piper["artifact"]
        old = pin["filename"]
        pin["filename"] = old.replace("win_amd64", "win_arm64")
        pin["url"] = pin["url"].replace(old, pin["filename"])
    with pytest.raises(ProfileError):
        RuntimeProfile.from_payload(payload)


def test_valid_schema_is_not_approval_and_cannot_select_untrusted_content(profile):
    payload = profile.to_payload()
    payload["packages"][0]["artifact"]["sha256"] = "a" * 64
    unapproved = RuntimeProfile.from_payload(payload)
    assert unapproved.fingerprint != profile.fingerprint
    with pytest.raises(ProfileError, match="allowlist"):
        require_approved(unapproved, HOST)
    with pytest.raises(ProfileError, match="Unknown"):
        load_approved_profile("../../manifest", HOST)


def test_json_schema_rejects_commands_unknown_fields_duplicates_and_oversize(profile):
    for path in ((), ("health_check",), ("interpreter",), ("interpreter", "artifact"), ("native_runtime",)):
        payload = profile.to_payload()
        node = payload
        for part in path:
            node = node[part]
        node["command"] = "run-me"
        with pytest.raises(ProfileError):
            RuntimeProfile.from_json(json.dumps(payload))
    for text in ('{"schema_version":1,"schema_version":1}', "[" * 1100, " " * (256 * 1024 + 1), "null"):
        with pytest.raises(ProfileError):
            RuntimeProfile.from_json(text)


def test_health_result_is_typed_serializable_and_bound_to_exact_profile(profile):
    observations = [HealthObservation(check, True, "fixture observation") for check in HealthCheck]
    health = ProfileHealth(profile.profile_id, profile.fingerprint, HealthStatus.READY,
                           datetime(2026, 9, 12, tzinfo=UTC), observations)
    observations.clear()
    health.ensure_matches(profile)
    assert ProfileHealth.from_payload(health.to_payload()) == health
    assert len(health.observations) == 4
    with pytest.raises(ProfileError, match="different profile"):
        health.ensure_matches(replace(profile, profile_version="1.0.1"))


@pytest.mark.parametrize("status,checks", [
    (HealthStatus.READY, ()),
    (HealthStatus.READY, (HealthObservation(HealthCheck.INTERPRETER, True, "only one check"),)),
    (HealthStatus.READY, tuple(HealthObservation(c, c != HealthCheck.CPU_BACKEND, "fixture") for c in HealthCheck)),
    (HealthStatus.FAILED, ()),
    (HealthStatus.INCOMPATIBLE, (HealthObservation(HealthCheck.INTERPRETER, True, "no failure"),)),
    (HealthStatus.NOT_INSTALLED, (HealthObservation(HealthCheck.PACKAGES, True, "contradiction"),)),
])
def test_health_cannot_claim_readiness_without_complete_evidence(profile, status, checks):
    with pytest.raises(ProfileError):
        ProfileHealth(profile.profile_id, profile.fingerprint, status, datetime(2026, 9, 12, tzinfo=UTC), checks)


def test_missing_failed_and_incompatible_health_have_explicit_outcomes(profile):
    for status in (HealthStatus.NOT_INSTALLED, HealthStatus.FAILED, HealthStatus.INCOMPATIBLE):
        checks = () if status == HealthStatus.NOT_INSTALLED else (HealthObservation(HealthCheck.INTERPRETER, False, "fixture reason"),)
        result = ProfileHealth(profile.profile_id, profile.fingerprint, status, datetime(2026, 9, 12, tzinfo=UTC), checks)
        assert ProfileHealth.from_payload(result.to_payload()) == result


def test_discovery_is_offline_and_does_not_import_or_execute_optional_runtimes():
    code = """
import sys, socket, subprocess
sys.path.insert(0, sys.argv[1])
class NoRuntime:
    def find_spec(self, fullname, *args):
        assert fullname.split('.')[0] not in ('torch', 'onnxruntime', 'piper', 'numpy', 'PySide6'), fullname
        return None
sys.meta_path.insert(0, NoRuntime())
def forbidden(*args, **kwargs):
    raise AssertionError('Discovery attempted external I/O or process execution')
socket.socket = forbidden
subprocess.Popen = forbidden
from app.runtime.profile_catalog import approved_profile_ids, load_approved_profile
from app.runtime.profiles import HostCapabilities
for name in approved_profile_ids():
    load_approved_profile(name, HostCapabilities('windows', 'x86_64', ('cpu',)))
assert 'app.providers' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-I", "-c", code, str(Path(__file__).resolve().parents[2])],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr

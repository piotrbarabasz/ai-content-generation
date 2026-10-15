"""Offline lifecycle acceptance with real receipts and fresh Python processes."""

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.runtime import chatterbox_refresh as module
from app.runtime.chatterbox_profile import ChatterboxHealth, profile_fingerprint
from app.runtime.provisioning import ProvisioningError, _inventory, _json, _worker_files
from app.runtime.supervisor import WorkerLaunch


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_isolated_managed_launch_cannot_write_import_bytecode(tmp_path):
    (tmp_path / "dependency.py").write_text("value = 42\n")
    entry = tmp_path / "entry.py"
    entry.write_text("import sys\nsys.path.insert(0, sys.argv[0].rsplit('entry.py', 1)[0])\n"
                     "import dependency\nassert dependency.value == 42\n"
                     "assert sys.flags.isolated and sys.dont_write_bytecode\n")
    launch = WorkerLaunch(Path(getattr(sys, "_base_executable", sys.executable)), entry)
    subprocess.run(launch.command(), check=True, capture_output=True, timeout=30)
    assert not (tmp_path / "__pycache__").exists()


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        # The synthetic interpreter/native bytes are never executed. Keep Linux
        # CI exercising every inventory/activation path and actual subprocess,
        # replacing only the Windows host gate in this test and its child.
        monkeypatch.setattr(module.ChatterboxProvisioner, "_require_host", staticmethod(lambda profile: None))
        run = subprocess.run

        def portable_run(command, **kwargs):
            command = list(command)
            command[4] = command[4].replace(
                "r=ChatterboxRefresh",
                "from app.runtime.chatterbox_provisioning import ChatterboxProvisioner; "
                "ChatterboxProvisioner._require_host=staticmethod(lambda profile: None); "
                "r=ChatterboxRefresh",
            )
            return run(command, **kwargs)

        monkeypatch.setattr(module.subprocess, "run", portable_run)
    operation = module.ChatterboxRefresh(tmp_path / "managed")
    root = operation.service.root
    old = root / "envs" / ("a" * 32)
    old.mkdir(parents=True)
    health = ChatterboxHealth(profile_fingerprint(), "cuda:2", ("en", "pl"), "fixture GPU", 1024)
    files = {"python.exe": b"synthetic interpreter", "packages/torch/native.dll": b"native",
             "python311._pth": b"python311.zip\n.\npackages\nworker\n",
             "runtime-cache/pkuseg/seed": b"seed", **_worker_files()}
    files["worker/app/runtime/worker.py"] = b"# previous reviewed worker\n"
    files["worker/app/runtime/obsolete.py"] = b"# removed module\n"
    for name, data in files.items():
        path = old / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    _json(old / "profile.json", operation.distribution.to_payload())
    _json(old / "ready.json", {"schema_version": 1,
          "distribution_fingerprint": operation.distribution.fingerprint,
          "health": health.to_payload(), "files": _inventory(old)})
    operation.pointer.parent.mkdir()
    _json(operation.pointer, {"schema_version": 1, "generation": old.name,
                             "fingerprint": operation.distribution.fingerprint})
    for name in ("models/weights", "projects/project.sqlite", "projects/completed.wav"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"untouched")

    async def probe(launch, device, work_root):
        assert launch.executable.parent != old
        assert device == "cuda:2"
        assert launch.environment["HF_HUB_OFFLINE"] == "1"
        assert Path(launch.environment["AICS_CHATTERBOX_DISTRIBUTION"]).parent == launch.executable.parent
        return health

    monkeypatch.setattr(module, "probe_chatterbox", probe)
    return operation, old


def test_check_reports_exact_differences_without_any_writes(runtime, tmp_path, capsys):
    operation, old = runtime
    before = snapshot(tmp_path)
    installed, _, _, changes = operation.check()
    assert installed.directory == old
    assert changes == [{"path": "worker/app/runtime/obsolete.py", "change": "removed"},
                       {"path": "worker/app/runtime/worker.py", "change": "changed"}]
    assert module.main(["--root", str(operation.service.root), "--check"]) == 0
    assert "changed: worker/app/runtime/worker.py" in capsys.readouterr().out
    assert snapshot(tmp_path) == before


def test_refresh_copies_independent_generation_and_reopens_in_fresh_process(runtime, tmp_path):
    operation, old = runtime
    before = snapshot(old)
    outside = {name: snapshot(tmp_path / name) for name in ("models", "projects")}
    installed, changes = operation.refresh()
    assert changes
    assert installed.directory != old
    assert installed.health.device == "cuda:2"
    assert snapshot(old) == before
    assert {name: snapshot(tmp_path / name) for name in outside} == outside
    for name, data in _worker_files().items():
        assert (installed.directory / name).read_bytes() == data
    assert not (installed.directory / "worker/app/runtime/obsolete.py").exists()
    receipt = json.loads((installed.directory / "ready.json").read_text())
    assert receipt["files"] == _inventory(installed.directory)
    assert operation.service.active(operation.distribution) == installed
    # This is a real isolated application subprocess, not a mocked verification.
    operation.verify_fresh()
    assert module.main(["--root", str(operation.service.root), "--verify"]) == 0
    for name in before:
        if name.startswith("worker/") or name == "ready.json":
            continue
        assert (installed.directory / name).read_bytes() == before[name]
        assert (installed.directory / name).stat().st_nlink == 1
    # Mutating a copied dependency cannot affect the previous generation.
    (installed.directory / "packages/torch/native.dll").write_bytes(b"changed")
    assert snapshot(old) == before


def test_repeated_refresh_is_idempotent_and_unchanged_check_is_read_only(runtime, monkeypatch):
    operation, _ = runtime
    installed, _ = operation.refresh()
    before = snapshot(operation.service.root)

    async def forbidden(*args):
        pytest.fail("An unchanged generation must not be health-probed/copied")

    monkeypatch.setattr(module, "probe_chatterbox", forbidden)
    assert operation.check()[3] == []
    again, changes = operation.refresh()
    assert again == installed and changes == []
    assert snapshot(operation.service.root) == before


@pytest.mark.parametrize("target", ["packages/torch/native.dll", "worker/app/runtime/worker.py", "profile.json"])
def test_corruption_is_rejected_even_when_it_is_a_worker(runtime, target):
    operation, old = runtime
    pointer = operation.pointer.read_bytes()
    (old / target).write_bytes(b"corrupt")
    before = snapshot(old)
    with pytest.raises((ValueError, ProvisioningError), match="incomplete|changed"):
        operation.refresh()
    assert operation.pointer.read_bytes() == pointer
    assert snapshot(old) == before
    assert list((operation.service.root / "envs").iterdir()) == [old]


@pytest.mark.parametrize("where", ["pointer", "receipt"])
def test_invalid_distribution_fingerprint_is_rejected(runtime, where):
    operation, old = runtime
    path = operation.pointer if where == "pointer" else old / "ready.json"
    value = json.loads(path.read_text())
    value["fingerprint" if where == "pointer" else "distribution_fingerprint"] = "0" * 64
    path.write_text(json.dumps(value))
    before = snapshot(operation.service.root)
    with pytest.raises((ValueError, ProvisioningError)):
        operation.check()
    assert snapshot(operation.service.root) == before


@pytest.mark.parametrize("failure", ["health", "copy", "disk", "candidate_verify", "active_verify", "health_tamper"])
def test_failures_preserve_previous_pointer_and_files(runtime, monkeypatch, failure):
    operation, old = runtime
    pointer, original = operation.pointer.read_bytes(), snapshot(old)
    if failure == "health":
        async def fail(*args):
            raise ValueError("synthetic CUDA failure")
        monkeypatch.setattr(module, "probe_chatterbox", fail)
    elif failure == "copy":
        copy = operation._copy
        def interrupted(source, target, digest):
            copy(source, target, digest)
            raise KeyboardInterrupt()
        monkeypatch.setattr(operation, "_copy", interrupted)
    elif failure == "disk":
        monkeypatch.setattr(module.shutil, "disk_usage", lambda root: SimpleNamespace(free=0))
    elif failure.endswith("verify"):
        verify = operation.verify_fresh
        def fail_verify(generation=None):
            if failure == "active_verify" and generation:
                return verify(generation)
            raise ProvisioningError("synthetic fresh process failure")
        monkeypatch.setattr(operation, "verify_fresh", fail_verify)
    else:
        async def tamper(launch, device, root):
            (launch.executable.parent / "packages/torch/native.dll").write_bytes(b"tamper")
            return ChatterboxHealth(profile_fingerprint(), device, ("en", "pl"), "fixture GPU", 1024)
        monkeypatch.setattr(module, "probe_chatterbox", tamper)
    with pytest.raises((ProvisioningError, KeyboardInterrupt)):
        operation.refresh()
    assert operation.pointer.read_bytes() == pointer
    assert snapshot(old) == original
    if failure == "disk":
        assert list((operation.service.root / "envs").iterdir()) == [old]


def test_startup_still_rejects_old_worker_and_never_refreshes(runtime, monkeypatch):
    operation, _ = runtime
    before = snapshot(operation.service.root)
    def forbidden(*args, **kwargs):
        pytest.fail("Startup must not refresh")
    monkeypatch.setattr(module.ChatterboxRefresh, "refresh", forbidden)
    with pytest.raises(ProvisioningError, match="verification"):
        operation.service.active(operation.distribution)
    assert snapshot(operation.service.root) == before


def test_explicit_rollback_requires_worker_matching_target_application(runtime, monkeypatch):
    operation, old = runtime
    installed, _ = operation.refresh()
    current_pointer = operation.pointer.read_bytes()
    with pytest.raises(ProvisioningError, match="worker differs"):
        operation.rollback(old.name)
    assert operation.pointer.read_bytes() == current_pointer
    # Model switching back to the previous application version: its exact
    # historical worker closure makes the retained generation valid again.
    old_workers = {name: (old / name).read_bytes() for name in snapshot(old) if name.startswith("worker/")}
    monkeypatch.setattr(module, "_worker_files", lambda: old_workers)
    from app.runtime import chatterbox_provisioning
    monkeypatch.setattr(chatterbox_provisioning, "_worker_files", lambda: old_workers)
    # The real fresh subprocess uses the installed app's closure; emulate the
    # target app's fresh checker here. Real subprocess behavior is covered above.
    monkeypatch.setattr(operation, "verify_fresh", lambda generation=None: operation._verify_local(generation))
    assert operation.rollback(old.name).directory == old
    assert installed.directory.exists()


def test_verify_fresh_rejects_corruption_in_an_actual_new_process(runtime):
    operation, _ = runtime
    installed, _ = operation.refresh()
    (installed.directory / "python.exe").write_bytes(b"corrupt")
    with pytest.raises(ProvisioningError, match="Fresh-process"):
        operation.verify_fresh()


def test_rollback_reopens_matching_retained_generation_in_real_fresh_process(runtime):
    operation, old = runtime
    installed, _ = operation.refresh()
    # Two independently stored generations can match the target application.
    # Simulate another valid active generation, retaining the one to roll back to.
    other = installed.directory.parent / ("b" * 32)
    shutil.copytree(installed.directory, other)
    value = json.loads(operation.pointer.read_text())
    value["generation"] = other.name
    operation.pointer.write_text(json.dumps(value))
    before = {path.name: snapshot(path) for path in (old, installed.directory, other)}
    assert operation.rollback(installed.directory.name) == installed
    operation.verify_fresh()
    assert {path.name: snapshot(path) for path in (old, installed.directory, other)} == before

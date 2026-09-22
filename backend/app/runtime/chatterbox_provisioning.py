"""Transactional provisioning of the approved D029 Windows/CUDA runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import tempfile
from uuid import uuid4
import zipfile

from .chatterbox_audio import _private_cache_environment, probe_chatterbox
from .chatterbox_distribution import ChatterboxDistribution, distribution_from_payload
from .chatterbox_profile import ChatterboxHealth
from .native_piper import extract_native
from .piper_health import private_environment
from .profiles import ProfileError
from .provisioning import (
    ArtifactSource, ProvisioningError, _install_lock, _inventory, _json, _safe_parts,
    _sha, _worker_files,
)


def _extract_wheel(archive: Path, destination: Path, expected_size: int, package_name: str) -> None:
    """Install one already-reviewed wheel without executing package code/hooks."""
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 100_000 or sum(item.file_size for item in entries) != expected_size:
            raise ProvisioningError("Reviewed wheel extraction size differs from its profile.")
        seen = set()
        for info in entries:
            parts = _safe_parts(info.orig_filename.rstrip("/") if info.is_dir() else info.orig_filename)
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ProvisioningError("Wheel links/special files are unsupported.")
            if parts[0].endswith(".data"):
                if len(parts) < 3 or parts[1] not in {"purelib", "platlib", "data", "scripts", "headers"}:
                    raise ProvisioningError("Unsupported wheel installation scheme.")
                scheme = parts[1]
                prefix = [] if scheme in {"purelib", "platlib"} else [scheme]
                parts = [*prefix, *parts[2:]]
            if parts[-1].lower().endswith((".pth", "._pth")):
                # The reviewed setuptools wheel uses this optional hook only to
                # replace stdlib distutils. Runtime inference never builds code;
                # omitting it preserves the no-path-hooks private interpreter.
                if package_name == "setuptools" and parts == ["distutils-precedence.pth"]:
                    continue
                raise ProvisioningError("Wheel path hooks are unsupported.")
            key = "/".join(parts).casefold()
            if key in seen:
                raise ProvisioningError("Duplicate wheel destination.")
            seen.add(key)
            target = destination.joinpath(*parts)
            if info.is_dir():
                if target.exists() and not target.is_dir():
                    raise ProvisioningError("Wheel destination collides with a file.")
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with bundle.open(info) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
                except FileExistsError as exc:
                    raise ProvisioningError("Reviewed wheels contain a cross-package file collision.") from exc


def _extract_auxiliary(archive: Path, root: Path) -> None:
    target = root / "runtime-cache" / "pkuseg"
    model = target / "spacy_ontonotes"
    target.mkdir(parents=True)
    shutil.copyfile(archive, target / "spacy_ontonotes.zip")
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) != 2 or sum(item.file_size for item in entries) != 60_193_935:
            raise ProvisioningError("Pinned spacy-pkuseg model archive has unexpected contents.")
        model.mkdir()
        for info in entries:
            parts = _safe_parts(info.orig_filename)
            if len(parts) != 1 or info.is_dir() or (info.external_attr >> 16) & 0o170000 not in (0, stat.S_IFREG):
                raise ProvisioningError("Unsafe spacy-pkuseg model archive.")
            with bundle.open(info) as source, (model / parts[0]).open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)


@dataclass(frozen=True, slots=True)
class InstalledChatterboxRuntime:
    directory: Path
    health: ChatterboxHealth
    distribution_fingerprint: str

    @property
    def cache_root(self):
        return self.directory

    def worker_launch(self):
        from .supervisor import WorkerLaunch
        environment = private_environment(self.directory) | _private_cache_environment(self.directory)
        environment["AICS_CHATTERBOX_DISTRIBUTION"] = str(self.directory / "profile.json")
        return WorkerLaunch(self.directory / "python.exe",
                            self.directory / "worker/app/runtime/chatterbox_worker.py",
                            environment, self.directory)


class ChatterboxProvisioner:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    @staticmethod
    def _require_host(distribution: ChatterboxDistribution):
        if not isinstance(distribution, ChatterboxDistribution):
            raise ProfileError("Only the approved Chatterbox distribution can be provisioned.")
        if distribution_from_payload(distribution.to_payload()) != distribution:
            raise ProfileError("Chatterbox distribution differs from the approved immutable content.")
        if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
            raise ProfileError("The approved Chatterbox distribution requires Windows x64/CUDA.")

    def active(self, distribution: ChatterboxDistribution):
        self._require_host(distribution)
        pointer = self.root / "active" / (distribution.profile_id + ".json")
        if not pointer.exists():
            return None
        try:
            value = json.loads(pointer.read_text(encoding="utf-8"))
            if (set(value) != {"schema_version", "generation", "fingerprint"}
                    or value["schema_version"] != 1
                    or value["fingerprint"] != distribution.fingerprint
                    or re.fullmatch(r"[0-9a-f]{32}", value["generation"]) is None):
                raise ValueError("Invalid active Chatterbox pointer.")
            root = self.root / "envs" / value["generation"]
            if root.is_symlink() or root.resolve().parent != (self.root / "envs").resolve():
                raise ValueError("Chatterbox pointer escaped configured storage.")
            receipt = json.loads((root / "ready.json").read_text(encoding="utf-8"))
            if set(receipt) != {"schema_version", "distribution_fingerprint", "health", "files"}:
                raise ValueError("Invalid Chatterbox installation receipt.")
            health = ChatterboxHealth.from_payload(receipt["health"])
            if (receipt["schema_version"] != 1
                    or receipt["distribution_fingerprint"] != distribution.fingerprint
                    or receipt["files"] != _inventory(root)
                    or json.loads((root / "profile.json").read_text(encoding="utf-8")) != distribution.to_payload()):
                raise ValueError("Installed Chatterbox runtime is incomplete or changed.")
            for name, data in _worker_files().items():
                if (root / name).read_bytes() != data:
                    raise ValueError("Installed Chatterbox worker differs from this application revision.")
            return InstalledChatterboxRuntime(root, health, distribution.fingerprint)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ProvisioningError("Active Chatterbox runtime failed verification; no worker may be launched.") from exc

    def install(self, distribution: ChatterboxDistribution, source: ArtifactSource):
        self._require_host(distribution)
        self.root.mkdir(parents=True, exist_ok=True)
        with _install_lock(self.root):
            existing = self.active(distribution)
            if existing is not None:
                return existing
            required = (sum(package.artifact.unpacked_size_bytes for package in distribution.packages)
                        + 512 * 1024 * 1024)
            if shutil.disk_usage(self.root).free < required:
                raise ProvisioningError("Insufficient storage for the reviewed Chatterbox runtime.")
            generation = uuid4().hex
            candidate = self.root / "envs" / generation
            candidate.mkdir(parents=True)
            (candidate / "temp").mkdir()
            with tempfile.TemporaryDirectory(prefix="provision-", dir=self.root) as temporary:
                scratch = Path(temporary)
                pins = (distribution.interpreter, *(package.artifact for package in distribution.packages),
                        distribution.native_runtime, distribution.auxiliary)
                for pin in pins:
                    path = scratch / pin.filename
                    digest, count = hashlib.sha256(), 0
                    with source.open(pin) as incoming, path.open("xb") as output:
                        while chunk := incoming.read(1024 * 1024):
                            count += len(chunk)
                            if count > pin.size_bytes:
                                raise ProvisioningError("Chatterbox artifact exceeds pinned size.")
                            digest.update(chunk)
                            output.write(chunk)
                    if count != pin.size_bytes or digest.hexdigest() != pin.sha256:
                        raise ProvisioningError("Chatterbox artifact hash/size mismatch: " + pin.filename)
                from .provisioning import _extract_zip
                _extract_zip(scratch / distribution.interpreter.filename, candidate)
                packages_root = candidate / "packages"
                for package in distribution.packages:
                    _extract_wheel(scratch / package.artifact.filename, packages_root,
                                   package.artifact.unpacked_size_bytes, package.name)
                extract_native(scratch / distribution.native_runtime.filename, candidate, scratch / "native")
                _extract_auxiliary(scratch / distribution.auxiliary.filename, candidate)
                chatterbox_info, = packages_root.glob("chatterbox_tts-*.dist-info")
                _json(chatterbox_info / "direct_url.json", {
                    "url": "https://github.com/resemble-ai/chatterbox.git",
                    "vcs_info": {"vcs": "git", "commit_id":
                                 "5de7a54aa4e5e2baadb0182dde554908b48b85c2"},
                })
                for item in distribution.payload["native_libraries"]:
                    path = packages_root.joinpath(*item["path"].split("/"))
                    if path.stat().st_size != item["size_bytes"] or _sha(path) != item["sha256"]:
                        raise ProvisioningError("Installed Torch native library differs from review.")
                (candidate / "python311._pth").write_text("python311.zip\n.\npackages\nworker\n", encoding="ascii")
                for name, data in _worker_files().items():
                    destination = candidate / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                _json(candidate / "profile.json", distribution.to_payload())
                launch = InstalledChatterboxRuntime(candidate, None, distribution.fingerprint).worker_launch()
                try:
                    try:
                        health = asyncio.run(probe_chatterbox(launch, "cuda:0", candidate / "health-work"))
                    except Exception as exc:
                        _json(candidate / "failed-health.json", {
                            "schema_version": 1, "distribution_fingerprint": distribution.fingerprint,
                            "error": (str(exc) or type(exc).__name__)[:4096],
                        })
                        raise ProvisioningError("Private Chatterbox health check failed.") from exc
                finally:
                    shutil.rmtree(candidate / "health-work", ignore_errors=True)
                _json(candidate / "ready.json", {"schema_version": 1,
                                                  "distribution_fingerprint": distribution.fingerprint,
                                                  "health": health.to_payload(), "files": _inventory(candidate)})
            active_dir = self.root / "active"
            active_dir.mkdir(exist_ok=True)
            pending = active_dir / (generation + ".tmp")
            _json(pending, {"schema_version": 1, "generation": generation,
                            "fingerprint": distribution.fingerprint})
            os.replace(pending, active_dir / (distribution.profile_id + ".json"))
            return self.active(distribution)

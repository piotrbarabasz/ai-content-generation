"""Transactional installation of the one approved private Piper CPU profile.

This blocking service belongs off the UI thread. Candidates are immutable after
health verification. Only atomic pointer publication makes a candidate active;
abandoned candidates are never inferred to be installed or reused after a crash.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import tempfile
from typing import BinaryIO, Protocol
from uuid import uuid4
import zipfile

from .native_piper import extract_native
from .piper_health import private_environment, probe_runtime
from .profile_catalog import require_approved
from .profiles import HealthStatus, ProfileError, ProfileHealth


class ProvisioningError(ProfileError):
    pass


class ArtifactSource(Protocol):
    def open(self, pin) -> BinaryIO: ...


@dataclass(frozen=True)
class DirectorySource:
    """Explicit offline cache. The installer rechecks every byte against its pin."""

    directory: Path

    def open(self, pin):
        return (Path(self.directory).resolve() / pin.filename).open("rb")


def _sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _safe_parts(name):
    parts = name.split("/")
    if (not name or "\\" in name or any(
            part in ("", ".", "..") or part.endswith((".", " "))
            or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
            or PureWindowsPath(part).is_reserved() for part in parts)):
        raise ProvisioningError("Unsafe archive or receipt path.")
    return parts


def _extract_zip(archive, destination, *, wheel=False):
    """Bounded approved-wheel subset; no setup, scripts, .pth or install hooks."""
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 10000 or sum(i.file_size for i in entries) > 512 * 1024 * 1024:
            raise ProvisioningError("Runtime archive exceeds extraction limits.")
        seen = set()
        for info in entries:
            # ZipInfo normalizes backslashes on Windows and truncates at NUL;
            # validate the original archive spelling before those transformations.
            parts = _safe_parts(info.orig_filename.rstrip("/") if info.is_dir() else info.orig_filename)
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ProvisioningError("Archive links/special files are unsupported.")
            if wheel and parts[0].endswith(".data"):
                # The reviewed Piper wheel carries its COPYING file as data.
                if len(parts) < 3 or parts[1] != "data":
                    raise ProvisioningError("Unsupported wheel installation scheme.")
                parts = ["share", parts[0], *parts[2:]]
            if wheel and parts[-1].lower().endswith((".pth", "._pth")):
                raise ProvisioningError("Wheel path hooks are unsupported.")
            key = "/".join(parts).casefold()
            if key in seen:
                raise ProvisioningError("Duplicate archive destination.")
            seen.add(key)
            target = destination.joinpath(*parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                # Exclusive creation also rejects collisions across wheel packages.
                with bundle.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)


def _worker_files():
    resource = files("app.runtime")
    names = ("protocol.py", "worker.py", "piper_worker.py")
    packed = resource.joinpath("worker_sources.json")
    if packed.is_file():
        # Standalone packagers compile .py modules; the build recipe retains the
        # same three worker sources as explicit package data for private Python.
        payload = json.loads(packed.read_text(encoding="utf-8"))
        if set(payload) != set(names) or any(not isinstance(v, str) for v in payload.values()):
            raise ProvisioningError("Invalid bundled worker sources.")
        sources = {name: value.encode("utf-8") for name, value in payload.items()}
    else:
        sources = {name: resource.joinpath(name).read_bytes() for name in names}
    return {"worker/app/__init__.py": b"", "worker/app/runtime/__init__.py": b"",
            **{f"worker/app/runtime/{name}": sources[name] for name in names}}


def _inventory(root):
    result = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        # Only this explicitly private scratch directory is mutable after activation.
        if path.is_symlink() or (path.stat().st_file_attributes & 0x400 if os.name == "nt" else False):
            raise ProvisioningError("Runtime links/reparse points are unsupported.")
        if relative.parts[0] == "temp" or relative.as_posix() == "ready.json":
            continue
        if path.is_file():
            result[relative.as_posix()] = _sha(path)
    return result


@contextmanager
def _install_lock(root):
    # OS locks are released on process death; a stale PID file cannot block restart.
    path = root / "provision.lock"
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ProvisioningError("Another runtime installation holds this storage lock.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


@dataclass(frozen=True)
class InstalledRuntime:
    directory: Path
    health: ProfileHealth

    def worker_launch(self):
        from .supervisor import WorkerLaunch
        return WorkerLaunch(self.directory / "python.exe",
                            self.directory / "worker/app/runtime/piper_worker.py",
                            private_environment(self.directory), self.directory)


class PiperProvisioner:
    def __init__(self, root: Path, host):
        self.root = Path(root).expanduser().resolve()
        self.host = host

    def active(self, profile):
        """Restart discovery verifies receipts and bytes without importing providers."""
        require_approved(profile, self.host)
        pointer = self.root / "active" / (profile.profile_id + ".json")
        if not pointer.exists():
            return None
        try:
            value = json.loads(pointer.read_text(encoding="utf-8"))
            if (set(value) != {"schema_version", "generation", "fingerprint"}
                    or type(value["schema_version"]) is not int or value["schema_version"] != 1
                    or value["fingerprint"] != profile.fingerprint
                    or not re.fullmatch(r"[0-9a-f]{32}", value["generation"])):
                raise ValueError("Invalid active runtime pointer.")
            root = self.root / "envs" / value["generation"]
            if root.is_symlink() or root.resolve().parent != (self.root / "envs").resolve():
                raise ValueError("Runtime pointer escaped configured storage.")
            receipt = json.loads((root / "ready.json").read_text(encoding="utf-8"))
            if set(receipt) != {"schema_version", "health", "files"} or receipt["schema_version"] != 1:
                raise ValueError("Invalid installation receipt.")
            health = ProfileHealth.from_payload(receipt["health"])
            health.ensure_matches(profile)
            if health.status != HealthStatus.READY or receipt["files"] != _inventory(root):
                raise ValueError("Installed runtime is incomplete or changed.")
            if json.loads((root / "profile.json").read_text(encoding="utf-8")) != profile.to_payload():
                raise ValueError("Installed profile differs from approved content.")
            for name, data in _worker_files().items():
                if (root / name).read_bytes() != data:
                    raise ValueError("Installed worker differs from this application revision.")
            return InstalledRuntime(root, health)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ProvisioningError("Active runtime failed verification; no worker may be launched.") from exc

    def install(self, profile, source: ArtifactSource):
        require_approved(profile, self.host)  # Before filesystem writes or source access.
        self.root.mkdir(parents=True, exist_ok=True)
        with _install_lock(self.root):
            existing = self.active(profile)
            if existing is not None:
                return existing
            generation = uuid4().hex
            candidate = self.root / "envs" / generation
            candidate.mkdir(parents=True)
            (candidate / "temp").mkdir()
            # Keep failed candidates for diagnosis; never activate/reuse one after restart.
            with tempfile.TemporaryDirectory(prefix="provision-", dir=self.root) as temporary:
                scratch = Path(temporary)
                pins = (profile.interpreter, *(p.artifact for p in profile.packages), profile.native_runtime.artifact)
                for pin in pins:
                    path = scratch / pin.filename
                    digest, count = hashlib.sha256(), 0
                    with source.open(pin) as incoming, path.open("xb") as output:
                        while chunk := incoming.read(1024 * 1024):
                            count += len(chunk)
                            if count > pin.size_bytes:
                                raise ProvisioningError("Runtime artifact exceeds pinned size.")
                            digest.update(chunk)
                            output.write(chunk)
                    if count != pin.size_bytes or digest.hexdigest() != pin.sha256:
                        raise ProvisioningError("Runtime artifact hash/size mismatch: " + pin.filename)
                _extract_zip(scratch / profile.interpreter.filename, candidate)
                for package in profile.packages:
                    _extract_zip(scratch / package.artifact.filename, candidate / "packages", wheel=True)
                extract_native(scratch / profile.native_runtime.artifact.filename, candidate, scratch / "native")
                (candidate / "python311._pth").write_text("python311.zip\n.\npackages\nworker\n", encoding="ascii")
                for name, data in _worker_files().items():
                    destination = candidate / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                _json(candidate / "profile.json", profile.to_payload())
                health = probe_runtime(candidate, profile)
                health.ensure_matches(profile)
                if health.status != HealthStatus.READY:
                    _json(candidate / "failed-health.json", health.to_payload())
                    raise ProvisioningError("Private runtime health check failed: " + str(health.to_payload()))
                _json(candidate / "ready.json", {"schema_version": 1, "health": health.to_payload(),
                                                "files": _inventory(candidate)})
            active_dir = self.root / "active"
            active_dir.mkdir(exist_ok=True)
            pending = active_dir / (generation + ".tmp")
            _json(pending, {"schema_version": 1, "generation": generation, "fingerprint": profile.fingerprint})
            os.replace(pending, active_dir / (profile.profile_id + ".json"))
            return self.active(profile)

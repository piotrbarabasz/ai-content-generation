"""Explicit offline generation refresh. Never imported by desktop startup."""

from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4

from .chatterbox_audio import probe_chatterbox
from .chatterbox_distribution import load_approved_chatterbox_distribution
from .chatterbox_profile import ChatterboxHealth
from .chatterbox_provisioning import ChatterboxProvisioner, InstalledChatterboxRuntime
from .profiles import ProfileError
from .provisioning import ProvisioningError, _install_lock, _inventory, _json, _safe_parts, _sha, _worker_files


class ChatterboxRefresh:
    def __init__(self, root: Path):
        self.service = ChatterboxProvisioner(root)
        self.distribution = load_approved_chatterbox_distribution()
        self.service._require_host(self.distribution)

    @property
    def pointer(self):
        return self.service.root / "active" / (self.distribution.profile_id + ".json")

    def _read_pointer(self):
        for path in (self.pointer.parent, self.pointer):
            if path.is_symlink() or (os.name == "nt" and path.stat().st_file_attributes & 0x400):
                raise ProvisioningError("Active pointer links/reparse points are unsupported.")
        raw = self.pointer.read_bytes()
        value = json.loads(raw)
        if (set(value) != {"schema_version", "generation", "fingerprint"}
                or value["schema_version"] != 1
                or value["fingerprint"] != self.distribution.fingerprint
                or not isinstance(value["generation"], str)
                or re.fullmatch(r"[0-9a-f]{32}", value["generation"]) is None):
            raise ProvisioningError("Invalid active Chatterbox pointer/fingerprint.")
        return value, raw

    def check(self):
        """Hash-only inspection: no locks, mkdir, health probe or launch creation."""
        value, _ = self._read_pointer()
        installed, receipt = self.service._verified_generation(self.distribution, value["generation"])
        workers = _worker_files()
        expected = {name: sha256(data).hexdigest() for name, data in workers.items()}
        old = {name: digest for name, digest in receipt["files"].items() if name.startswith("worker/")}
        changes = [{"path": name, "change": "added" if name not in old else
                    "removed" if name not in expected else "changed"}
                   for name in sorted(old.keys() | expected.keys()) if old.get(name) != expected.get(name)]
        return installed, receipt, workers, changes

    def verify_fresh(self, generation=None):
        """Reopen with the application's interpreter; never execute old worker code.

        Candidate verification is performed before pointer publication. Active
        verification uses active(), the same strict authorization as startup.
        -I/-B exclude environment import hooks and suppress bytecode mutations.
        """
        backend = str(Path(__file__).resolve().parents[2])
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from pathlib import Path; "
            "from app.runtime.chatterbox_refresh import ChatterboxRefresh; "
            "r=ChatterboxRefresh(Path(sys.argv[2])); "
            "r._verify_local(sys.argv[3] or None)"
        )
        try:
            subprocess.run([sys.executable, "-I", "-B", "-c", script, backend,
                            str(self.service.root), generation or ""], check=True,
                           capture_output=True, timeout=600,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProvisioningError("Fresh-process Chatterbox verification failed.") from exc

    def _verify_local(self, generation=None):
        if generation is None:
            installed = self.service.active(self.distribution)
            if installed is None:
                raise ProvisioningError("No active Chatterbox runtime.")
            return installed
        installed, receipt = self.service._verified_generation(self.distribution, generation)
        expected = {name: sha256(data).hexdigest() for name, data in _worker_files().items()}
        if {name: digest for name, digest in receipt["files"].items()
                if name.startswith("worker/")} != expected:
            raise ProvisioningError("Generation worker differs from this application revision.")
        return installed

    @staticmethod
    def _copy(source, destination, digest):
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive byte copies, never reflinks, hardlinks or copied reparse points.
        with source.open("rb") as incoming, destination.open("xb") as output:
            shutil.copyfileobj(incoming, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if _sha(destination) != digest:
            raise ProvisioningError("Runtime changed during copy: " + source.name)

    def _activate(self, generation, previous):
        # Both the candidate and its receipt are verified in a fresh process
        # before the atomic commit. An interrupted pre-commit operation cannot
        # expose a partial installation. Old generation directories are untouched.
        self.verify_fresh(generation)
        if self.pointer.read_bytes() != previous:
            raise ProvisioningError("Active pointer changed during refresh.")
        pending = self.pointer.parent / (uuid4().hex + ".tmp")
        _json(pending, {"schema_version": 1, "generation": generation,
                        "fingerprint": self.distribution.fingerprint})
        # Keep a durable exact-byte restore ready before publication, so even
        # KeyboardInterrupt during post-commit verification can restore it.
        restore = self.pointer.parent / (uuid4().hex + ".tmp")
        with restore.open("xb") as stream:
            stream.write(previous)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(pending, self.pointer)
            self.verify_fresh()
        except BaseException:
            os.replace(restore, self.pointer)
            raise
        finally:
            restore.unlink(missing_ok=True)

    def refresh(self, *, device=None):
        with _install_lock(self.service.root):
            installed, receipt, workers, changes = self.check()
            _, previous = self._read_pointer()
            if not changes:
                self.verify_fresh()
                return installed, changes
            selected_device = device or installed.health.device
            # Validate the device before creating a candidate or allocating space.
            ChatterboxHealth(installed.health.profile, selected_device, installed.health.languages,
                             installed.health.gpu_name, installed.health.total_memory)
            protected = {name: digest for name, digest in receipt["files"].items()
                         if not name.startswith("worker/")}
            for name in (*protected, *workers):
                _safe_parts(name)
            required = (sum((installed.directory / name).stat().st_size for name in protected)
                        + sum(len(data) for data in workers.values()) + 512 * 1024 * 1024)
            if shutil.disk_usage(self.service.root).free < required:
                raise ProvisioningError("Insufficient storage for a separate Chatterbox generation.")
            generation = uuid4().hex
            candidate = self.service.root / "envs" / generation
            candidate.mkdir()
            (candidate / "temp").mkdir()
            for name, digest in protected.items():
                self._copy(installed.directory / name, candidate / name, digest)
            for name, data in workers.items():
                target = candidate / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            expected = protected | {name: sha256(data).hexdigest() for name, data in workers.items()}
            if (_inventory(candidate) != expected
                    or json.loads((candidate / "profile.json").read_text(encoding="utf-8"))
                    != self.distribution.to_payload()):
                raise ProvisioningError("Candidate inventory/profile differs from verified inputs.")
            launch = InstalledChatterboxRuntime(candidate, installed.health,
                                               self.distribution.fingerprint).worker_launch()
            # All health scratch files live in the already-excluded private temp
            # directory. The probe loads packages/CUDA and checks worker identity,
            # but never loads the separately stored model weights.
            try:
                health = asyncio.run(probe_chatterbox(launch, selected_device, candidate / "temp" / "health"))
            except Exception as exc:
                raise ProvisioningError("Candidate Chatterbox health check failed; active generation unchanged.") from exc
            if health.device != selected_device or _inventory(candidate) != expected:
                raise ProvisioningError("Candidate health changed the device or protected files.")
            # Recheck original integrity as well: no concurrent changes may be
            # blessed by copying into a newly receipted installation.
            self.service._verified_generation(self.distribution, installed.directory.name)
            _json(candidate / "ready.json", {"schema_version": 1,
                  "distribution_fingerprint": self.distribution.fingerprint,
                  "health": health.to_payload(), "files": _inventory(candidate)})
            self._activate(generation, previous)
            return self.service.active(self.distribution), changes

    def rollback(self, generation):
        """Explicit activation only when the target matches this application."""
        with _install_lock(self.service.root):
            self._read_pointer()
            _, previous = self._read_pointer()
            self._verify_local(generation)
            self._activate(generation, previous)
            return self.service.active(self.distribution)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Read-only inventory and worker differences")
    modes.add_argument("--verify", action="store_true", help="Read-only fresh-process active verification")
    modes.add_argument("--rollback", metavar="GENERATION", help="Activate a valid generation matching this application")
    parser.add_argument("--device", help="Explicit cuda:N; default is the installed health device")
    args = parser.parse_args(argv)
    if args.device and (args.check or args.verify or args.rollback):
        parser.error("--device is only applicable to refresh")
    try:
        operation = ChatterboxRefresh(args.root)
        if args.verify:
            operation.verify_fresh()
            print("Active Chatterbox runtime verified in a fresh process.")
        elif args.rollback:
            print("Active generation:", operation.rollback(args.rollback).directory.name)
        else:
            installed, _, _, changes = operation.check()
            print("Current generation:", installed.directory.name)
            for change in changes:
                print(change["change"] + ": " + change["path"])
            print("Refresh required." if changes else "Current worker closure; no refresh required.")
            if not args.check:
                installed, _ = operation.refresh(device=args.device)
                print("Verified active generation:", installed.directory.name)
        return 0
    except (OSError, ValueError, TypeError, KeyError, ProfileError) as exc:
        print("Chatterbox refresh refused: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

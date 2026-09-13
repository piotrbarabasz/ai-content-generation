"""Offline smoke entry point, also compiled into a standalone Windows executable."""

import argparse
import json
from pathlib import Path
import platform
import shutil
import sys

from app.runtime.native_piper import system_directory
from app.runtime.piper_health import probe_runtime
from app.runtime.profile_catalog import load_approved_profile
from app.runtime.profiles import HealthStatus, HostCapabilities
from app.runtime.provisioning import DirectorySource, PiperProvisioner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New disposable smoke directory")
    parser.add_argument("--clean-windows", action="store_true",
                        help="Operator attests this is a clean Windows VM without Python/Piper/VC redist")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        parser.error("Windows x64 is required.")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    host = HostCapabilities("windows", "x86_64", ("cpu",))
    profile = load_approved_profile("piper-cpu-windows-x64", host)
    storage = output / "Piper \u017c\u00f3\u0142ty"
    service = PiperProvisioner(storage, host)
    first = service.install(profile, DirectorySource(args.artifacts.resolve()))
    recovered = PiperProvisioner(storage, host).active(profile)
    if recovered != first or recovered.health.status != HealthStatus.READY:
        raise RuntimeError("Restart did not recognize the same ready environment.")
    # A fresh service also reuses the profile without any source access.
    if PiperProvisioner(storage, host).install(profile, None) != first:
        raise RuntimeError("Idempotent installation changed the profile.")
    relocated = output / "Przeniesiony runtime"
    shutil.copytree(storage, relocated)
    copied = PiperProvisioner(relocated, host).active(profile)
    health = probe_runtime(copied.directory, profile)
    if health.status != HealthStatus.READY:
        raise RuntimeError(str(health.to_payload()))
    report = {"automated_pass": True, "profile_fingerprint": profile.fingerprint,
              "health": health.to_payload(), "restart_same_generation": True,
              "relocated_worker_pass": True, "worker_path_only_system32": True,
              "private_native_dll_origins_checked": True, "system_directory": str(system_directory()),
              "clean_windows_operator_attested": args.clean_windows,
              "standalone_driver": "__compiled__" in globals(),
              "host": platform.platform(), "driver": sys.executable}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

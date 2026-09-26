"""Explicit offline intake and restart verification of the private D059 runtime."""

from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4

from app.domain.dependencies import canonical_json
from app.runtime.provisioning import _install_lock


MODEL = "realesr-general-x4v3"
MODEL_SHA256 = "8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292"
PROFILE = "realesr-general-x4v3-cu124-fp32-tile128-v1"
PACKAGE_PINS = {"torch": "2.6.0+cu124", "pillow": "11.1.0", "numpy": "2.4.6"}


def _worker_bytes():
    return files("app.runtime").joinpath("upscale_worker.py").read_bytes()


def _inventory(root):
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Managed upscale installations cannot contain links.")
        if path.is_file():
            with path.open("rb") as stream:
                import hashlib
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def _python(root):
    executable = root / "python.exe"
    pth = root / "python311._pth"
    if not executable.is_file() or not pth.is_file() or pth.read_text(encoding="ascii").splitlines() != ["python311.zip", ".", "packages"]:
        raise ValueError("Private Python 3.11.9 upscaler runtime is not isolated.")
    return executable


def _packages(root):
    script = ("import importlib.metadata as m,json,sys;print(json.dumps({'python':list(sys.version_info[:3]),"
              "'packages':{k:m.version(k) for k in " + repr(tuple(PACKAGE_PINS)) + "}}))")
    result = subprocess.run([str(_python(root)), "-I", "-B", "-c", script], capture_output=True, text=True,
                            timeout=30, check=True)
    value = json.loads(result.stdout)
    if value != {"python": [3, 11, 9], "packages": PACKAGE_PINS}:
        raise ValueError("Private upscale package or Python pins differ from the profile.")
    return value


@dataclass(frozen=True)
class InstalledUpscaler:
    runtime: Path
    model: Path
    fingerprint: str
    cache: Path


class LocalUpscaleInstallation:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def active(self):
        pointer = self.root / "active.json"
        if not pointer.is_file():
            return None
        try:
            value = json.loads(pointer.read_text(encoding="utf-8"))
            generation = value["generation"]
            if set(value) != {"version", "generation", "fingerprint"} or value["version"] != 1 or re.fullmatch(r"[0-9a-f]{32}", generation) is None:
                raise ValueError("Invalid upscale activation pointer.")
            directory = self.root / "installed" / generation
            runtime, model = directory / "runtime", directory / "model.pth"
            if (directory.is_symlink() or runtime.is_symlink() or model.is_symlink()
                    or directory.resolve(strict=True).parent != (self.root / "installed").resolve(strict=True)):
                raise ValueError("Managed upscale installation escaped its root.")
            receipt = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
            with model.open("rb") as stream:
                import hashlib
                model_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            fingerprint = receipt.get("fingerprint")
            if (fingerprint != sha256(canonical_json({k: v for k, v in receipt.items() if k != "fingerprint"}).encode()).hexdigest()
                    or receipt["profile"] != PROFILE or receipt["model_sha256"] != MODEL_SHA256
                    or model_hash != MODEL_SHA256 or receipt["runtime_files"] != _inventory(runtime)
                    or receipt["packages"] != _packages(runtime)
                    or receipt["fingerprint"] != value["fingerprint"]
                    or (runtime / "upscale_worker.py").read_bytes() != _worker_bytes()):
                raise ValueError("Managed upscale runtime or model changed after activation.")
            return InstalledUpscaler(runtime, model, receipt["fingerprint"], self.root / "cache")
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            raise ValueError("Active upscaler runtime failed verification.") from exc

    def install(self, runtime_source, model_source):
        runtime_source = Path(runtime_source).resolve(strict=True)
        model_source = Path(model_source).resolve(strict=True)
        if (runtime_source == self.root or self.root in runtime_source.parents or runtime_source in self.root.parents
                or model_source == self.root or self.root in model_source.parents or model_source in self.root.parents):
            raise ValueError("Installation sources must be outside the managed upscale root.")
        self.root.mkdir(parents=True, exist_ok=True)
        with _install_lock(self.root):
            if self.active() is not None:
                raise ValueError("An upscaler runtime is already active.")
            generation = uuid4().hex
            directory = self.root / "installed" / generation
            directory.mkdir(parents=True)
            runtime, model = directory / "runtime", directory / "model.pth"
            shutil.copytree(runtime_source, runtime, symlinks=True)
            shutil.copyfile(model_source, model)
            (runtime / "upscale_worker.py").write_bytes(_worker_bytes())
            with model.open("rb") as stream:
                import hashlib
                if hashlib.file_digest(stream, "sha256").hexdigest() != MODEL_SHA256:
                    raise ValueError("Upscaler weight differs from the pinned official release.")
            receipt = {"profile": PROFILE, "model_sha256": MODEL_SHA256,
                       "runtime_files": _inventory(runtime), "packages": _packages(runtime)}
            receipt["fingerprint"] = sha256(canonical_json(receipt).encode()).hexdigest()
            (directory / "ready.json").write_text(canonical_json(receipt) + "\n", encoding="utf-8")
            pending = self.root / (generation + ".tmp")
            pending.write_text(canonical_json({"version": 1, "generation": generation,
                                               "fingerprint": receipt["fingerprint"]}) + "\n", encoding="utf-8")
            os.replace(pending, self.root / "active.json")
            return self.active()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("install", "verify"))
    parser.add_argument("root")
    parser.add_argument("runtime_source", nargs="?")
    parser.add_argument("model_source", nargs="?")
    args = parser.parse_args()
    installation = LocalUpscaleInstallation(args.root)
    installed = installation.install(args.runtime_source, args.model_source) if args.operation == "install" else installation.active()
    if installed is None:
        parser.error("No installed upscaler runtime.")
    print("verified", PROFILE, installed.fingerprint)

"""Generate the reviewed D029 profile from an exact local artifact cache.

This is a release-engineering command. It may query publisher metadata, but it
never installs or imports a wheel. The generated profile pins the bytes consumed
by the offline product provisioner.
"""

from __future__ import annotations

import argparse
import base64
import email
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.request import urlopen
import zipfile

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


SOURCE_REVISION = "5de7a54aa4e5e2baadb0182dde554908b48b85c2"
MODEL_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
TORCH_INDEX = "https://download.pytorch.org/whl/cu124"
PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"


def file_pin(path: Path) -> dict:
    return {"filename": path.name, "sha256": sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size}


def wheel_unpacked_size(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        return sum(item.file_size for item in archive.infolist())


def wheel_metadata(path: Path) -> tuple[object, dict[str, bytes]]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        # Some wheels (notably setuptools) bundle vendored dist-info trees.  The
        # wheel's own metadata is the only one rooted at the archive top level.
        metadata, = [name for name in names
                     if name.endswith(".dist-info/METADATA") and name.count("/") == 1]
        licenses = {name: archive.read(name) for name in names
                    if any(part in name.rsplit("/", 1)[-1].lower()
                           for part in ("license", "copying", "notice")) and not name.endswith("/")}
        return email.message_from_bytes(archive.read(metadata)), licenses


def publisher_artifact(name: str, version: str, filename: str) -> dict:
    if name == "chatterbox-tts":
        raise AssertionError("Chatterbox provenance must not use the unrelated PyPI artifact.")
    if name in {"torch", "torchaudio"}:
        return {"kind": "publisher-wheel",
                "url": f"{TORCH_INDEX}/{filename.replace('+', '%2B')}",
                "source_url": f"https://pytorch.org/get-started/previous-versions/#v260"}
    with urlopen(PYPI_JSON.format(name=name, version=version), timeout=30) as response:
        value = json.load(response)
    for item in value["urls"]:
        if item["filename"] == filename:
            return {"kind": "publisher-wheel", "url": item["url"],
                    "source_url": PYPI_JSON.format(name=name, version=version)}
    if name == "antlr4-python3-runtime":
        source, = [item for item in value["urls"] if item["packagetype"] == "sdist"]
        return {"kind": "reviewed-built-wheel", "url": None,
                "source_url": source["url"], "source_sha256": source["digests"]["sha256"],
                "source_size_bytes": source["size"],
                "build": "python -m pip wheel --no-deps antlr4-python3-runtime==4.9.3; normalize_wheel.py"}
    raise ValueError(f"Publisher metadata does not contain {filename}.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--piper-profile", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    wheelhouse = args.wheelhouse.resolve(strict=True)
    lock = Path(__file__).with_name("runtime-requirements.lock").read_text(encoding="utf-8")
    locked = {}
    for line in lock.splitlines():
        if not line or line.startswith("#"):
            continue
        name, version = line.split("==", 1)
        locked[canonicalize_name(name)] = version
    locked["chatterbox-tts"] = "0.1.7"

    wheels = {}
    for path in wheelhouse.glob("*.whl"):
        metadata, licenses = wheel_metadata(path)
        name, version = canonicalize_name(metadata["Name"]), metadata["Version"]
        if name in wheels:
            raise ValueError(f"Duplicate wheel for {name}.")
        wheels[name] = (path, metadata, licenses)
    if set(wheels) != set(locked):
        raise ValueError(f"Wheel closure differs from lock: missing={set(locked)-set(wheels)}, extra={set(wheels)-set(locked)}")

    environment = default_environment() | {
        "implementation_name": "cpython", "python_version": "3.11",
        "python_full_version": "3.11.9", "sys_platform": "win32",
        "platform_system": "Windows", "platform_machine": "AMD64", "extra": "",
    }
    packages = []
    for name in sorted(wheels):
        path, metadata, license_values = wheels[name]
        if Version(metadata["Version"]) != Version(locked[name]):
            raise ValueError(f"Wheel version differs from lock: {name}.")
        requirements = []
        for raw in metadata.get_all("Requires-Dist", []):
            requirement = Requirement(raw)
            if requirement.marker is not None and not requirement.marker.evaluate(environment):
                continue
            dependency = canonicalize_name(requirement.name)
            if dependency not in locked:
                raise ValueError(f"Dependency closure omits {dependency}, required by {name}.")
            if requirement.url is not None:
                raise ValueError(f"Mutable/direct dependency remains in reviewed wheel: {name} -> {raw}.")
            if requirement.specifier and not requirement.specifier.contains(locked[dependency], prereleases=True):
                raise ValueError(f"Locked {dependency} does not satisfy {name}: {raw}.")
            requirements.append({"name": dependency, "specifier": str(requirement.specifier)})
        pin = file_pin(path) | {"unpacked_size_bytes": wheel_unpacked_size(path)}
        if name != "chatterbox-tts":
            pin |= publisher_artifact(name, metadata["Version"], path.name)
        if name == "chatterbox-tts":
            source = wheelhouse / f"chatterbox-{SOURCE_REVISION}.tar.gz"
            pin |= {"kind": "reviewed-built-wheel", "url": None,
                    "source_url": f"https://github.com/resemble-ai/chatterbox/archive/{SOURCE_REVISION}.tar.gz",
                    "source_sha256": file_pin(source)["sha256"], "source_size_bytes": source.stat().st_size,
                    "source_revision": SOURCE_REVISION,
                    "build": "python -m pip wheel --no-deps <pinned-source>; normalize_wheel.py"}
        declared = ([metadata["License-Expression"]] if metadata.get("License-Expression") else
                    [metadata["License"].strip()] if metadata.get("License", "").strip() not in ("", "UNKNOWN") else
                    [item.removeprefix("License :: ") for item in metadata.get_all("Classifier", [])
                     if item.startswith("License ::")])
        license_files = [{"path": path, "size_bytes": len(value), "sha256": sha256(value).hexdigest()}
                         for path, value in sorted(license_values.items())]
        if not declared and not license_files:
            raise ValueError(f"No license declaration/file in {path.name}.")
        packages.append({"name": name, "version": metadata["Version"], "artifact": pin,
                         "dependencies": sorted(requirements, key=lambda item: item["name"]),
                         "license": {"declared": declared, "files": license_files}})

    torch_path = wheels["torch"][0]
    with zipfile.ZipFile(torch_path) as archive:
        native = []
        for item in archive.infolist():
            if item.filename.startswith("torch/lib/") and item.filename.lower().endswith(".dll"):
                value = archive.read(item.filename)
                native.append({"path": item.filename, "size_bytes": len(value), "sha256": sha256(value).hexdigest()})
    piper = json.loads(args.piper_profile.read_text(encoding="utf-8"))
    pkuseg = wheelhouse / "spacy_ontonotes.zip"
    profile = {
        "schema_version": 1,
        "profile_id": "chatterbox-v3-cu124-windows-x64",
        "profile_version": "1.0.0",
        "target": {"os": "windows", "architecture": "x86_64", "device": "cuda:0",
                   "minimum_compute_capability": "7.5", "cuda_runtime": "12.4"},
        "interpreter": piper["interpreter"],
        "packages": packages,
        "native_runtime": piper["native_runtime"],
        "native_libraries": sorted(native, key=lambda item: item["path"]),
        "auxiliary_assets": [{
            "id": "spacy-pkuseg-ontonotes-0.0.26", "filename": pkuseg.name,
            "url": "https://github.com/explosion/spacy-pkuseg/releases/download/v0.0.26/spacy_ontonotes.zip",
            "sha256": file_pin(pkuseg)["sha256"], "size_bytes": pkuseg.stat().st_size,
            "license_expression": "MIT", "license_url": "https://github.com/explosion/spacy-pkuseg/blob/v1.0.1/LICENSE"
        }],
        "model_contract": {"revision": MODEL_REVISION, "variant": "v3", "languages": ["en", "pl"],
                           "sample_rate": 24000, "bundled": False},
        "health_check": {"id": "chatterbox-v3-cu124-v1", "worker_protocol": 1},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(sha256(json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Explicit, offline intake and restart verification for one private image runtime.

This first profile adopts a separately prepared Windows Python environment and a
downloaded model snapshot. Installation never downloads packages or weights.
"""

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


PROFILE = "sd15-cu124-fp32-inference-v1"
MODEL = "stable-diffusion-v1-5/stable-diffusion-v1-5"
MODEL_REVISION = "f03de327dd89b501a01da37fc5240cf4fdba85a1"
PACKAGE_PINS = {
    "torch": "2.6.0+cu124",
    "torchvision": "0.21.0+cu124",
    "diffusers": "0.35.1",
    "transformers": "4.49.0",
    "accelerate": "1.4.0",
    "safetensors": "0.5.3",
    "huggingface-hub": "0.34.4",
    "pillow": "11.1.0",
}
MODEL_HASHES = {
    "feature_extractor/preprocessor_config.json": "2a1da83b5e1032aaeef397552ddb408dca0d8cd1dc58f61bf6abf38d6f33a0a2",
    "model_index.json": "e2f6f22e274374010aec30c79eb2d6e53fff4a36ac0225523a92cb2d41a83347",
    "safety_checker/config.json": "5dd77a06cbd9b155060bd58deb81ffd1aafc1c6d7970acac674c1128bd4edfe2",
    "safety_checker/model.fp16.safetensors": "08902f19b1cfebd7c989f152fc0507bef6898c706a91d666509383122324b511",
    "scheduler/scheduler_config.json": "699cce92eb7c122e2eb7dfdea78e6187fda76a5ed4a8e42319b85610e620e091",
    "text_encoder/config.json": "845df614cb9327ae7bbea027316246fae917827407da6df13572e41b5f93b4cc",
    "text_encoder/model.fp16.safetensors": "77795e2023adcf39bc29a884661950380bd093cf0750a966d473d1718dc9ef4e",
    "tokenizer/merges.txt": "9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a",
    "tokenizer/special_tokens_map.json": "c4864a9376a8401918425bed71fc14fc0e81f9b59ec45c1cf96cccb2df508eac",
    "tokenizer/tokenizer_config.json": "00439066fcba73de57644cf41e4e3b9f2dbb09d7f3fc2005898ba52399045882",
    "tokenizer/vocab.json": "e089ad92ba36837a0d31433e555c8f45fe601ab5c221d4f607ded32d9f7a4349",
    "unet/config.json": "78f474de6bab3d893868f37be97b636ae65c0df3073ed3256ca458ff599b5f96",
    "unet/diffusion_pytorch_model.fp16.safetensors": "c83908253f9a64d08c25fc90874c9c8aef9a329ce1ca5fb909d73b0c83d1ea21",
    "vae/config.json": "786a7d21647ddea6a04b9675c03d3cb45e90a2f3c6da5fbda2c54ade040036de",
    "vae/diffusion_pytorch_model.fp16.safetensors": "4fbcf0ebe55a0984f5a5e00d8c4521d52359af7229bb4d81890039d2aa16dd7c",
}


def _worker_bytes():
    return files("app.runtime").joinpath("local_image_worker.py").read_bytes()


def _inventory(root):
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Managed image installations cannot contain links.")
        if path.is_file():
            with path.open("rb") as stream:
                import hashlib
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def _write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical_json(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _python(root):
    value = root / "python.exe"
    if not value.is_file():
        raise ValueError("Private embedded Python 3.11.9 runtime is missing.")
    pth = root / "python311._pth"
    if not pth.is_file() or pth.read_text(encoding="ascii").splitlines() != ["python311.zip", ".", "packages"]:
        raise ValueError("Private image interpreter paths are not isolated.")
    return value


def _packages(root):
    script = ("import importlib.metadata as m,json,sys; "
              "print(json.dumps({'python':list(sys.version_info[:3]),"
              "'packages':{k:m.version(k) for k in " + repr(tuple(PACKAGE_PINS)) + "}}))")
    result = subprocess.run([str(_python(root)), "-I", "-B", "-c", script],
                            capture_output=True, text=True, timeout=30, check=True)
    value = json.loads(result.stdout)
    if value != {"python": [3, 11, 9], "packages": PACKAGE_PINS}:
        raise ValueError("Private image runtime package or Python pins differ from the profile.")
    return value


def _model_files(root):
    inventory = _inventory(root)
    if inventory != MODEL_HASHES:
        raise ValueError("Local Stable Diffusion model bytes differ from the pinned revision.")
    config = json.loads((root / "model_index.json").read_text(encoding="utf-8"))
    if config.get("_class_name") != "StableDiffusionPipeline":
        raise ValueError("Local image model is not the selected pipeline.")
    return inventory


@dataclass(frozen=True)
class InstalledLocalImage:
    runtime: Path
    model: Path
    fingerprint: str
    cache: Path


class LocalImageInstallation:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def active(self):
        pointer = self.root / "active.json"
        if not pointer.is_file():
            return None
        try:
            value = json.loads(pointer.read_text(encoding="utf-8"))
            if set(value) != {"version", "generation", "fingerprint"} or value["version"] != 1:
                raise ValueError("Invalid local image activation pointer.")
            generation = value["generation"]
            if not isinstance(generation, str) or re.fullmatch(r"[0-9a-f]{32}", generation) is None:
                raise ValueError("Invalid local image generation identity.")
            directory = self.root / "installed" / generation
            runtime, model = directory / "runtime", directory / "model"
            if (directory.is_symlink() or runtime.is_symlink() or model.is_symlink()
                    or directory.resolve(strict=True).parent != (self.root / "installed").resolve(strict=True)):
                raise ValueError("Managed local image installation escaped its root.")
            receipt = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
            if (receipt["profile"] != PROFILE or receipt["model"] != MODEL
                    or receipt["revision"] != MODEL_REVISION
                    or receipt["runtime_files"] != _inventory(runtime)
                    or receipt["model_files"] != _model_files(model)
                    or receipt["packages"] != _packages(runtime)
                    or receipt["fingerprint"] != value["fingerprint"]):
                raise ValueError("Managed image runtime or model changed after activation.")
            if (runtime / "local_image_worker.py").read_bytes() != _worker_bytes():
                raise ValueError("Managed image worker differs from this application.")
            return InstalledLocalImage(runtime, model, receipt["fingerprint"], self.root / "cache")
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            raise ValueError("Active local image runtime failed verification.") from exc

    def install(self, runtime_source, model_source):
        """Adopt explicit offline sources; candidates never become active on failure."""
        runtime_source = Path(runtime_source).resolve(strict=True)
        model_source = Path(model_source).resolve(strict=True)
        if (runtime_source == self.root or self.root in runtime_source.parents
                or runtime_source in self.root.parents
                or model_source == self.root or self.root in model_source.parents
                or model_source in self.root.parents):
            raise ValueError("Installation sources must be outside the managed image root.")
        self.root.mkdir(parents=True, exist_ok=True)
        with _install_lock(self.root):
            if self.active() is not None:
                raise ValueError("A local image runtime is already active.")
            generation = uuid4().hex
            directory = self.root / "installed" / generation
            directory.mkdir(parents=True)
            runtime, model = directory / "runtime", directory / "model"
            shutil.copytree(runtime_source, runtime, symlinks=True)
            source = model_source
            model.mkdir()
            for name in MODEL_HASHES:
                origin = source / name
                if not origin.is_file():
                    raise ValueError("Pinned local image model file is missing: " + name)
                target = model / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, target)
            (runtime / "local_image_worker.py").write_bytes(_worker_bytes())
            packages = _packages(runtime)
            receipt = {"profile": PROFILE, "model": MODEL, "revision": MODEL_REVISION,
                       "packages": packages, "runtime_files": _inventory(runtime),
                       "model_files": _model_files(model)}
            receipt["fingerprint"] = sha256(canonical_json(receipt).encode()).hexdigest()
            _write_json(directory / "ready.json", receipt)
            pending = self.root / (generation + ".tmp")
            _write_json(pending, {"version": 1, "generation": generation,
                                  "fingerprint": receipt["fingerprint"]})
            os.replace(pending, self.root / "active.json")
            return self.active()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Explicit offline local image runtime intake")
    parser.add_argument("operation", choices=("install", "verify"))
    parser.add_argument("root", type=Path)
    parser.add_argument("runtime_source", type=Path, nargs="?")
    parser.add_argument("model_source", type=Path, nargs="?")
    args = parser.parse_args(argv)
    manager = LocalImageInstallation(args.root)
    if args.operation == "install":
        if args.runtime_source is None or args.model_source is None:
            parser.error("install requires private runtime and model source directories")
        installed = manager.install(args.runtime_source, args.model_source)
    else:
        installed = manager.active()
        if installed is None:
            parser.error("no active local image runtime")
    print(f"verified {PROFILE} {installed.fingerprint}")


if __name__ == "__main__":
    main()

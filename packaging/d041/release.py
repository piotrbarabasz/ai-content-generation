"""Deterministic D041 release verification helpers (standard library only)."""

from __future__ import annotations

from hashlib import file_digest
import json
from pathlib import Path


FORBIDDEN_PARTS = {".git", ".pytest_cache", "__pycache__", "tests", "test"}
FORBIDDEN_SUFFIXES = {".env", ".key", ".onnx", ".pem", ".pfx", ".pt", ".safetensors"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def load_lock(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or not value.get("ffmpeg", {}).get("files"):
        raise ValueError("Unsupported or incomplete D041 component lock.")
    return value


def verify_ffmpeg(directory: Path, lock: dict) -> tuple[Path, ...]:
    directory = directory.resolve(strict=True)
    expected = lock["ffmpeg"]["files"]
    expected_names = {item["name"] for item in expected}
    actual_names = {path.name for path in directory.iterdir() if path.is_file() and path.name != "ffplay.exe"}
    if actual_names != expected_names:
        raise ValueError("FFmpeg directory differs from the pinned release file set.")
    result = []
    for item in expected:
        path = directory / item["name"]
        if path.is_symlink() or path.stat().st_size != item["size"] or digest(path) != item["sha256"]:
            raise ValueError(f"FFmpeg component failed its release pin: {item['name']}")
        result.append(path)
    return tuple(result)


def verify_installer_compiler(path: Path, lock: dict) -> Path:
    path = path.resolve(strict=True)
    expected = lock["installer"]
    if (path.name.casefold() != "iscc.exe" or path.is_symlink()
            or path.stat().st_size != expected["compiler_size"]
            or digest(path) != expected["compiler_sha256"]):
        raise ValueError("Inno Setup compiler differs from the pinned release tool.")
    return path


def audit_bundle(root: Path) -> tuple[dict, ...]:
    root = root.resolve(strict=True)
    inventory = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        lowered = {part.lower() for part in relative.parts}
        if lowered & FORBIDDEN_PARTS or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError(f"Forbidden release content: {relative.as_posix()}")
        inventory.append({"path": relative.as_posix(), "size": path.stat().st_size, "sha256": digest(path)})
    if not inventory:
        raise ValueError("Release bundle is empty.")
    return tuple(inventory)


def write_manifest(path: Path, *, lock: dict, inventory: tuple[dict, ...], signed: bool) -> None:
    payload = {
        "schema_version": 1,
        "application": lock["application"],
        "components": {"ffmpeg": lock["ffmpeg"], "installer": lock["installer"]},
        "signing": {"status": "signed" if signed else "unsigned", "release_eligible": signed},
        "files": list(inventory),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

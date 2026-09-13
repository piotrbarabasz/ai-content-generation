"""Immutable voice versions and atomic active pointers in configured model storage."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from .provisioning import _install_lock, _json, _safe_parts
from .voice_http import VoiceDownloadError


def voice_fingerprint(entry):
    return hashlib.sha256(json.dumps(entry.to_catalog_payload(), sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def asset_names(entry):
    """Curated model + companion + model card only; keep the original provenance."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", entry.provider_key):
        raise VoiceDownloadError("Invalid curated voice key.")
    expected = (entry.provider_key + ".onnx", entry.provider_key + ".onnx.json", "MODEL_CARD")
    paths = entry.required_files
    if (len(paths) != 3 or set(dict(entry.checksums)) != set(paths)
            or len(entry.checksums) != 3
            or tuple(_safe_parts(path)[-1] for path in paths) != expected
            or any(not re.fullmatch(r"[0-9a-f]{32}", digest) for _, digest in entry.checksums)):
        raise VoiceDownloadError("Incomplete curated Piper model/config/card integrity metadata.")
    return expected


def file_identity(path):
    md5 = hashlib.md5(usedforsecurity=False)  # Legacy publisher/catalog integrity pin.
    sha256 = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while data := stream.read(256 * 1024):
            size += len(data)
            md5.update(data)
            sha256.update(data)
    return {"md5": md5.hexdigest(), "sha256": sha256.hexdigest(), "size": size}


def validate_config(root, entry):
    config = root / (entry.provider_key + ".onnx.json")
    if config.stat().st_size > 1024 * 1024:
        raise VoiceDownloadError("Voice companion config is too large.")
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
        if (data["language"]["code"] != entry.language_id
                or data["audio"]["sample_rate"] != entry.expected_sample_rate_hz
                or type(data["audio"]["sample_rate"]) is not int):
            raise ValueError("Mismatch")
    except (ValueError, TypeError, KeyError) as exc:
        raise VoiceDownloadError("Voice config language/sample rate differs from the selected catalog voice.") from exc


def _plain(path):
    if path.is_symlink() or (os.name == "nt" and path.stat().st_file_attributes & 0x400):
        raise VoiceDownloadError("Model storage links/reparse points are unsupported.")


@dataclass(frozen=True)
class InstalledVoice:
    provider_key: str
    language_id: str
    fingerprint: str
    directory: Path

    @property
    def model_path(self):
        return self.directory / (self.provider_key + ".onnx")

    @property
    def config_path(self):
        return self.directory / (self.provider_key + ".onnx.json")


class ModelIndex:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        return _install_lock(self.root)

    def version_path(self, entry):
        return self.root / "versions" / voice_fingerprint(entry)

    def verify_version(self, entry):
        root = self.version_path(entry)
        names = asset_names(entry)
        try:
            _plain(root.parent)
            _plain(root)
            actual = {p.name for p in root.iterdir()}
            if actual != {*names, "voice.json"}:
                raise ValueError("Incomplete or unexpected voice version files.")
            for path in root.iterdir():
                _plain(path)
            receipt = json.loads((root / "voice.json").read_text(encoding="utf-8"))
            if (set(receipt) != {"schema_version", "catalog", "files"}
                    or type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1
                    or receipt["catalog"] != entry.to_catalog_payload() or set(receipt["files"]) != set(names)):
                raise ValueError("Voice receipt does not match the curated revision.")
            for name, path in zip(names, entry.required_files, strict=True):
                expected = dict(entry.checksums)[path]
                identity = file_identity(root / name)
                if identity != receipt["files"][name] or identity["md5"] != expected or identity["size"] == 0:
                    raise ValueError("Voice file integrity failure.")
            validate_config(root, entry)
            return InstalledVoice(entry.provider_key, entry.language_id, voice_fingerprint(entry), root)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise VoiceDownloadError("Installed voice failed verification; it cannot be selected.") from exc

    def installed(self, entry):
        asset_names(entry)
        pointer = self.root / "active" / (entry.provider_key + ".json")
        if not pointer.exists():
            return None
        try:
            _plain(pointer.parent)
            _plain(pointer)
            value = json.loads(pointer.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or type(value.get("schema_version")) is not int
                    or value != {"schema_version": 1, "fingerprint": voice_fingerprint(entry)}):
                raise ValueError("Invalid voice pointer.")
        except (OSError, ValueError, TypeError) as exc:
            raise VoiceDownloadError("Invalid active voice index.") from exc
        return self.verify_version(entry)

    def activate(self, entry):
        installed = self.verify_version(entry)
        active = self.root / "active"
        active.mkdir(exist_ok=True)
        _plain(active)
        pending = active / (uuid4().hex + ".tmp")
        _json(pending, {"schema_version": 1, "fingerprint": installed.fingerprint})
        os.replace(pending, active / (entry.provider_key + ".json"))
        return installed

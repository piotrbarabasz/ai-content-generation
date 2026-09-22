"""Path-free public identity and controlled private path resolution for references."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

from app.storage.paths import contained_path, storage_root
from .assembly import inspect_pcm_wav


_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ApprovedReferenceAudio:
    """Approved runtime input returned for an opaque artifact identifier."""

    runtime_path: Path
    checksum: str
    approval_label: str = "approved"
    approved: bool = True
    artifact_id: str | None = None


def cached_reference_path(root, artifact_id, checksum):
    if (not isinstance(artifact_id, str) or _OPAQUE_ID.fullmatch(artifact_id) is None
            or not isinstance(checksum, str) or _SHA256.fullmatch(checksum) is None):
        raise ValueError("Reference audio requires an opaque id and SHA-256 checksum.")
    cache = storage_root(root)
    return contained_path(cache, f"{artifact_id}/{checksum}.wav")


def resolve_cached_reference(root, artifact_id, metadata):
    if (not isinstance(metadata, dict) or set(metadata) != {"checksum", "approval_label", "approved"}
            or metadata.get("approved") is not True or not isinstance(metadata.get("approval_label"), str)
            or not metadata["approval_label"].strip()):
        raise ValueError("Reference audio lacks current approval metadata.")
    path = cached_reference_path(root, artifact_id, metadata.get("checksum"))
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError("Approved reference audio is unavailable from controlled storage.") from exc
    if sha256(payload).hexdigest() != metadata["checksum"]:
        raise ValueError("Approved reference audio bytes changed after approval.")
    inspect_pcm_wav(payload)
    return path

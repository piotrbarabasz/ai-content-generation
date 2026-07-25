"""Local filesystem artifact store."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from app.domain.types import JsonDict

from .artifact_store import ArtifactStore
from .manifest import ArtifactManifest, _coerce_metadata


def _normalize_prefix(prefix: str) -> str:
    normalized = str(prefix).strip().replace("\\", "/")
    if not normalized:
        return ""
    if normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        raise ValueError("Artifact prefix must be relative.")
    parts = PurePosixPath(normalized).parts
    if any(part == ".." for part in parts):
        raise ValueError("Artifact prefix cannot traverse directories.")
    return normalized.rstrip("/")


def _normalize_key(key: str) -> str:
    normalized = str(key).strip().replace("\\", "/")
    if not normalized:
        raise ValueError("Artifact key is required.")
    if normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        raise ValueError("Artifact key must be relative.")
    parts = PurePosixPath(normalized).parts
    if any(part in {"..", ""} for part in parts):
        raise ValueError("Artifact key cannot traverse directories.")
    return normalized


def _content_bytes(content: bytes | str) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    raise TypeError("Artifact content must be bytes or str.")


def _checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class LocalArtifactStore(ArtifactStore):
    """Persist artifacts on the local filesystem using relative storage keys."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._manifest_root = self._root / ".artifacts"
        self._manifest_root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def save_artifact(
        self,
        name: str,
        content: bytes | str,
        metadata: JsonDict | None = None,
    ) -> ArtifactManifest:
        metadata_dict = _coerce_metadata(metadata)
        payload = _content_bytes(content)
        manifest = ArtifactManifest.create(
            name=name,
            artifact_type=str(
                metadata_dict.get("artifact_type")
                or metadata_dict.get("artifactType")
                or Path(name).suffix.lstrip(".")
                or Path(name).name
            ),
            workflow_run_id=str(
                metadata_dict.get("workflow_run_id")
                or metadata_dict.get("workflowRunId")
                or ""
            ),
            module_name=str(metadata_dict.get("module_name") or metadata_dict.get("moduleName") or ""),
            metadata={
                key: value
                for key, value in metadata_dict.items()
                if key not in {
                    "artifact_type",
                    "artifactType",
                    "module_name",
                    "moduleName",
                    "workflow_run_id",
                    "workflowRunId",
                }
            },
            artifact_version=str(
                metadata_dict.get("artifact_version")
                or metadata_dict.get("artifactVersion")
                or metadata_dict.get("version")
                or "1"
            ),
            checksum=_checksum(payload),
            size_bytes=len(payload),
        )

        artifact_path = self._root / Path(_normalize_key(manifest.storage_key))
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(payload)
        self._write_manifest(manifest)
        return manifest

    def read_artifact(self, key: str) -> bytes:
        artifact_path = self._root / Path(_normalize_key(key))
        if not artifact_path.exists():
            raise FileNotFoundError(key)
        return artifact_path.read_bytes()

    def list_artifacts(self, prefix: str = "") -> tuple[ArtifactManifest, ...]:
        normalized_prefix = _normalize_prefix(prefix)
        manifests: list[ArtifactManifest] = []
        for path in sorted(self._manifest_root.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            manifest = ArtifactManifest.from_payload(data)
            if normalized_prefix and not manifest.storage_key.startswith(normalized_prefix):
                continue
            manifests.append(manifest)
        return tuple(sorted(manifests, key=lambda manifest: manifest.storage_key))

    def _write_manifest(self, manifest: ArtifactManifest) -> None:
        manifest_path = self._manifest_root / f"{manifest.artifact_id}.json"
        manifest_path.write_text(
            json.dumps(manifest.to_payload(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

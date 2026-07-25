"""Deterministic in-memory storage provider."""

from __future__ import annotations

import hashlib

from app.domain.enums import ProviderType
from app.domain.types import JsonDict
from app.storage.manifest import ArtifactManifest

from .interfaces import StorageProvider, _coerce_json_dict, _stable_signature


class MockStorageProvider(StorageProvider):
    provider_type = ProviderType.STORAGE

    def __init__(self, provider_name: str = "mock") -> None:
        self.provider_name = provider_name
        self._artifacts: dict[str, bytes] = {}
        self._manifests: dict[str, ArtifactManifest] = {}

    def save_artifact(
        self,
        name: str,
        content: bytes | str,
        metadata: JsonDict | None = None,
    ) -> ArtifactManifest:
        metadata_dict = _coerce_json_dict(metadata)
        payload = content if isinstance(content, bytes) else content.encode("utf-8")
        signature = _stable_signature(
            {
                "provider": self.provider_name,
                "name": name,
                "payload": hashlib.sha256(payload).hexdigest(),
                "metadata": metadata_dict,
            }
        )
        artifact_id = f"artifact_{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:12]}"
        manifest = ArtifactManifest.create(
            name=name,
            artifact_type=str(
                metadata_dict.get("artifact_type")
                or metadata_dict.get("artifactType")
                or name.rsplit(".", 1)[-1]
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
                if key
                not in {
                    "artifact_type",
                    "artifactType",
                    "workflow_run_id",
                    "workflowRunId",
                    "module_name",
                    "moduleName",
                }
            },
            artifact_version=str(
                metadata_dict.get("artifact_version")
                or metadata_dict.get("artifactVersion")
                or "1"
            ),
            checksum=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            artifact_id=artifact_id,
        )
        self._artifacts[manifest.storage_key] = payload
        self._manifests[manifest.storage_key] = manifest
        return manifest

    def read_artifact(self, key: str) -> bytes:
        try:
            return self._artifacts[key]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc

    def list_artifacts(self, prefix: str = "") -> tuple[ArtifactManifest, ...]:
        normalized_prefix = prefix.strip().replace("\\", "/")
        manifests = tuple(
            sorted(
                (
                    manifest
                    for key, manifest in self._manifests.items()
                    if not normalized_prefix or key.startswith(normalized_prefix)
                ),
                key=lambda manifest: manifest.storage_key,
            )
        )
        return manifests

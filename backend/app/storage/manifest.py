"""Artifact manifest format used by the local artifact store."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
import re

from app.domain.base import new_id
from app.domain.types import JsonDict


_CANONICAL_METADATA_KEYS = {
    "artifact_type",
    "artifactType",
    "artifact_version",
    "artifactVersion",
    "module_name",
    "moduleName",
    "storage_key",
    "storageKey",
    "workflow_run_id",
    "workflowRunId",
}


def _coerce_metadata(metadata: JsonDict | None) -> JsonDict:
    return dict(metadata or {})


def _coerce_text(value: object, *, field_name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"ArtifactManifest {field_name} is required.")
    return text


def _coerce_int(value: object, *, field_name: str) -> int:
    number = int(value)
    if number < 0:
        raise ValueError(f"ArtifactManifest {field_name} cannot be negative.")
    return number


def _coerce_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("ArtifactManifest created_at must be a datetime or ISO formatted string.")


def _safe_segment(value: str, *, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = cleaned.strip("._-")
    return cleaned or fallback


def _build_storage_key(
    *,
    artifact_id: str,
    name: str,
    workflow_run_id: str,
    module_name: str,
) -> str:
    filename = PurePosixPath(name).name
    stem = _safe_segment(PurePosixPath(filename).stem, fallback="artifact")
    suffix = PurePosixPath(filename).suffix
    key_parts = [
        _safe_segment(workflow_run_id, fallback="workflow"),
        _safe_segment(module_name, fallback="module"),
        f"{artifact_id}-{stem}{suffix}",
    ]
    return "/".join(part for part in key_parts if part)


@dataclass(slots=True)
class ArtifactManifest:
    """Serializable record describing one stored artifact."""

    artifact_id: str = ""
    name: str = ""
    artifact_type: str = ""
    workflow_run_id: str = ""
    module_name: str = ""
    storage_key: str = ""
    artifact_version: str = "1"
    checksum: str = ""
    size_bytes: int = 0
    metadata: JsonDict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = 1

    @classmethod
    def create(
        cls,
        *,
        name: str,
        artifact_type: str,
        workflow_run_id: str = "",
        module_name: str = "",
        metadata: JsonDict | None = None,
        artifact_version: str = "1",
        checksum: str = "",
        size_bytes: int = 0,
        storage_key: str = "",
        artifact_id: str = "",
        created_at: datetime | str | None = None,
        schema_version: int = 1,
    ) -> "ArtifactManifest":
        normalized_name = _coerce_text(name, field_name="name")
        normalized_artifact_type = _coerce_text(artifact_type, field_name="artifact_type")
        normalized_workflow_run_id = str(workflow_run_id).strip()
        normalized_module_name = str(module_name).strip()
        normalized_artifact_version = _coerce_text(artifact_version, field_name="artifact_version")
        normalized_metadata = _coerce_metadata(metadata)
        normalized_storage_key = str(storage_key).strip()
        normalized_artifact_id = str(artifact_id).strip() or new_id("artifact")
        normalized_created_at = (
            datetime.now(UTC) if created_at is None else _coerce_datetime(created_at)
        )

        if not normalized_storage_key:
            normalized_storage_key = _build_storage_key(
                artifact_id=normalized_artifact_id,
                name=normalized_name,
                workflow_run_id=normalized_workflow_run_id,
                module_name=normalized_module_name,
            )

        return cls(
            artifact_id=normalized_artifact_id,
            name=normalized_name,
            artifact_type=normalized_artifact_type,
            workflow_run_id=normalized_workflow_run_id,
            module_name=normalized_module_name,
            storage_key=normalized_storage_key,
            artifact_version=normalized_artifact_version,
            checksum=str(checksum),
            size_bytes=_coerce_int(size_bytes, field_name="size_bytes"),
            metadata=normalized_metadata,
            created_at=normalized_created_at,
            schema_version=_coerce_int(schema_version, field_name="schema_version"),
        )

    def to_payload(self) -> JsonDict:
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "name": self.name,
            "artifact_type": self.artifact_type,
            "workflow_run_id": self.workflow_run_id,
            "module_name": self.module_name,
            "storage_key": self.storage_key,
            "artifact_version": self.artifact_version,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: JsonDict) -> "ArtifactManifest":
        metadata = _coerce_metadata(payload.get("metadata") if isinstance(payload, dict) else {})
        artifact_type = payload.get("artifact_type") or payload.get("artifactType") or ""
        workflow_run_id = payload.get("workflow_run_id") or payload.get("workflowRunId") or ""
        module_name = payload.get("module_name") or payload.get("moduleName") or ""
        storage_key = payload.get("storage_key") or payload.get("storageKey") or ""
        artifact_version = payload.get("artifact_version") or payload.get("artifactVersion") or "1"
        artifact_id = payload.get("artifact_id") or payload.get("artifactId") or ""
        created_at = payload.get("created_at") or payload.get("createdAt")

        schema_version = payload.get("schema_version") or payload.get("schemaVersion") or 1
        checksum = payload.get("checksum") or ""
        size_bytes = payload.get("size_bytes") or payload.get("sizeBytes") or 0
        name = payload.get("name") or ""

        return cls.create(
            name=name,
            artifact_type=artifact_type,
            workflow_run_id=workflow_run_id,
            module_name=module_name,
            metadata=metadata,
            artifact_version=artifact_version,
            checksum=checksum,
            size_bytes=size_bytes,
            storage_key=storage_key,
            artifact_id=artifact_id,
            created_at=created_at,
            schema_version=schema_version,
        )

    @property
    def metadata_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.metadata))

    def filtered_metadata(self) -> JsonDict:
        return {
            key: value
            for key, value in self.metadata.items()
            if key not in _CANONICAL_METADATA_KEYS
        }

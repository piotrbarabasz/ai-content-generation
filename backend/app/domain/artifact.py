"""Artifact domain model."""

from dataclasses import dataclass, field

from app.domain.base import DomainEntity, DomainValidationError, new_id
from app.domain.types import JsonDict


@dataclass(slots=True)
class Artifact(DomainEntity):
    workflow_run_id: str = ""
    module_name: str = ""
    artifact_type: str = ""
    storage_key: str = ""
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        workflow_run_id: str,
        module_name: str,
        artifact_type: str,
        storage_key: str,
        metadata: JsonDict | None = None,
    ) -> "Artifact":
        if not workflow_run_id.strip():
            raise DomainValidationError("Artifact workflow_run_id is required.")
        if not module_name.strip():
            raise DomainValidationError("Artifact module_name is required.")
        if not artifact_type.strip():
            raise DomainValidationError("Artifact artifact_type is required.")
        if not storage_key.strip():
            raise DomainValidationError("Artifact storage_key is required.")

        return cls(
            id=new_id("artifact"),
            workflow_run_id=workflow_run_id,
            module_name=module_name,
            artifact_type=artifact_type,
            storage_key=storage_key,
            metadata=metadata or {},
        )

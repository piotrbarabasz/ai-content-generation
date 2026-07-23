"""Export bundle domain model."""

from dataclasses import dataclass, field

from app.domain.base import DomainEntity, DomainValidationError, new_id
from app.domain.types import JsonDict


def _coerce_str_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if values is None:
        return []
    return [str(value) for value in values]


@dataclass(slots=True)
class ExportBundle(DomainEntity):
    workflow_run_id: str = ""
    manifest_path: str = ""
    required_files: list[str] = field(default_factory=list)
    included_artifacts: list[str] = field(default_factory=list)
    missing_optional_artifacts: list[str] = field(default_factory=list)
    approval_summary: JsonDict = field(default_factory=dict)
    provider_summary: JsonDict = field(default_factory=dict)
    status: str = "pending"

    @classmethod
    def create(
        cls,
        *,
        workflow_run_id: str,
        manifest_path: str,
        required_files: list[str] | tuple[str, ...] | None = None,
        included_artifacts: list[str] | tuple[str, ...] | None = None,
        missing_optional_artifacts: list[str] | tuple[str, ...] | None = None,
        approval_summary: JsonDict | None = None,
        provider_summary: JsonDict | None = None,
        status: str = "pending",
    ) -> "ExportBundle":
        if not workflow_run_id.strip():
            raise DomainValidationError("ExportBundle workflow_run_id is required.")
        if not manifest_path.strip():
            raise DomainValidationError("ExportBundle manifest_path is required.")
        if not status.strip():
            raise DomainValidationError("ExportBundle status is required.")

        return cls(
            id=new_id("export_bundle"),
            workflow_run_id=workflow_run_id,
            manifest_path=manifest_path,
            required_files=_coerce_str_list(required_files),
            included_artifacts=_coerce_str_list(included_artifacts),
            missing_optional_artifacts=_coerce_str_list(missing_optional_artifacts),
            approval_summary=approval_summary or {},
            provider_summary=provider_summary or {},
            status=status,
        )

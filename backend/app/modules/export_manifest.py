"""Export bundle manifest schema."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar

from app.domain.base import DomainValidationError
from app.domain.enums import ContentGenre, ContentType, DurationProfile, WorkflowPreset
from app.domain.types import JsonDict


def _coerce_text(value: object | None, *, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise DomainValidationError(f"ExportBundleManifest {field_name} is required.")
    return text


def _coerce_str_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if values is None:
        return []
    return [str(value) for value in values]


def _coerce_mapping(value: Mapping[str, object] | None) -> JsonDict:
    if value is None:
        return {}
    return dict(value)


def _coerce_datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise DomainValidationError(
        "ExportBundleManifest created_at must be a datetime or ISO formatted string."
    )


@dataclass(slots=True)
class ExportBundleManifest:
    """Serializable manifest for an export bundle."""

    REQUIRED_FILES: ClassVar[tuple[str, ...]] = (
        "manifest.json",
        "workflow_config.json",
        "workflow_run.json",
    )

    schema_version: int = 1
    export_id: str = ""
    project_id: str = ""
    workflow_run_id: str = ""
    workflow_preset: WorkflowPreset = WorkflowPreset.SHORT_VIDEO
    content_type: ContentType = ContentType.SHORT_VIDEO
    content_genre: ContentGenre = ContentGenre.NEWS
    duration_profile: DurationProfile = DurationProfile.SIXTY_SECONDS
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    included_artifacts: list[str] = field(default_factory=list)
    missing_optional_artifacts: list[str] = field(default_factory=list)
    module_results: JsonDict = field(default_factory=dict)
    approval_summary: JsonDict = field(default_factory=dict)
    provider_summary: JsonDict = field(default_factory=dict)
    artifact_references: JsonDict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        export_id: str,
        project_id: str,
        workflow_run_id: str,
        workflow_preset: WorkflowPreset | str,
        content_type: ContentType | str,
        content_genre: ContentGenre | str,
        duration_profile: DurationProfile | str,
        created_at: datetime | str | None = None,
        included_artifacts: list[str] | tuple[str, ...] | None = None,
        missing_optional_artifacts: list[str] | tuple[str, ...] | None = None,
        module_results: Mapping[str, object] | None = None,
        approval_summary: Mapping[str, object] | None = None,
        provider_summary: Mapping[str, object] | None = None,
        artifact_references: Mapping[str, object] | None = None,
        schema_version: int = 1,
    ) -> "ExportBundleManifest":
        try:
            manifest = cls(
                schema_version=schema_version,
                export_id=_coerce_text(export_id, field_name="export_id"),
                project_id=_coerce_text(project_id, field_name="project_id"),
                workflow_run_id=_coerce_text(workflow_run_id, field_name="workflow_run_id"),
                workflow_preset=WorkflowPreset(workflow_preset),
                content_type=ContentType(content_type),
                content_genre=ContentGenre(content_genre),
                duration_profile=DurationProfile(duration_profile),
                created_at=_coerce_datetime(created_at),
                included_artifacts=_coerce_str_list(included_artifacts),
                missing_optional_artifacts=_coerce_str_list(missing_optional_artifacts),
                module_results=_coerce_mapping(module_results),
                approval_summary=_coerce_mapping(approval_summary),
                provider_summary=_coerce_mapping(provider_summary),
                artifact_references=_coerce_mapping(artifact_references),
            )
        except ValueError as exc:
            raise DomainValidationError(str(exc)) from exc

        if manifest.schema_version < 1:
            raise DomainValidationError("ExportBundleManifest schema_version must be greater than zero.")

        return manifest

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "ExportBundleManifest":
        aliases = {
            "schemaVersion": "schema_version",
            "exportId": "export_id",
            "projectId": "project_id",
            "workflowRunId": "workflow_run_id",
            "workflowPreset": "workflow_preset",
            "contentType": "content_type",
            "contentGenre": "content_genre",
            "durationProfile": "duration_profile",
            "createdAt": "created_at",
            "includedArtifacts": "included_artifacts",
            "missingOptionalArtifacts": "missing_optional_artifacts",
            "moduleResults": "module_results",
            "approvalSummary": "approval_summary",
            "providerSummary": "provider_summary",
            "artifactReferences": "artifact_references",
        }
        normalized = {aliases.get(key, key): value for key, value in payload.items()}
        return cls.create(**normalized)

    def to_payload(self) -> JsonDict:
        return {
            "schemaVersion": self.schema_version,
            "exportId": self.export_id,
            "projectId": self.project_id,
            "workflowRunId": self.workflow_run_id,
            "workflowPreset": self.workflow_preset.value,
            "contentType": self.content_type.value,
            "contentGenre": self.content_genre.value,
            "durationProfile": self.duration_profile.value,
            "createdAt": self.created_at.isoformat(),
            "includedArtifacts": list(self.included_artifacts),
            "missingOptionalArtifacts": list(self.missing_optional_artifacts),
            "moduleResults": dict(self.module_results),
            "approvalSummary": dict(self.approval_summary),
            "providerSummary": dict(self.provider_summary),
            "artifactReferences": dict(self.artifact_references),
        }


__all__ = ["ExportBundleManifest"]

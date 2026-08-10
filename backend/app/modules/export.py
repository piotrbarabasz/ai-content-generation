"""Deterministic export packaging module."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256

from app.domain.enums import ContentGenre, ContentType, DurationProfile, WorkflowPreset
from app.domain.export_config import ExportConfig
from app.domain.export_bundle import ExportBundle
from app.domain.platform_handoff import PlatformHandoffBuilder
from app.domain.types import JsonDict
from app.modules.export_manifest import ExportBundleManifest
from app.storage.artifact_store import ArtifactStore
from app.workflow.execution import ModuleExecutionContext, ModuleResult
from app.workflow.module import ModuleDefinition


_OPTIONAL_ARTIFACTS_BY_PRESET: dict[str, dict[str, tuple[str, ...]]] = {
    WorkflowPreset.SHORT_VIDEO.value: {
        "voiceover": ("voiceover.wav", "speech_timeline.json"),
        "captions": ("captions.json",),
    },
    WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER.value: {
        "research": ("research.json",),
        "dossier": ("dossier.json",),
        "voiceover": ("voiceover.wav", "speech_timeline.json"),
        "videoRendering": ("render.mp4",),
    },
}


def _pick(mapping: Mapping[str, object], *names: str, default: object | None = None) -> object | None:
    for name in names:
        if name not in mapping:
            continue
        value = mapping[name]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return default


def _optional_text(value: object | None) -> str:
    return " ".join(str(value).strip().split()) if value is not None else ""


def _coerce_mapping(value: object | None) -> JsonDict:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _json_default(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _stable_export_id(
    *,
    workflow_run_id: str,
    project_id: str,
    workflow_preset: str,
    content_type: str,
    content_genre: str,
    duration_profile: str,
    included_artifacts: list[str],
) -> str:
    signature = sha256(
        "|".join(
            (
                workflow_run_id,
                project_id,
                workflow_preset,
                content_type,
                content_genre,
                duration_profile,
                ",".join(included_artifacts),
            )
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"export_{signature}"


def _ordered_module_results(context: ModuleExecutionContext) -> tuple[tuple[str, ModuleResult], ...]:
    ordered_names: list[str] = []
    for name in (*context.enabled_modules, *context.disabled_modules, *context.module_results):
        if name in context.module_results and name not in ordered_names:
            ordered_names.append(name)
    for name in context.module_results:
        if name not in ordered_names:
            ordered_names.append(name)
    return tuple((name, context.module_results[name]) for name in ordered_names)


def _module_result_payload(result: ModuleResult) -> JsonDict:
    return {
        "status": result.status,
        "outputArtifactIds": list(result.output_artifact_ids),
        "usageMetadata": dict(result.usage_metadata),
        "errorMessage": result.error_message,
        "skippedReason": result.skipped_reason,
    }


def _artifact_name_from_payload(payload: Mapping[str, object]) -> str:
    name = _optional_text(payload.get("name"))
    if name:
        return name
    storage_key = _optional_text(payload.get("storage_key") or payload.get("storageKey"))
    if storage_key:
        return storage_key.rsplit("/", 1)[-1]
    return ""


def _artifact_payloads_from_result(result: ModuleResult) -> dict[str, JsonDict]:
    payloads: dict[str, JsonDict] = {}
    output = result.output if isinstance(result.output, Mapping) else {}
    for key, value in output.items():
        if key != "artifact" and not key.endswith("_artifact"):
            continue
        if not isinstance(value, Mapping):
            continue
        payload = dict(value)
        artifact_name = _artifact_name_from_payload(payload)
        if not artifact_name:
            artifact_name = next(iter(result.output_artifact_ids), "")
        if not artifact_name:
            continue
        payload.setdefault("module_name", result.module_name)
        payload.setdefault("status", result.status)
        payload.setdefault("artifact_name", artifact_name)
        payloads[artifact_name] = payload

    for artifact_name in result.output_artifact_ids:
        payloads.setdefault(
            artifact_name,
            {
                "artifact_name": artifact_name,
                "module_name": result.module_name,
                "status": result.status,
            },
        )

    return payloads


def _coerce_optional_artifacts(
    *,
    workflow_preset: str,
    enabled_modules: tuple[str, ...],
    disabled_modules: tuple[str, ...],
    included_artifacts: list[str],
) -> list[str]:
    optional_artifacts = _OPTIONAL_ARTIFACTS_BY_PRESET.get(workflow_preset, {})
    missing: list[str] = []
    present = set(included_artifacts)
    for module_name in (*enabled_modules, *disabled_modules):
        artifact_names = optional_artifacts.get(module_name)
        if artifact_names is None:
            continue
        for artifact_name in artifact_names:
            if artifact_name not in present and artifact_name not in missing:
                missing.append(artifact_name)
    return missing


def _summary_from_workflow_run(workflow_run: JsonDict) -> JsonDict:
    if not workflow_run:
        return {}
    summary: JsonDict = {}
    status = _optional_text(workflow_run.get("status"))
    if status:
        summary["workflowRunStatus"] = status
    stage = _optional_text(workflow_run.get("current_stage") or workflow_run.get("currentStage"))
    if stage:
        summary["currentStage"] = stage
    return summary


def _summary_from_workflow_config(workflow_config: JsonDict) -> JsonDict:
    if not workflow_config:
        return {}
    summary: JsonDict = {}
    preset = _optional_text(workflow_config.get("workflow_preset") or workflow_config.get("workflowPreset"))
    if preset:
        summary["workflowPreset"] = preset
    enabled_modules = workflow_config.get("enabled_modules") or workflow_config.get("enabledModules")
    if isinstance(enabled_modules, list):
        summary["enabledModules"] = [str(value) for value in enabled_modules]
    return summary


class ExportModule:
    """Package workflow artifacts and metadata into an export bundle manifest."""

    definition = ModuleDefinition(
        name="export",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "projectId": {"type": "string"},
                "workflow_run_id": {"type": "string"},
                "workflowRunId": {"type": "string"},
                "workflow_preset": {"type": "string"},
                "workflowPreset": {"type": "string"},
                "content_type": {"type": "string"},
                "contentType": {"type": "string"},
                "content_genre": {"type": "string"},
                "contentGenre": {"type": "string"},
                "duration_profile": {"type": "string"},
                "durationProfile": {"type": "string"},
                "export_id": {"type": "string"},
                "exportId": {"type": "string"},
                "created_at": {"type": "string"},
                "createdAt": {"type": "string"},
                "workflow_config": {"type": "object"},
                "workflowConfig": {"type": "object"},
                "workflow_run": {"type": "object"},
                "workflowRun": {"type": "object"},
                "approval_summary": {"type": "object"},
                "approvalSummary": {"type": "object"},
                "provider_summary": {"type": "object"},
                "providerSummary": {"type": "object"},
                "publishing_metadata": {"type": "object"},
                "publishingMetadata": {"type": "object"},
                "source_language": {"type": "string"},
                "sourceLanguage": {"type": "string"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "manifest": {"type": "object"},
                "artifact": {"type": "object"},
                "export_bundle": {"type": "object"},
                "workflow_snapshot": {"type": "object"},
                "platform_handoff": {"type": "object"},
            },
        },
        config_schema={
            "type": "object",
            "properties": {
                "manifest_name": {"type": "string"},
            },
        },
        enabled_by_default=True,
        disabled_behavior="fail",
        retry_limit=1,
        artifact_outputs=("manifest.json",),
        error_behavior="request_missing_fields",
    )

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        manifest_name: str = "manifest.json",
        platform_handoff_builder: PlatformHandoffBuilder | None = None,
        handoff_name: str = "platform_handoff.json",
    ) -> None:
        self._artifact_store = artifact_store
        self._manifest_name = _optional_text(manifest_name) or "manifest.json"
        self._platform_handoff_builder = platform_handoff_builder
        self._handoff_name = _optional_text(handoff_name) or "platform_handoff.json"

    def execute(self, context: ModuleExecutionContext) -> ModuleResult:
        inputs = dict(context.inputs)
        workflow_run_id = _optional_text(_pick(inputs, "workflow_run_id", "workflowRunId")) or context.workflow_run_id
        project_id = _optional_text(_pick(inputs, "project_id", "projectId"))
        workflow_preset = _optional_text(
            _pick(inputs, "workflow_preset", "workflowPreset")
        )
        content_type = _optional_text(_pick(inputs, "content_type", "contentType"))
        content_genre = _optional_text(_pick(inputs, "content_genre", "contentGenre"))
        duration_profile = _optional_text(
            _pick(inputs, "duration_profile", "durationProfile")
        )
        created_at = _pick(inputs, "created_at", "createdAt")

        if not project_id:
            raise ValueError("ExportModule project_id is required.")
        if not workflow_preset:
            raise ValueError("ExportModule workflow_preset is required.")
        if not content_type:
            raise ValueError("ExportModule content_type is required.")
        if not content_genre:
            raise ValueError("ExportModule content_genre is required.")
        if not duration_profile:
            raise ValueError("ExportModule duration_profile is required.")

        workflow_config = _coerce_mapping(_pick(inputs, "workflow_config", "workflowConfig"))
        workflow_run = _coerce_mapping(_pick(inputs, "workflow_run", "workflowRun"))
        approval_summary = _coerce_mapping(
            _pick(inputs, "approval_summary", "approvalSummary")
        ) or _summary_from_workflow_run(workflow_run)
        provider_summary = _coerce_mapping(
            _pick(inputs, "provider_summary", "providerSummary")
        ) or _summary_from_workflow_config(workflow_config)

        module_results_payload: JsonDict = {}
        included_artifacts: list[str] = list(ExportBundleManifest.REQUIRED_FILES)
        artifact_references: JsonDict = {}

        if workflow_config:
            artifact_references["workflow_config.json"] = workflow_config
        if workflow_run:
            artifact_references["workflow_run.json"] = workflow_run

        for module_name, module_result in _ordered_module_results(context):
            module_results_payload[module_name] = _module_result_payload(module_result)
            for artifact_name, artifact_payload in _artifact_payloads_from_result(module_result).items():
                if artifact_name not in included_artifacts:
                    included_artifacts.append(artifact_name)
                artifact_references[artifact_name] = artifact_payload

        missing_optional_artifacts = _coerce_optional_artifacts(
            workflow_preset=workflow_preset,
            enabled_modules=context.enabled_modules,
            disabled_modules=context.disabled_modules,
            included_artifacts=included_artifacts,
        )

        export_id = _optional_text(_pick(inputs, "export_id", "exportId"))
        if not export_id:
            export_id = _stable_export_id(
                workflow_run_id=workflow_run_id,
                project_id=project_id,
                workflow_preset=workflow_preset,
                content_type=content_type,
                content_genre=content_genre,
                duration_profile=duration_profile,
                included_artifacts=included_artifacts,
            )

        manifest = ExportBundleManifest.create(
            export_id=export_id,
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            workflow_preset=WorkflowPreset(workflow_preset),
            content_type=ContentType(content_type),
            content_genre=ContentGenre(content_genre),
            duration_profile=DurationProfile(duration_profile),
            created_at=created_at if created_at is not None else datetime.now(UTC),
            included_artifacts=included_artifacts,
            missing_optional_artifacts=missing_optional_artifacts,
            module_results=module_results_payload,
            approval_summary=approval_summary,
            provider_summary=provider_summary,
            artifact_references=artifact_references,
        )

        manifest_payload = manifest.to_payload()
        platform_handoff_payload: JsonDict = {}
        if self._platform_handoff_builder is not None:
            source_language = _optional_text(
                _pick(inputs, "source_language", "sourceLanguage")
            ) or _optional_text(workflow_config.get("language"))
            if not source_language:
                raise ValueError("ExportModule source_language is required for a platform handoff.")
            publishing_metadata = _coerce_mapping(
                _pick(inputs, "publishing_metadata", "publishingMetadata")
            )
            export_config = ExportConfig.from_mapping(
                workflow_config.get("exportConfig", workflow_config.get("export_config", {})),
                source_language=source_language,
            )
            platform_handoff = self._platform_handoff_builder.build(
                manifest_payload,
                source_language=source_language,
                metadata=publishing_metadata,
                export_config=export_config,
            )
            platform_handoff_payload = platform_handoff.to_payload()
            handoff_artifact = self._artifact_store.save_artifact(
                self._handoff_name,
                json.dumps(platform_handoff_payload, indent=2, sort_keys=True) + "\n",
                metadata={
                    "workflow_run_id": workflow_run_id,
                    "module_name": self.definition.name,
                    "artifact_type": "platform_handoff",
                    "export_id": export_id,
                    "platform": platform_handoff.platform,
                },
            )
            manifest_payload["artifactReferences"][self._handoff_name] = handoff_artifact.to_payload()
            if self._handoff_name not in manifest_payload["includedArtifacts"]:
                manifest_payload["includedArtifacts"].append(self._handoff_name)

        manifest_artifact = self._artifact_store.save_artifact(
            self._manifest_name,
            json.dumps(manifest_payload, indent=2, sort_keys=True, default=_json_default),
            metadata={
                "workflow_run_id": workflow_run_id,
                "module_name": self.definition.name,
                "artifact_type": "manifest",
                "export_id": export_id,
                "project_id": project_id,
                "workflow_preset": workflow_preset,
            },
        )
        manifest_payload["artifactReferences"]["manifest.json"] = manifest_artifact.to_payload()

        export_bundle = ExportBundle.create(
            workflow_run_id=workflow_run_id,
            manifest_path=manifest_artifact.storage_key,
            manifest=manifest_payload,
            required_files=list(ExportBundleManifest.REQUIRED_FILES),
            included_artifacts=manifest_payload["includedArtifacts"],
            missing_optional_artifacts=missing_optional_artifacts,
            approval_summary=approval_summary,
            provider_summary=provider_summary,
            status="created",
        )

        return ModuleResult(
            module_name=self.definition.name,
            status="completed",
            output_artifact_ids=self.definition.artifact_outputs,
            output={
                "manifest": manifest_payload,
                "artifact": manifest_artifact.to_payload(),
                "export_bundle": asdict(export_bundle),
                "workflow_snapshot": {
                    "workflow_run_id": context.workflow_run_id,
                    "workflow_config_id": context.workflow_config_id,
                    "module_name": context.module_name,
                    "enabled_modules": list(context.enabled_modules),
                    "disabled_modules": list(context.disabled_modules),
                },
                "platform_handoff": platform_handoff_payload,
            },
        )


__all__ = ["ExportModule"]

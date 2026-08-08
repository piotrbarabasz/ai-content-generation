"""Provider-neutral publishing and localization handoff orchestration."""

from __future__ import annotations

import json
from collections.abc import Mapping

from app.domain.localization_handoff import LocalizationHandoff
from app.domain.export_config import ExportConfig
from app.domain.types import JsonDict
from app.providers.interfaces import PublicationResult, PublishingProvider, PublishingRequest
from app.storage.artifact_store import ArtifactStore


class PublishingModule:
    """Enforce final approval before invoking any publishing provider."""

    def __init__(
        self,
        *,
        publishing_provider: PublishingProvider,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._publishing_provider = publishing_provider
        self._artifact_store = artifact_store

    def publish(self, handoff: Mapping[str, object]) -> JsonDict:
        request = PublishingRequest.create(handoff)
        if not request.approved:
            raise ValueError("Final export approval is required before provider invocation.")
        raw_result = self._publishing_provider.publish(request)
        result = raw_result.to_payload() if isinstance(raw_result, PublicationResult) else dict(raw_result)
        localization = handoff.get("localization", {})
        if not isinstance(localization, Mapping):
            localization = {}
        source_language = str(handoff.get("sourceLanguage") or "")
        export_config = ExportConfig.from_mapping(
            localization,
            source_language=source_language,
        )
        localization_handoff = LocalizationHandoff.create(
            export_id=str(handoff.get("exportId") or ""),
            source_language=source_language,
            targets=export_config.localization_targets,
            strategy=export_config.localization_strategy,
            manual_acceptance_required=export_config.manual_acceptance_required,
            custom_audio_fallback_enabled=export_config.custom_audio_fallback_enabled,
            provider=str(result.get("provider") or ""),
            publish_ref=str(result.get("publish_ref") or ""),
        )
        payload = localization_handoff.to_payload()
        artifact_payload: JsonDict | None = None
        if self._artifact_store is not None:
            artifact = self._artifact_store.save_artifact(
                "localization_handoff.json",
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                metadata={
                    "module_name": "publishing",
                    "artifact_type": "localization_handoff",
                    "export_id": localization_handoff.export_id,
                },
            )
            artifact_payload = artifact.to_payload()
        return {
            "publication": result,
            "localization_handoff": payload,
            "artifact": artifact_payload,
        }


__all__ = ["PublishingModule"]

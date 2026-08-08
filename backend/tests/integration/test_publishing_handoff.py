from __future__ import annotations

import json

import pytest

from app.api.main import create_app
from app.api.routes.publishing import (
    LocalizationDecisionRequest,
    PlatformStatusRequest,
    decide_localization,
    register_localization_handoff,
    reset_publishing_state,
    update_localization_platform_status,
)
from app.domain.localization_handoff import LocalizationHandoff
from app.modules.publishing import PublishingModule
from app.providers.mocks import MockPublishingProvider
from app.storage.local_store import LocalArtifactStore


def _handoff(approved=True):
    return {
        "platform": "youtube",
        "exportId": "export_1",
        "sourceLanguage": "en",
        "approved": approved,
        "idempotencyKey": "publish-1",
        "metadata": {"privacyStatus": "unlisted"},
        "localization": {
            "localizationStrategy": "platform_auto_dub",
            "localizationTargets": ["pl"],
            "manualAcceptanceRequired": True,
            "customAudioFallbackEnabled": True,
        },
    }


def test_publishing_module_persists_truthful_localization_handoff(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    module = PublishingModule(
        publishing_provider=MockPublishingProvider(), artifact_store=store
    )
    result = module.publish(_handoff())
    assert result["publication"]["status"] == "published"
    assert result["localization_handoff"]["sourceLanguage"] == "en"
    assert result["localization_handoff"]["targetStates"]["pl"]["status"] == "pending_platform_processing"
    artifact = result["artifact"]
    persisted = json.loads(store.read_artifact(artifact["storage_key"]))
    assert persisted == result["localization_handoff"]


def test_publishing_module_blocks_unapproved_export_before_provider() -> None:
    class RecordingProvider(MockPublishingProvider):
        def __init__(self):
            super().__init__()
            self.called = False

        def publish(self, export_bundle, target=None):
            self.called = True
            return super().publish(export_bundle, target)

    provider = RecordingProvider()
    with pytest.raises(ValueError, match="approval"):
        PublishingModule(publishing_provider=provider).publish(_handoff(False))
    assert provider.called is False


def test_manual_localization_state_is_exposed_and_updated_via_api() -> None:
    reset_publishing_state()
    handoff = register_localization_handoff(
        LocalizationHandoff.create(
            export_id="export_api",
            source_language="en",
            targets=["pl"],
            strategy="platform_auto_dub",
            manual_acceptance_required=True,
            custom_audio_fallback_enabled=True,
        )
    )
    paths = set(create_app().openapi()["paths"])
    assert (
        "/api/v1/publishing/localization-handoffs/{handoff_id}/targets/{language}/decisions"
        in paths
    )
    update_localization_platform_status(
        handoff.id,
        "pl",
        PlatformStatusRequest.model_validate({"status": "available_for_review"}),
    )
    response = decide_localization(
        handoff.id,
        "pl",
        LocalizationDecisionRequest.model_validate(
            {"decision": "accept", "reviewerId": "reviewer", "comment": "Approved"}
        ),
    )
    assert response.source_language == "en"
    assert response.target_states["pl"].status == "accepted"
    assert len(response.target_states["pl"].decision_history) == 1

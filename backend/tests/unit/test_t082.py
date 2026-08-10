from __future__ import annotations

import pytest

from app.api.schemas import LocalizationHandoffSchema
from app.domain.base import DomainValidationError
from app.domain.localization_handoff import LocalizationHandoff


def _handoff() -> LocalizationHandoff:
    return LocalizationHandoff.create(
        export_id="export_1",
        source_language="en",
        targets=["pl"],
        strategy="platform_auto_dub",
        manual_acceptance_required=True,
        custom_audio_fallback_enabled=True,
        provider="youtube",
        publish_ref="https://www.youtube.com/watch?v=video_1",
    )


def test_manual_platform_review_acceptance_is_idempotent() -> None:
    handoff = _handoff()
    handoff.update_platform_status(language="pl", status="available_for_review")
    first = handoff.decide(
        language="pl", decision="accept", reviewer_id="reviewer", comment="Sounds good."
    )
    second = handoff.decide(
        language="pl", decision="accept", reviewer_id="reviewer", comment="Sounds good."
    )
    assert first is second
    assert handoff.target_states["pl"].status == "accepted"
    assert len(handoff.target_states["pl"].decision_history) == 1
    with pytest.raises(DomainValidationError, match="does not allow"):
        handoff.decide(language="pl", decision="reject", reviewer_id="other")


def test_rejection_requires_custom_audio_and_preserves_approved_metadata() -> None:
    handoff = _handoff()
    handoff.update_platform_status(language="pl", status="available_for_review")
    handoff.decide(
        language="pl", decision="reject", reviewer_id="reviewer", comment="Pronunciation"
    )
    target = handoff.target_states["pl"]
    assert target.status == "custom_audio_required"
    supplied = handoff.supply_custom_audio(
        language="pl",
        artifact_reference="run/localization/voiceover.pl.wav",
        checksum="b" * 64,
        approved_label="approved-pl-v1",
        provenance="licensed studio recording",
    )
    repeated = handoff.supply_custom_audio(
        language="pl",
        artifact_reference="run/localization/voiceover.pl.wav",
        checksum="b" * 64,
        approved_label="approved-pl-v1",
        provenance="licensed studio recording",
    )
    assert supplied is repeated
    assert target.status == "custom_audio_supplied"
    assert target.custom_audio["targetLanguage"] == "pl"
    assert target.decision_history[0].decision == "reject"
    with pytest.raises(DomainValidationError, match="relative"):
        _handoff().supply_custom_audio(
            language="pl",
            artifact_reference="D:\\private\\voice.wav",
            checksum="b" * 64,
            approved_label="approved",
            provenance="private",
        )

    private_metadata = _handoff()
    private_metadata.update_platform_status(language="pl", status="unavailable")
    with pytest.raises(DomainValidationError, match="private paths"):
        private_metadata.supply_custom_audio(
            language="pl",
            artifact_reference="run/localization/voice.wav",
            checksum="b" * 64,
            approved_label="approved",
            provenance="recorded at D:\\private\\studio.wav",
        )


def test_changes_requested_unavailable_and_api_serialization_are_truthful() -> None:
    handoff = _handoff()
    handoff.update_platform_status(language="pl", status="available_for_review")
    handoff.decide(
        language="pl",
        decision="request_changes",
        reviewer_id="reviewer",
        comment="Review names again.",
    )
    assert handoff.target_states["pl"].status == "changes_requested"
    payload = handoff.to_payload()
    schema = LocalizationHandoffSchema.model_validate(payload)
    assert schema.source_language == "en"
    assert schema.targets == ["pl"]
    assert schema.target_states["pl"].status == "changes_requested"
    assert "autoDubResult" not in payload

    unavailable = _handoff()
    unavailable.update_platform_status(language="pl", status="unavailable")
    unavailable.require_custom_audio(language="pl")
    assert unavailable.target_states["pl"].status == "custom_audio_required"

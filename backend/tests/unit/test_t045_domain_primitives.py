from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from app.domain.base import DomainEntity, DomainValidationError, new_id, utc_now
from app.domain.enums import (
    ContentGenre,
    ContentType,
    DurationProfile,
    ProviderType,
    TargetPlatform,
    WorkflowPreset,
)


class SampleEntity(DomainEntity):
    pass


@pytest.mark.parametrize(
    "prefix",
    ["project", "workflow_config", "artifact"],
)
def test_new_id_uses_requested_prefix_and_hex_suffix(prefix: str) -> None:
    identifier = new_id(prefix)

    assert identifier.startswith(f"{prefix}_")
    assert re.fullmatch(rf"{re.escape(prefix)}_[0-9a-f]+", identifier)


def test_utc_now_returns_timezone_aware_utc_datetime() -> None:
    current = utc_now()

    assert current.tzinfo is UTC
    assert current.utcoffset() == UTC.utcoffset(current)


def test_domain_entity_serializes_with_created_at_metadata() -> None:
    entity = SampleEntity(id="sample_1")
    payload = asdict(entity)

    assert payload["id"] == "sample_1"
    assert isinstance(payload["created_at"], datetime)
    assert payload["created_at"].tzinfo is UTC

    serialized = json.dumps(
        payload,
        default=lambda value: value.isoformat()
        if isinstance(value, datetime)
        else value,
    )

    assert '"id": "sample_1"' in serialized
    assert '"created_at":' in serialized
    assert "+00:00" in serialized


def test_domain_validation_error_is_a_value_error() -> None:
    with pytest.raises(DomainValidationError):
        raise DomainValidationError("invalid domain state")


@pytest.mark.parametrize(
    ("enum_member", "raw_value"),
    [
        (WorkflowPreset.SHORT_VIDEO, "short_video"),
        (WorkflowPreset.LONG_FORM_SCRIPT_VOICEOVER, "long_form_script_voiceover"),
        (ContentType.SHORT_VIDEO, "short_video"),
        (ContentType.LONG_FORM_VIDEO, "long_form_video"),
        (ContentType.AUDIO_ONLY, "audio_only"),
        (ContentType.SCRIPT_ONLY, "script_only"),
        (ContentGenre.NEWS, "news"),
        (ContentGenre.DOCUMENTARY, "documentary"),
        (DurationProfile.SHORT_15_30S, "15_30s"),
        (DurationProfile.SIXTY_SECONDS, "60s"),
        (TargetPlatform.YOUTUBE_SHORTS, "youtube_shorts"),
        (TargetPlatform.GENERIC_EXPORT, "generic_export"),
        (ProviderType.LLM, "LLMProvider"),
        (ProviderType.PUBLISHING, "PublishingProvider"),
    ],
)
def test_strenum_members_round_trip_through_strings_and_json(
    enum_member: str, raw_value: str
) -> None:
    assert str(enum_member) == raw_value
    assert enum_member == raw_value
    assert type(enum_member)(raw_value) is enum_member

    encoded = json.dumps({"value": enum_member})
    decoded = json.loads(encoded)

    assert decoded["value"] == raw_value

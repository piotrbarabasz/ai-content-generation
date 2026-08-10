from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.tts.preview as preview_module
from app.api.schemas import WorkflowConfigCreateRequest, WorkflowConfigSchema
from app.domain.base import DomainValidationError
from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.domain.workflow_config import WorkflowConfig
from app.providers.tts_capabilities import TTSCapabilities
from app.providers.tts_settings import TTSSettings, TTSSettingsError
from app.tts.catalog import (
    TTSCatalog,
    TTSModelDescriptor,
    TTSProviderDescriptor,
    TTSVoiceDescriptor,
)
from app.tts.selection import TTSSelectionError, map_catalog_selection
from app.providers.tts_factory import TTSFactoryError, build_tts_provider


def _provider(
    provider_id: str,
    model_id: str,
    voice_id: str,
    *,
    language: str,
    voice_mode: str,
    usage_policy: str = "production",
) -> TTSProviderDescriptor:
    capabilities = TTSCapabilities(
        provider_name=provider_id,
        supported_languages=(language,),
        voice_modes=(voice_mode,),
        reference_audio_required=voice_mode == "reference",
        speaking_rate_supported=False,
        usage_policy=usage_policy,
    )
    voice = TTSVoiceDescriptor(
        id=voice_id,
        display_name=voice_id,
        provider_id=provider_id,
        model_id=model_id,
        voice_mode=voice_mode,
        supported_languages=(language,),
        preview_supported=True,
        reference_audio_required=voice_mode == "reference",
    )
    return TTSProviderDescriptor(
        id=provider_id,
        display_name=provider_id,
        usage_policy=usage_policy,
        supported_languages=(language,),
        capabilities=capabilities,
        models=(
            TTSModelDescriptor(
                id=model_id,
                display_name=model_id,
                provider_id=provider_id,
                supported_languages=(language,),
                voices=(voice,),
                runtime_required=True,
                asset_required=provider_id != "chatterbox_v3",
            ),
        ),
    )


def _catalog() -> TTSCatalog:
    return TTSCatalog(
        providers=(
            _provider("chatterbox_v3", "v3", "builtin", language="en", voice_mode="builtin"),
            _provider("piper", "pl_PL-gosia-medium", "gosia", language="pl", voice_mode="catalog"),
            _provider(
                "xtts_v2_eval",
                "xtts_v2",
                "reference",
                language="en",
                voice_mode="reference",
                usage_policy="evaluation_only",
            ),
        )
    )


def _workflow_kwargs(mapping) -> dict[str, object]:
    return {
        "project_id": "project_t088",
        "workflow_preset": "short_video",
        "content_type": "short_video",
        "content_genre": "news",
        "duration_profile": "60s",
        "target_platform": "youtube_shorts",
        "language": mapping.language,
        "tone": "neutral",
        "provider_config": mapping.provider_config,
        "voice_config": mapping.voice_config,
    }


def _api_payload(
    provider_config: dict[str, object],
    *,
    voice_config: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "projectId": "project_t088_alias",
        "workflowPreset": "short_video",
        "contentType": "short_video",
        "contentGenre": "news",
        "durationProfile": "60s",
        "targetPlatform": "youtube_shorts",
        "language": "en",
        "tone": "neutral",
        "providerConfig": provider_config,
        "voiceConfig": voice_config or {},
    }


def test_chatterbox_builtin_maps_only_existing_workflow_fields_and_tempo() -> None:
    mapping = map_catalog_selection(
        catalog=_catalog(),
        provider="chatterbox_v3",
        model="v3",
        voice="builtin",
        language="EN",
        tempo=1.25,
        synthesis_settings={"cfgWeight": 0.4},
    )

    assert mapping.language == "en"
    assert mapping.provider_config == {
        "tts": {
            "providerName": "chatterbox_v3",
            "enabled": True,
            "settings": {
                "provider": "chatterbox_v3",
                "usage_policy": "production",
                "model_variant": "v3",
                "cfg_weight": 0.4,
            },
        }
    }
    assert mapping.voice_config == {
        "voice_id": "builtin",
        "voice_mode": "builtin",
        "post_processing": {"tempo": 1.25},
    }
    assert "language" not in mapping.voice_config
    assert "tempo" not in mapping.provider_config["tts"]["settings"]


def test_reference_requires_approved_opaque_metadata_and_never_maps_a_path() -> None:
    reference_catalog = TTSCatalog(
        providers=(
            _provider("chatterbox_v3", "v3", "reference", language="en", voice_mode="reference"),
        )
    )
    with pytest.raises(TTSSelectionError, match="artifact id"):
        map_catalog_selection(
            catalog=reference_catalog,
            provider="chatterbox_v3",
            model="v3",
            voice="reference",
            language="en",
        )

    mapping = map_catalog_selection(
        catalog=reference_catalog,
        provider="chatterbox_v3",
        model="v3",
        voice="reference",
        language="en",
        reference_audio_artifact_id="artifact-approved-1",
        reference_audio_metadata={
            "checksum": "a" * 64,
            "approvalLabel": "editor-approved",
            "approved": True,
        },
    )

    encoded = json.dumps(mapping.to_payload()).lower()
    assert mapping.voice_config["reference_audio_artifact_id"] == "artifact-approved-1"
    assert "path" not in encoded and "file:" not in encoded


def test_piper_preserves_catalog_model_voice_and_rejects_foreign_settings() -> None:
    mapping = map_catalog_selection(
        catalog=_catalog(),
        provider="piper",
        model="pl_PL-gosia-medium",
        voice="gosia",
        language="pl",
        synthesis_settings={"lengthScale": 1.1},
    )

    assert mapping.provider_config["tts"]["settings"]["model_key"] == "pl_PL-gosia-medium"
    assert mapping.voice_config["voice_id"] == "gosia"
    assert mapping.voice_config["voice_mode"] == "catalog"
    with pytest.raises(TTSSelectionError, match="not declared"):
        map_catalog_selection(
            catalog=_catalog(),
            provider="piper",
            model="pl_PL-gosia-medium",
            voice="gosia",
            language="pl",
            synthesis_settings={"temperature": 0.5},
        )


def test_evaluation_provider_is_rejected_before_factory_construction() -> None:
    with pytest.raises(TTSSelectionError, match="evaluation-only"):
        map_catalog_selection(
            catalog=_catalog(),
            provider="xtts_v2_eval",
            model="xtts_v2",
            voice="reference",
            language="en",
            reference_audio_artifact_id="approved-ref",
            reference_audio_metadata={
                "checksum": "b" * 64,
                "approval_label": "approved",
                "approved": True,
            },
        )

    calls: list[object] = []
    config = ProviderConfig.create(
        workflow_config_id="workflow",
        provider_type=ProviderType.TTS,
        provider_name="xtts_v2_eval",
        settings={
            "provider": "xtts_v2_eval",
            "usage_policy": "production",
            "model_variant": "xtts_v2",
            "reference_audio_path": "controlled-reference.wav",
            "approved_label": "approved",
        },
    )
    with pytest.raises(TTSFactoryError, match="evaluation-only"):
        build_tts_provider(
            config,
            provider_factories={"xtts_v2_eval": lambda settings: calls.append(settings)},
        )
    assert calls == []


def test_workflow_validation_rejects_stale_selection_but_keeps_legacy_mock_valid() -> None:
    mapping = map_catalog_selection(
        catalog=_catalog(),
        provider="piper",
        model="pl_PL-gosia-medium",
        voice="gosia",
        language="pl",
    )
    WorkflowConfig.create(**_workflow_kwargs(mapping), tts_catalog=_catalog())

    stale = mapping.to_payload()
    stale["provider_config"]["tts"]["settings"]["model_key"] = "stale"
    with pytest.raises(DomainValidationError, match="Unknown TTS model"):
        WorkflowConfig.create(
            **{
                **_workflow_kwargs(mapping),
                "provider_config": stale["provider_config"],
            },
            tts_catalog=_catalog(),
        )

    legacy = WorkflowConfig.create(
        project_id="legacy",
        workflow_preset="short_video",
        content_type="short_video",
        content_genre="news",
        duration_profile="60s",
        target_platform="youtube_shorts",
        language="en",
        tone="neutral",
        provider_config={"tts": {"providerName": "mock", "enabled": True, "settings": {}}},
    )
    assert legacy.provider_config["tts"]["providerName"] == "mock"


def test_api_camel_case_round_trip_normalizes_internal_tts_settings() -> None:
    mapping = map_catalog_selection(
        catalog=_catalog(),
        provider="chatterbox_v3",
        model="v3",
        voice="builtin",
        language="en",
        synthesis_settings={"cfgWeight": 0.5},
    )
    payload = {
        "projectId": "project_t088",
        "workflowPreset": "short_video",
        "contentType": "short_video",
        "contentGenre": "news",
        "durationProfile": "60s",
        "targetPlatform": "youtube_shorts",
        "language": "en",
        "tone": "neutral",
        "providerConfig": {
            "tts": {
                "providerName": "chatterbox_v3",
                "enabled": True,
                "settings": {
                    "provider": "chatterbox_v3",
                    "usagePolicy": "production",
                    "modelVariant": "v3",
                    "cfgWeight": 0.5,
                },
            }
        },
        "voiceConfig": {
            "voiceId": "builtin",
            "voiceMode": "builtin",
            "postProcessing": {"tempo": 1.0},
        },
    }
    request = WorkflowConfigCreateRequest.model_validate(payload)
    domain = request.to_domain()
    response = WorkflowConfigSchema.model_validate(domain).model_dump(by_alias=True)

    assert domain.provider_config == mapping.provider_config
    assert domain.voice_config == mapping.voice_config
    assert response["providerConfig"] == payload["providerConfig"]
    assert response["voiceConfig"] == payload["voiceConfig"]


@pytest.mark.parametrize("snake_name", ["chatterbox_v3", "piper"])
def test_api_rejects_equal_or_conflicting_provider_name_aliases(snake_name: str) -> None:
    with pytest.raises(ValidationError, match="cannot contain both providerName and provider_name"):
        WorkflowConfigCreateRequest.model_validate(
            _api_payload(
                {
                    "tts": {
                        "providerName": "chatterbox_v3",
                        "provider_name": snake_name,
                        "enabled": True,
                        "settings": {},
                    }
                }
            )
        )


def test_api_rejects_non_mock_snake_alias_and_canonicalizes_legacy_mock() -> None:
    with pytest.raises(ValidationError, match="must use providerName"):
        WorkflowConfigCreateRequest.model_validate(
            _api_payload(
                {
                    "tts": {
                        "provider_name": "chatterbox_v3",
                        "enabled": True,
                        "settings": {},
                    }
                }
            )
        )

    request = WorkflowConfigCreateRequest.model_validate(
        _api_payload(
            {
                "tts": {
                    "provider_name": "mock",
                    "enabled": True,
                    "settings": {},
                }
            }
        )
    )
    domain = request.to_domain()
    response = WorkflowConfigSchema.model_validate(domain).model_dump(by_alias=True)

    expected = {"tts": {"providerName": "mock", "enabled": True, "settings": {}}}
    assert request.provider_config == expected
    assert domain.provider_config == expected
    assert response["providerConfig"] == expected


def test_domain_rejects_noncanonical_duplicate_provider_name_alias() -> None:
    mapping = map_catalog_selection(
        catalog=_catalog(),
        provider="chatterbox_v3",
        model="v3",
        voice="builtin",
        language="en",
    )
    polluted_provider_config = json.loads(json.dumps(mapping.provider_config))
    polluted_provider_config["tts"]["provider_name"] = "piper"

    with pytest.raises(DomainValidationError, match="cannot contain both providerName and provider_name"):
        WorkflowConfig.create(
            **{
                **_workflow_kwargs(mapping),
                "provider_config": polluted_provider_config,
            },
            tts_catalog=_catalog(),
        )


def test_tts_settings_normalize_camel_case_and_reject_provider_foreign_values() -> None:
    settings = TTSSettings.from_mapping(
        {
            "provider": "chatterbox_v3",
            "usagePolicy": "production",
            "modelVariant": "v3",
            "cfgWeight": 0.25,
        },
        provider="chatterbox_v3",
    )
    assert settings.cfg_weight == 0.25
    with pytest.raises(TTSSettingsError, match="Unsupported settings"):
        TTSSettings.from_mapping(
            {"provider": "piper", "modelKey": "voice", "cfgWeight": 0.2},
            provider="piper",
        )


def test_preview_calls_canonical_mapper_before_provider_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapper_calls: list[dict[str, object]] = []
    builder_calls: list[ProviderConfig] = []
    original_mapper = preview_module.map_catalog_selection

    def tracking_mapper(**kwargs):
        mapper_calls.append(kwargs)
        return original_mapper(**kwargs)

    monkeypatch.setattr(preview_module, "map_catalog_selection", tracking_mapper)
    service = preview_module.TTSPreviewService(
        catalog=_catalog(),
        preview_root=Path("unused-preview-root"),
        provider_builder=lambda config: builder_calls.append(config),  # type: ignore[arg-type,return-value]
    )

    with pytest.raises(preview_module.TTSPreviewError, match="Unknown TTS model"):
        service.synthesize_preview(
            provider="chatterbox_v3",
            model="stale",
            voice="builtin",
            language="en",
            tempo=1.0,
            text="Canonical mapper first.",
        )

    assert len(mapper_calls) == 1
    assert builder_calls == []

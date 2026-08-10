from __future__ import annotations

from app.api.schemas import TTSCatalog
from app.providers.tts_catalog import build_tts_catalog


def test_tts_catalog_schema_round_trips_to_camel_case_payload() -> None:
    catalog = build_tts_catalog()

    schema = TTSCatalog.model_validate(catalog.to_payload())
    payload = schema.model_dump(by_alias=True, exclude_none=True)

    assert tuple(provider["id"] for provider in payload["providers"]) == (
        "chatterbox_v3",
        "piper",
        "xtts_v2_eval",
    )

    chatterbox_provider = payload["providers"][0]
    chatterbox_model = chatterbox_provider["models"][0]
    chatterbox_voice = chatterbox_model["voices"][0]

    assert set(chatterbox_provider) == {
        "id",
        "displayName",
        "usagePolicy",
        "supportedLanguages",
        "capabilities",
        "models",
    }
    assert set(chatterbox_model) == {
        "id",
        "displayName",
        "providerId",
        "supportedLanguages",
        "runtimeRequired",
        "assetRequired",
        "voices",
    }
    assert set(chatterbox_voice) == {
        "id",
        "displayName",
        "providerId",
        "modelId",
        "voiceMode",
        "supportedLanguages",
        "previewSupported",
        "referenceAudioRequired",
    }

    piper_provider = payload["providers"][1]
    piper_voice = piper_provider["models"][0]["voices"][0]

    assert piper_voice["publicMetadata"]["provider_key"]
    assert payload == TTSCatalog.model_validate(catalog.to_payload()).model_dump(
        by_alias=True,
        exclude_none=True,
    )

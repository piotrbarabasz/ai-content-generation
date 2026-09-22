"""Existing Chatterbox adapter composition; no heavy imports in the application."""

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.chatterbox_v3 import ChatterboxV3Provider
from app.providers.tts_catalog import build_tts_catalog
from app.providers.tts_factory import build_tts_provider
from app.tts.selection import map_catalog_selection
from .chatterbox_profile import profile_fingerprint


def prepare_voice(selection, max_words, health, runtime_identity, *, model_loader=None):
    if (set(selection) - {"provider", "model", "voice", "language", "settings"}
            or selection.get("provider") != "chatterbox_v3" or selection.get("model") != "v3"
            or selection.get("voice") != "builtin" or selection.get("language") not in health.languages):
        raise ValueError("Managed Chatterbox requires V3, a tested language and the builtin voice.")
    if selection.get("settings", {}).get("device") != health.device:
        raise ValueError("Select the exact health-tested CUDA device; fallback is not implicit.")
    if type(max_words) is not int or not 1 <= max_words <= 1000:
        raise ValueError("Invalid Chatterbox technical chunk size.")
    mapping = map_catalog_selection(catalog=build_tts_catalog(), **{k: selection[k] for k in
                                    ("provider", "model", "voice", "language")},
                                    synthesis_settings=selection.get("settings"))

    def factory(settings):
        return ChatterboxV3Provider(device=health.device, language_id=mapping.language,
                                   model_loader=model_loader, **{name: getattr(settings, name) for name in
                                   ("exaggeration", "cfg_weight", "temperature", "repetition_penalty", "min_p", "top_p")})

    provider = build_tts_provider(ProviderConfig.create(
        workflow_config_id="section-audio", provider_type=ProviderType.TTS,
        provider_name="chatterbox_v3", settings=mapping.provider_config["tts"]["settings"]),
        provider_factories={"chatterbox_v3": factory})
    config = mapping.voice_config | selection["settings"] | {"language_id": mapping.language}
    config.pop("post_processing", None)
    prepared = {"selection": selection, "max_words": max_words, "voice_config": config,
                "effective_identity": {"synthesis": provider.effective_synthesis_identity(config),
                                       "runtime": runtime_identity, "model_fingerprint": profile_fingerprint(),
                                       "runtime_device": health.decision().to_payload()}}
    return prepared, provider

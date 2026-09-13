"""Managed Piper composition using the existing catalog, factory and provider."""

import io
import wave

from app.domain.enums import ProviderType
from app.domain.provider_config import ProviderConfig
from app.providers.piper_catalog import get_piper_voice_catalog_entry
from app.providers.piper_tts import PiperTTSProvider
from app.providers.tts_catalog import build_tts_catalog
from app.providers.tts_factory import build_tts_provider
from app.tts.selection import map_catalog_selection
from .model_index import ModelIndex


class ManagedPiperBackend:
    """Pinned Piper 1.6 API bridge injected into PiperTTSProvider, not a provider."""

    def __init__(self, voice):
        from piper.voice import PiperVoice
        self.voice = PiperVoice.load(str(voice.model_path), config_path=str(voice.config_path), use_cuda=False)

    def synthesize(self, text, *, language_id=None, **settings):
        from piper.config import SynthesisConfig
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            self.voice.synthesize_wav(text, output, syn_config=SynthesisConfig(**settings))
        return buffer.getvalue()


def prepare_voice(selection, max_words, model_root, runtime_identity):
    if set(selection) - {"provider", "model", "voice", "language", "settings"} or selection.get("provider") != "piper":
        raise ValueError("D010 managed composition requires a curated Piper selection.")
    if selection.get("settings", {}).get("device", "cpu") != "cpu":
        raise ValueError("The managed Piper profile is CPU-only.")
    mapping = map_catalog_selection(catalog=build_tts_catalog(), provider="piper",
                                    model=selection["model"], voice=selection["voice"], language=selection["language"],
                                    synthesis_settings=selection.get("settings"))
    entry = get_piper_voice_catalog_entry(selection["model"])
    installed = ModelIndex(model_root).installed(entry)
    if installed is None:
        raise ValueError("Download and verify the selected voice before section generation.")
    config = mapping.provider_config["tts"]["settings"] | {"language_id": mapping.language}
    provider = build_tts_provider(ProviderConfig.create(workflow_config_id="section-audio", provider_type=ProviderType.TTS,
                                                       provider_name="piper", settings=config), provider_factories={
        "piper": lambda settings: PiperTTSProvider(device="cpu", language_id=mapping.language, model_key=settings.model_key,
            length_scale=settings.length_scale, volume=settings.volume, noise_scale=settings.noise_scale,
            noise_w_scale=settings.noise_w_scale, model_loader=lambda reference: ManagedPiperBackend(installed))})
    voice_config = mapping.voice_config | {"language_id": mapping.language}
    # Tempo is not an input to raw synthesis; D011 owns derivatives.
    voice_config.pop("post_processing", None)
    prepared = {"selection": selection, "max_words": max_words, "voice_config": voice_config,
                "effective_identity": {"synthesis": provider.effective_synthesis_identity(voice_config),
                                       "runtime": runtime_identity, "voice_fingerprint": installed.fingerprint}}
    return prepared, provider

"""Explicit catalog adapters for the production TTS providers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import re

from app.tts.catalog import TTSCatalog, TTSCatalogError, TTSModelDescriptor, TTSProviderDescriptor, TTSVoiceDescriptor

from .chatterbox_v3 import ChatterboxV3Provider
from .piper_catalog import PiperVoiceCatalogEntry, list_piper_voice_catalog
from .piper_tts import PiperTTSProvider
from .xtts_v2 import XTTSV2EvalProvider


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class TTSCatalogAdapterError(ValueError):
    """Raised when catalog adapter registration or construction is invalid."""


@dataclass(frozen=True, slots=True)
class TTSCatalogAdapter:
    """Explicit provider-level adapter that contributes one catalog provider."""

    provider_id: str
    build_provider_descriptor: Callable[[], TTSProviderDescriptor]

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str):
            raise TTSCatalogAdapterError("TTS catalog adapter provider_id must be a string.")
        normalized_provider_id = self.provider_id.strip()
        if not normalized_provider_id:
            raise TTSCatalogAdapterError("TTS catalog adapter provider_id is required.")
        if _IDENTIFIER_RE.fullmatch(normalized_provider_id) is None:
            raise TTSCatalogAdapterError("TTS catalog adapter provider_id must be a stable identifier.")
        if not callable(self.build_provider_descriptor):
            raise TTSCatalogAdapterError("TTS catalog adapter build_provider_descriptor must be callable.")
        object.__setattr__(self, "provider_id", normalized_provider_id)


class TTSCatalogAdapterRegistry:
    """Deterministic registry of explicit provider-level catalog adapters."""

    def __init__(self, adapters: Iterable[TTSCatalogAdapter] | None = None) -> None:
        self._adapters: dict[str, TTSCatalogAdapter] = {}
        self._order: list[str] = []
        if adapters is not None:
            for adapter in adapters:
                self.register(adapter)

    def register(self, adapter: TTSCatalogAdapter) -> TTSCatalogAdapter:
        if not isinstance(adapter, TTSCatalogAdapter):
            raise TTSCatalogAdapterError("TTS catalog adapter registration requires a TTSCatalogAdapter.")
        if adapter.provider_id in self._adapters:
            raise TTSCatalogAdapterError(
                f"Duplicate TTS catalog adapter registration: {adapter.provider_id}."
            )
        self._adapters[adapter.provider_id] = adapter
        self._order.append(adapter.provider_id)
        return adapter

    def list_provider_ids(self) -> tuple[str, ...]:
        return tuple(self._order)

    def build_catalog(self) -> TTSCatalog:
        try:
            providers = tuple(
                self._adapters[provider_id].build_provider_descriptor()
                for provider_id in self._order
            )
        except TTSCatalogError:
            raise
        except Exception as exc:  # pragma: no cover - defensive guard for future adapters.
            raise TTSCatalogAdapterError("TTS catalog adapter construction failed.") from exc
        return TTSCatalog(providers=providers)


def _provider_display_name(provider_id: str) -> str:
    if provider_id == "xtts_v2_eval":
        return "XTTS v2 Evaluation"
    if provider_id == "chatterbox_v3":
        return "Chatterbox Multilingual V3"
    if provider_id == "piper":
        return "Piper"
    return provider_id


def _chatterbox_provider_descriptor() -> TTSProviderDescriptor:
    capabilities = ChatterboxV3Provider.catalog_capabilities()
    return TTSProviderDescriptor(
        id="chatterbox_v3",
        display_name=_provider_display_name("chatterbox_v3"),
        usage_policy=capabilities.usage_policy,
        supported_languages=capabilities.supported_languages,
        capabilities=capabilities,
        models=(
            TTSModelDescriptor(
                id="v3",
                display_name="Chatterbox V3",
                provider_id="chatterbox_v3",
                supported_languages=capabilities.supported_languages,
                voices=(
                    TTSVoiceDescriptor(
                        id="builtin",
                        display_name="Builtin",
                        provider_id="chatterbox_v3",
                        model_id="v3",
                        voice_mode="builtin",
                        supported_languages=capabilities.supported_languages,
                        preview_supported=True,
                        reference_audio_required=False,
                    ),
                    TTSVoiceDescriptor(
                        id="reference",
                        display_name="Reference",
                        provider_id="chatterbox_v3",
                        model_id="v3",
                        voice_mode="reference",
                        supported_languages=capabilities.supported_languages,
                        preview_supported=True,
                        reference_audio_required=True,
                    ),
                ),
                runtime_required=True,
                asset_required=False,
            ),
        ),
    )


def _piper_voice_public_metadata(entry: PiperVoiceCatalogEntry) -> dict[str, object]:
    return entry.to_public_metadata_payload()


def _piper_supported_language(entry: PiperVoiceCatalogEntry) -> str:
    primary_language = entry.language_id.split("_", 1)[0].strip().lower()
    return primary_language or "pl"


def _piper_provider_descriptor() -> TTSProviderDescriptor:
    capabilities = PiperTTSProvider.catalog_capabilities()
    models = tuple(
        TTSModelDescriptor(
            id=entry.provider_key,
            display_name=f"{entry.voice_name.replace('_', ' ').title()} {entry.quality}",
            provider_id="piper",
            supported_languages=(_piper_supported_language(entry),),
            voices=(
                TTSVoiceDescriptor(
                    id=entry.voice_name,
                    display_name=entry.voice_name.replace("_", " ").title(),
                    provider_id="piper",
                    model_id=entry.provider_key,
                    voice_mode="catalog",
                    supported_languages=(_piper_supported_language(entry),),
                    preview_supported=True,
                    reference_audio_required=False,
                    public_metadata=_piper_voice_public_metadata(entry),
                ),
            ),
            runtime_required=True,
            asset_required=True,
        )
        for entry in list_piper_voice_catalog()
    )
    return TTSProviderDescriptor(
        id="piper",
        display_name=_provider_display_name("piper"),
        usage_policy=capabilities.usage_policy,
        supported_languages=capabilities.supported_languages,
        capabilities=capabilities,
        models=models,
    )


def _xtts_provider_descriptor() -> TTSProviderDescriptor:
    capabilities = XTTSV2EvalProvider.catalog_capabilities()
    return TTSProviderDescriptor(
        id="xtts_v2_eval",
        display_name=_provider_display_name("xtts_v2_eval"),
        usage_policy=capabilities.usage_policy,
        supported_languages=capabilities.supported_languages,
        capabilities=capabilities,
        models=(
            TTSModelDescriptor(
                id="xtts_v2",
                display_name="XTTS v2",
                provider_id="xtts_v2_eval",
                supported_languages=capabilities.supported_languages,
                voices=(
                    TTSVoiceDescriptor(
                        id="reference",
                        display_name="Reference",
                        provider_id="xtts_v2_eval",
                        model_id="xtts_v2",
                        voice_mode="reference",
                        supported_languages=capabilities.supported_languages,
                        preview_supported=True,
                        reference_audio_required=True,
                    ),
                ),
                runtime_required=True,
                asset_required=True,
            ),
        ),
    )


_DEFAULT_TTS_CATALOG_ADAPTERS = TTSCatalogAdapterRegistry()
_DEFAULT_TTS_CATALOG_ADAPTERS.register(
    TTSCatalogAdapter(provider_id="chatterbox_v3", build_provider_descriptor=_chatterbox_provider_descriptor)
)
_DEFAULT_TTS_CATALOG_ADAPTERS.register(
    TTSCatalogAdapter(provider_id="piper", build_provider_descriptor=_piper_provider_descriptor)
)
_DEFAULT_TTS_CATALOG_ADAPTERS.register(
    TTSCatalogAdapter(provider_id="xtts_v2_eval", build_provider_descriptor=_xtts_provider_descriptor)
)


def register_tts_catalog_adapter(adapter: TTSCatalogAdapter) -> TTSCatalogAdapter:
    """Register an explicit adapter on the module-default production catalog registry."""

    return _DEFAULT_TTS_CATALOG_ADAPTERS.register(adapter)


def list_tts_catalog_adapters() -> tuple[str, ...]:
    """Return the explicit adapter registration order."""

    return _DEFAULT_TTS_CATALOG_ADAPTERS.list_provider_ids()


def build_tts_catalog(*, registry: TTSCatalogAdapterRegistry | None = None) -> TTSCatalog:
    """Build the deterministic TTS catalog from explicit adapters only."""

    return (registry or _DEFAULT_TTS_CATALOG_ADAPTERS).build_catalog()


__all__ = [
    "TTSCatalogAdapter",
    "TTSCatalogAdapterError",
    "TTSCatalogAdapterRegistry",
    "build_tts_catalog",
    "list_tts_catalog_adapters",
    "register_tts_catalog_adapter",
]

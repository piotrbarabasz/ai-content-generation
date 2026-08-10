"""Provider-neutral helpers for reliable text-to-speech work."""

from .catalog import (
    TTSCatalog,
    TTSCatalogError,
    TTSModelDescriptor,
    TTSProviderDescriptor,
    TTSVoiceDescriptor,
)
from .chunking import (
    NarrationChunk,
    NarrationChunkingSettings,
    chunk_narration,
    normalize_narration,
)

__all__ = [
    "TTSCatalog",
    "TTSCatalogError",
    "TTSModelDescriptor",
    "TTSProviderDescriptor",
    "TTSVoiceDescriptor",
    "NarrationChunk",
    "NarrationChunkingSettings",
    "chunk_narration",
    "normalize_narration",
]

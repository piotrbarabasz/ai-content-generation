"""Provider-neutral helpers for reliable text-to-speech work."""

from .chunking import (
    NarrationChunk,
    NarrationChunkingSettings,
    chunk_narration,
    normalize_narration,
)

__all__ = [
    "NarrationChunk",
    "NarrationChunkingSettings",
    "chunk_narration",
    "normalize_narration",
]

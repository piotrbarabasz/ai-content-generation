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
from .preview import (
    ApprovedReferenceAudio,
    PREVIEW_MANIFEST_VERSION,
    PREVIEW_POST_PROCESSING_VERSION,
    PREVIEW_TEXT_LIMIT,
    TTSPreviewError,
    TTSPreviewNotFoundError,
    TTSPreviewResult,
    TTSPreviewService,
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
    "ApprovedReferenceAudio",
    "PREVIEW_MANIFEST_VERSION",
    "PREVIEW_POST_PROCESSING_VERSION",
    "PREVIEW_TEXT_LIMIT",
    "TTSPreviewError",
    "TTSPreviewNotFoundError",
    "TTSPreviewResult",
    "TTSPreviewService",
]

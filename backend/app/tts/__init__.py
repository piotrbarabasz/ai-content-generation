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
from .selection import (
    TTSSelectionError,
    WorkflowTTSMapping,
    map_catalog_selection,
    validate_workflow_tts_mapping,
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
    "TTSSelectionError",
    "WorkflowTTSMapping",
    "map_catalog_selection",
    "validate_workflow_tts_mapping",
    "ApprovedReferenceAudio",
    "PREVIEW_MANIFEST_VERSION",
    "PREVIEW_POST_PROCESSING_VERSION",
    "PREVIEW_TEXT_LIMIT",
    "TTSPreviewError",
    "TTSPreviewNotFoundError",
    "TTSPreviewResult",
    "TTSPreviewService",
]

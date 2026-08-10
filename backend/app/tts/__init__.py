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
]

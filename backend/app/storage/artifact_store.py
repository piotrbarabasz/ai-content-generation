"""Artifact store abstraction."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable

from app.domain.types import JsonDict

from .manifest import ArtifactManifest


@runtime_checkable
class ArtifactStore(Protocol):
    """Persist and retrieve artifacts by storage key."""

    def save_artifact(
        self,
        name: str,
        content: bytes | str,
        metadata: JsonDict | None = None,
    ) -> ArtifactManifest:
        """Persist artifact content and return its manifest."""

    def read_artifact(self, key: str) -> bytes:
        """Read an artifact by storage key."""

    def list_artifacts(self, prefix: str = "") -> tuple[ArtifactManifest, ...]:
        """List stored artifact manifests matching a prefix."""


@runtime_checkable
class StreamingArtifactStore(ArtifactStore, Protocol):
    """Optional large-file capability; existing small-payload stores remain valid."""

    def import_file(self, name: str, source: Path | str,
                    metadata: JsonDict | None = None) -> ArtifactManifest: ...

    def import_stream(self, name: str, source: BinaryIO,
                      metadata: JsonDict | None = None) -> ArtifactManifest: ...

    def open_artifact(self, key: str) -> BinaryIO:
        """Open a published artifact for bounded reads; the caller closes it."""

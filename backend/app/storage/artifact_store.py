"""Artifact store abstraction."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

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

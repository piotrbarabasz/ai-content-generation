"""Map immutable D004 manifest metadata to D005 consumed-input records."""

from app.domain.base import DomainValidationError
from app.domain.dependencies import (
    DEPENDENCY_METADATA_KEY, ArtifactDependency, DependencyDeclaration, artifact_fingerprint,
)
from .artifact_index import ProjectArtifactIndex


class ArtifactDependencyIndex:
    def __init__(self, index: ProjectArtifactIndex):
        self.index = index

    def records(self) -> dict[str, ArtifactDependency]:
        """Legacy artifacts stay untracked; never guess inputs from names or paths."""
        manifests = {manifest.artifact_id: manifest for manifest in self.index.manifests()}
        records = {}
        for artifact_id, manifest in manifests.items():
            if DEPENDENCY_METADATA_KEY not in manifest.metadata:
                continue
            try:
                declaration = DependencyDeclaration.from_payload(manifest.metadata[DEPENDENCY_METADATA_KEY])
                for edge in declaration.request.inputs:
                    if edge.artifact_id is not None:
                        consumed = manifests.get(edge.artifact_id)
                        if consumed is None or artifact_fingerprint(consumed.artifact_id, consumed.checksum) != edge.fingerprint:
                            raise DomainValidationError("Consumed artifact is absent or its checksum does not match.")
                records[artifact_id] = ArtifactDependency(artifact_id, manifest.checksum, declaration)
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise DomainValidationError(f"Invalid dependency metadata for {artifact_id}.") from exc
        for record in records.values():
            for edge in record.declaration.request.inputs:
                consumed = records.get(edge.artifact_id)
                if consumed is not None and consumed.declaration.output_key != edge.key:
                    raise DomainValidationError(f"Invalid dependency metadata for {record.artifact_id}: input binding mismatch.")
        return records

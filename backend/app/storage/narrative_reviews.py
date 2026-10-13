"""Append-only editorial records; no media dependency edges or active-text writes."""
from hashlib import sha256
import json

from app.domain.dependencies import canonical_json


class ProjectNarrativeRecords:
    def __init__(self, repository, store):
        self.repository, self.store = repository, store
        self.project_id = repository.project().id
        if store._index is None or store._index.repository is not repository:
            raise ValueError("Narrative records require the owning project store.")

    def save(self, kind, value):
        return self.store.save_artifact("narrative_" + kind + ".json", canonical_json(value),
            {"artifact_type": "narrative_" + kind, "project_id": self.project_id,
             "module_name": "desktop_narrative_review"})

    def history(self, kind):
        return tuple(sorted((m for m in self.store.list_artifacts()
            if m.artifact_type == "narrative_" + kind and m.metadata.get("project_id") == self.project_id),
            key=lambda m: (m.created_at, m.artifact_id)))

    def read(self, manifest):
        retained = [m for m in self.store.list_artifacts() if m.artifact_id == manifest.artifact_id]
        if retained != [manifest] or manifest.metadata.get("project_id") != self.project_id:
            raise ValueError("Narrative record ownership mismatch.")
        raw = self.store.read_artifact(manifest.storage_key)
        if sha256(raw).hexdigest() != manifest.checksum:
            raise ValueError("Narrative record checksum mismatch.")
        value = json.loads(raw)
        if type(value) is not dict or value.get("version") != 1:
            raise ValueError("Malformed narrative record.")
        return value

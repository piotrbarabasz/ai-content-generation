"""Immutable Video Plan artifact revisions and linear active selection history."""

from hashlib import sha256
import json

from app.domain.dependencies import canonical_json
from app.domain.video_plan import VideoPlanRevision, VideoPlanSelection


class ProjectVideoPlans:
    REVISION = "video_plan_revision"
    SELECTION = "video_plan_selection"

    def __init__(self, repository, store):
        self.repository, self.store = repository, store
        self.project_id = repository.project().id
        if store.root.resolve() != (repository.workspace / "artifacts").resolve():
            raise ValueError("Video plan store belongs to a different project workspace.")

    def _manifest(self, kind, value_id):
        matches = [m for m in self.store.list_artifacts() if m.artifact_type == kind and m.metadata.get("value_id") == value_id]
        if len(matches) != 1 or matches[0].metadata.get("project_id") != self.project_id:
            raise ValueError("Unknown or ambiguous video plan artifact.")
        return matches[0]

    def _read(self, kind, value_id, cls):
        manifest = self._manifest(kind, value_id)
        raw = self.store.read_artifact(manifest.storage_key)
        if sha256(raw).hexdigest() != manifest.checksum:
            raise ValueError("Video plan artifact checksum mismatch.")
        value = cls.from_payload(json.loads(raw))
        if value.id != value_id or value.project_id != self.project_id:
            raise ValueError("Video plan artifact identity or project mismatch.")
        return value

    def _write(self, kind, value):
        if any(m.metadata.get("value_id") == value.id for m in self.store.list_artifacts()):
            raise ValueError("Video plan artifact identity already exists.")
        payload = canonical_json(value.to_payload())
        try:
            return self.store.save_artifact(kind + ".json", payload,
                                            {"artifact_type": kind, "project_id": self.project_id,
                                             "value_id": value.id, "module_name": "desktop_video_plan"})
        except Exception:
            # D004 can commit its index before reporting a later cleanup error.
            # Do not leave callers uncertain about whether a selection moved.
            try:
                manifest = self._manifest(kind, value.id)
                encoded = payload.encode("utf-8")
                if manifest.checksum == sha256(encoded).hexdigest() and self.store.read_artifact(manifest.storage_key) == encoded:
                    return manifest
            except Exception:
                pass
            raise

    def revision(self, revision_id):
        return self._read(self.REVISION, revision_id, VideoPlanRevision)

    def history(self):
        manifests = sorted(self.store.list_artifacts(), key=lambda m: (m.created_at, m.artifact_id))
        return tuple(self.revision(m.metadata["value_id"]) for m in manifests
                     if m.artifact_type == self.REVISION and m.metadata.get("project_id") == self.project_id)

    def save_revision(self, revision):
        if revision.project_id != self.project_id:
            raise ValueError("Video plan belongs to a different project.")
        if revision.parent_revision_id is not None:
            parent = self.revision(revision.parent_revision_id)
            if parent.project_id != revision.project_id:
                raise ValueError("Video plan lineage crosses projects.")
        self._write(self.REVISION, revision)

    def selection_history(self):
        events = [self._read(self.SELECTION, m.metadata["value_id"], VideoPlanSelection)
                  for m in self.store.list_artifacts()
                  if m.artifact_type == self.SELECTION and m.metadata.get("project_id") == self.project_id]
        children = {}
        for event in events:
            self.revision(event.revision_id)
            if event.parent_selection_id in children:
                raise ValueError("Video plan selection history branches.")
            children[event.parent_selection_id] = event
        result, parent = [], None
        while parent in children:
            event = children.pop(parent)
            result.append(event)
            parent = event.id
        if children:
            raise ValueError("Video plan selection history is incomplete.")
        return tuple(result)

    def selected_id(self):
        history = self.selection_history()
        return history[-1].id if history else None

    def selected(self):
        history = self.selection_history()
        return self.revision(history[-1].revision_id) if history else None

    def select(self, revision, *, expected_selection_id):
        retained = self.revision(revision.id)
        if retained != revision:
            raise ValueError("Selected video plan differs from retained revision.")
        active = self.selected_id()
        if active != expected_selection_id:
            raise ValueError("Active video plan selection changed; refresh before selecting.")
        event = VideoPlanSelection("video_plan_selection_" + revision.id, self.project_id, revision.id, active)
        self._write(self.SELECTION, event)
        return event

"""Immutable, parent-linked timeline snapshots on the owning project connection."""

from hashlib import sha256
import json
from uuid import uuid4

from app.application.timeline_editing import TimelineEdit
from app.domain.timeline import TimelineRevision


class ProjectTimelineEdits:
    def __init__(self, repository, store):
        if store._index is None or store._index.repository is not repository:
            raise ValueError("Timeline history requires the owning project store.")
        self.store, self.project_id = store, repository.project().id

    def current(self):
        children, ids = {}, set()
        for manifest in self.store.list_artifacts():
            if manifest.artifact_type != "desktop_timeline_edit":
                continue
            payload = self.store.read_artifact(manifest.storage_key)
            if sha256(payload).hexdigest() != manifest.checksum:
                raise ValueError("Timeline history checksum mismatch.")
            data = json.loads(payload)
            timeline = TimelineRevision.from_payload(data["timeline"])
            if (data["version"] != 1 or timeline.project_id != self.project_id
                    or manifest.metadata.get("project_id") != self.project_id
                    or manifest.metadata.get("value_id") != data["id"]
                    or data["id"] in ids or data["parent"] in children):
                raise ValueError("Invalid or branching timeline history.")
            ids.add(data["id"])
            children[data["parent"]] = TimelineEdit(data["id"], timeline)
        current, parent = None, None
        while parent in children:
            current = children.pop(parent)
            parent = current.id
        if children:
            raise ValueError("Incomplete timeline history.")
        return current

    def save(self, timeline, expected):
        previous = self.current()
        if timeline.project_id != self.project_id or (previous.id if previous else None) != expected:
            raise ValueError("Timeline changed or belongs to another project.")
        event = TimelineEdit(str(uuid4()), timeline)
        payload = json.dumps({"version": 1, "id": event.id, "parent": expected,
                              "timeline": timeline.to_payload()}, sort_keys=True)
        try:
            self.store.save_artifact("timeline-edit.json", payload,
                                     {"artifact_type": "desktop_timeline_edit", "project_id": self.project_id,
                                      "value_id": event.id, "module_name": "desktop_timeline"})
        except Exception:
            # Publication can commit before private staging cleanup fails (D004).
            try:
                committed = self.current() == event
            except Exception:
                committed = False
            if not committed:
                raise
        return event

"""Small retained project motion preference; uses the existing artifact store."""

from hashlib import sha256
import json

from app.domain.dependencies import canonical_json
from app.domain.scene_motion import MotionConfig


class ProjectMotionSettings:
    def __init__(self, store, project_id):
        self.store, self.project_id = store, project_id

    def current(self):
        history = sorted((m for m in self.store.list_artifacts()
                          if m.artifact_type == "project_motion_settings"
                          and m.metadata.get("project_id") == self.project_id),
                         key=lambda m: (m.created_at, m.artifact_id))
        if not history:
            # Opening a historical project writes nothing. Only new requests use
            # these visible defaults; retained requests keep their pinned policy.
            return MotionConfig()
        manifest = history[-1]
        raw = self.store.read_artifact(manifest.storage_key)
        if sha256(raw).hexdigest() != manifest.checksum:
            raise ValueError("Motion settings checksum mismatch.")
        return MotionConfig.from_payload(json.loads(raw))

    def save(self, config):
        if not isinstance(config, MotionConfig):
            raise ValueError("Expected motion configuration.")
        if self.current() != config:
            self.store.save_artifact("motion-settings.json", canonical_json(config.to_payload()),
                {"artifact_type": "project_motion_settings", "module_name": "desktop_motion_settings",
                 "project_id": self.project_id})
        return config

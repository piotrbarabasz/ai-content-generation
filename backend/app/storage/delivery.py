"""Independent delivery preferences, immutable reports and validated output reuse."""

from hashlib import file_digest
import json

from app.domain.delivery import DeliveryConfig
from app.domain.dependencies import canonical_json
from app.storage.branding import ProjectBranding


class ProjectDelivery:
    def __init__(self, store, project_id):
        self.store, self.project_id = store, project_id
        self.records = ProjectBranding(store, project_id)

    def current(self):
        history = self.records.history("delivery_settings")
        return DeliveryConfig.from_payload(json.loads(self.records.verified_bytes(history[-1]))) if history else DeliveryConfig()

    def save(self, config):
        if not isinstance(config, DeliveryConfig):
            raise ValueError("Expected delivery preferences.")
        if self.current() != config:
            self.store.save_artifact("delivery-settings.json", canonical_json(config.to_payload()),
                {"artifact_type": "delivery_settings", "module_name": "desktop_delivery", "project_id": self.project_id})

    def verify(self, manifest):
        if manifest.artifact_type != "delivery_video" or manifest.metadata.get("project_id") != self.project_id:
            raise ValueError("Delivery output belongs to another project or artifact type.")
        report = manifest.metadata.get("delivery", {})
        if (not report.get("fully_decoded") or report.get("checksum") != manifest.checksum
                or report.get("size_bytes") != manifest.size_bytes or report.get("status") not in {"PASS", "REVIEW", "SOURCE_COPY"}):
            raise ValueError("Delivery report differs from verified output bytes.")
        with self.store.open_artifact_id(manifest.artifact_id) as stream:
            if file_digest(stream, "sha256").hexdigest() != manifest.checksum:
                raise ValueError("Delivery output checksum mismatch.")
        if self.store._artifact_path(manifest.storage_key).stat().st_size != manifest.size_bytes:
            raise ValueError("Delivery output size mismatch.")
        return manifest

    def cached(self, fingerprint):
        for manifest in reversed(self.records.history("delivery_video")):
            if manifest.metadata.get("fingerprint") == fingerprint:
                try:
                    return self.verify(manifest)
                except (ValueError, OSError):
                    continue  # Historical damaged versions are retained, not repaired.
        return None

    def report(self, manifest):
        self.verify(manifest)
        return {**manifest.metadata["delivery"], "output_artifact_id": manifest.artifact_id}

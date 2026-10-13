"""Immutable branding settings, logo intake and independently verified outputs."""

from hashlib import file_digest, sha256
from io import BytesIO
import json

from PIL import Image

from app.domain.branding import BrandingConfig
from app.domain.dependencies import canonical_json


class ProjectBranding:
    def __init__(self, store, project_id):
        self.store, self.project_id = store, project_id

    def history(self, kind):
        return sorted((m for m in self.store.list_artifacts() if m.artifact_type == kind
                       and m.metadata.get("project_id") == self.project_id), key=lambda m: (m.created_at, m.artifact_id))

    def verified_bytes(self, manifest):
        raw = self.store.read_artifact(manifest.storage_key)
        if sha256(raw).hexdigest() != manifest.checksum or len(raw) != manifest.size_bytes:
            raise ValueError("Branding artifact failed checksum validation.")
        return raw

    def current(self):
        history = self.history("branding_settings")
        return BrandingConfig.from_payload(json.loads(self.verified_bytes(history[-1]))) if history else BrandingConfig()

    def save(self, config):
        if not isinstance(config, BrandingConfig):
            raise ValueError("Expected branding settings.")
        if config.logo_id:
            self.logo(config.logo_id)
        if self.current() != config:
            self.store.save_artifact("branding-settings.json", canonical_json(config.to_payload()),
                {"artifact_type": "branding_settings", "module_name": "desktop_branding", "project_id": self.project_id})
        return config

    def import_logo(self, path):
        # Bound intake before decoding; normalize to PNG while preserving alpha.
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("Logo PNG must be smaller than 20 MiB.")
        with Image.open(path) as image:
            if image.format != "PNG" or image.width * image.height > 16_000_000:
                raise ValueError("Logo must be a PNG of at most 16 million pixels.")
            image.load()
            output = BytesIO()
            image.convert("RGBA").save(output, format="PNG")
        return self.store.save_artifact("channel-logo.png", output.getvalue(),
            {"artifact_type": "branding_logo", "module_name": "desktop_branding", "project_id": self.project_id})

    def logo(self, artifact_id):
        manifest = next((m for m in self.history("branding_logo") if m.artifact_id == artifact_id), None)
        if manifest is None:
            raise ValueError("The selected project logo is missing.")
        raw = self.verified_bytes(manifest)
        with Image.open(BytesIO(raw)) as image:
            if image.format != "PNG":
                raise ValueError("The retained logo is not a PNG.")
            image.load()
        return manifest, raw

    def cached(self, fingerprint):
        for manifest in reversed(self.history("branded_video")):
            if manifest.metadata.get("fingerprint") == fingerprint:
                try:
                    self.verify_output(manifest)
                    return manifest
                except (ValueError, OSError):
                    continue  # Preserve damaged histories; rebuild without reusing them.
        return None

    def verify_output(self, manifest):
        evidence = manifest.metadata.get("finishing", {})
        if (not evidence.get("fully_decoded") or evidence.get("checksum") != manifest.checksum
                or evidence.get("size_bytes") != manifest.size_bytes):
            raise ValueError("Branded video has invalid measured publication evidence.")
        with self.store.open_artifact_id(manifest.artifact_id) as stream:
            if file_digest(stream, "sha256").hexdigest() != manifest.checksum:
                raise ValueError("Branded video checksum mismatch.")
        if self.store._artifact_path(manifest.storage_key).stat().st_size != manifest.size_bytes:
            raise ValueError("Branded video size mismatch.")
        return manifest

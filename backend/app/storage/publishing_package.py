"""Append-only desktop publishing revisions and checksum-verified ZIP histories."""

from hashlib import sha256
import json
import zipfile

from app.domain.dependencies import canonical_json
from app.storage.branding import ProjectBranding


class ProjectPublishing:
    def __init__(self, store, project_id):
        self.store, self.project_id = store, project_id
        self.records = ProjectBranding(store, project_id)

    def history(self, kind):
        return self.records.history("publishing_" + kind)

    def one(self, artifact_id, kind=None):
        values = [m for m in self.store.list_artifacts() if m.artifact_id == artifact_id
                  and (m.metadata.get("project_id") == self.project_id
                       or kind is None and not m.artifact_type.startswith("publishing_")
                       and m.metadata.get("project_id") is None)
                  and (kind is None or m.artifact_type == "publishing_" + kind)]
        if len(values) != 1: raise ValueError("Publishing dependency is missing or belongs to another project.")
        return values[0]

    def bytes(self, manifest):
        return self.records.verified_bytes(manifest)

    def read(self, manifest):
        return json.loads(self.bytes(manifest))

    def save(self, kind, value, **metadata):
        return self.store.save_artifact(kind + ".json", canonical_json(value),
            {"artifact_type": "publishing_" + kind, "module_name": "desktop_publishing",
             "project_id": self.project_id, **metadata})

    def latest(self):
        history = self.history("draft")
        return (history[-1], self.read(history[-1])) if history else (None, None)

    def verify_package(self, manifest):
        if manifest != self.one(manifest.artifact_id, "package"):
            raise ValueError("Package identity changed.")
        with self.store.open_artifact_id(manifest.artifact_id) as stream:
            digest = sha256()
            while block := stream.read(1024 * 1024): digest.update(block)
            if digest.hexdigest() != manifest.checksum: raise ValueError("ZIP checksum mismatch.")
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                expected = manifest.metadata["files"]
                names = archive.namelist()
                if len(names) != len(set(names)) or set(names) != set(expected):
                    raise ValueError("ZIP manifest/file list mismatch.")
                for name, details in expected.items():
                    if not name.startswith("publishing-package/") or ".." in name.split("/"):
                        raise ValueError("Unsafe package path.")
                    digest, size = sha256(), 0
                    with archive.open(name) as item:
                        while block := item.read(1024 * 1024):
                            digest.update(block); size += len(block)
                    if digest.hexdigest() != details["checksum"] or size != details["size_bytes"]:
                        raise ValueError("ZIP component checksum mismatch.")
        return manifest

    def cached(self, fingerprint):
        for manifest in reversed(self.history("package")):
            if manifest.metadata.get("fingerprint") == fingerprint:
                try: return self.verify_package(manifest)
                except (ValueError, OSError, zipfile.BadZipFile): continue
        return None

"""Append-only Short revisions; successful selections never replace parent heads."""
from hashlib import file_digest
import json
from io import BytesIO
import zipfile
from app.domain.dependencies import canonical_json
from app.storage.branding import ProjectBranding


class ProjectShorts:
    def __init__(self, store, project_id):
        self.store, self.project_id = store, project_id
        self.records = ProjectBranding(store, project_id)

    def history(self, kind): return self.records.history("shorts_" + kind)
    def read(self, m): return json.loads(self.records.verified_bytes(m))
    def save(self, kind, value, **metadata):
        return self.store.save_artifact(kind + ".json", canonical_json(value),
            {"artifact_type": "shorts_" + kind, "module_name": "desktop_shorts", "project_id": self.project_id, **metadata})
    def one(self, identity, kind):
        m = next((m for m in self.history(kind) if m.artifact_id == identity), None)
        if m is None: raise ValueError("Short dependency is missing or foreign.")
        return m
    def latest(self, kind, candidate_id):
        values = [m for m in self.history(kind) if m.metadata.get("candidate_id") == candidate_id]
        return values[-1] if values else None
    def analysis_response(self, analysis_key):
        """Only checksum-verified, explicitly scoped selection receipts are replayed."""
        for manifest in reversed(self.history('analysis_response')):
            if manifest.metadata.get('analysis_key') == analysis_key:
                value = self.read(manifest)
                if value.get('version') != 1 or value.get('analysis_key') != analysis_key:
                    raise ValueError('Invalid retained Short selection receipt.')
                return manifest, value['response']
        return None, None
    def verify(self, m):
        m = self.one(m.artifact_id, "video")
        e = m.metadata.get("shorts", {})
        if not e.get("fully_decoded") or e.get("checksum") != m.checksum or e.get("size_bytes") != m.size_bytes:
            raise ValueError("Short output lacks valid decode evidence.")
        with self.store.open_artifact_id(m.artifact_id) as stream:
            if file_digest(stream, "sha256").hexdigest() != m.checksum: raise ValueError("Short checksum mismatch.")
        if self.store._artifact_path(m.storage_key).stat().st_size != m.size_bytes: raise ValueError("Short size mismatch.")
        return m
    def completed(self, selection):
        value = self.read(selection)
        video = self.verify(self.one(value["video_id"], "video"))
        metadata = self.read(self.one(value["metadata_id"], "metadata"))
        if metadata["output_checksum"] != video.checksum or metadata["video_artifact_id"] != video.artifact_id:
            raise ValueError("Short metadata does not match selected media.")
        if value["srt_id"]: self.records.verified_bytes(self.one(value["srt_id"], "srt"))
        package = self.records.verified_bytes(self.one(value["package_id"], "package"))
        with zipfile.ZipFile(BytesIO(package)) as archive:
            expected = {"short-01/short.mp4", "short-01/metadata.json", "short-01/description.txt"}
            if value["srt_id"]: expected.add("short-01/captions.srt")
            if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected) or archive.testzip():
                raise ValueError("Short package is incomplete or damaged.")
        return video

    def cached(self, fingerprint):
        for selection in reversed(self.history("selection")):
            try:
                video = self.completed(selection)
                if video.metadata.get("fingerprint") == fingerprint: return video
            except (ValueError, OSError, KeyError, zipfile.BadZipFile): continue
        return None

    def selected(self, candidate_id):
        selection = self.latest("selection", candidate_id)
        return self.completed(selection) if selection else None

"""Detached, operation-scoped artifact reads and deferred small publications.

Only immutable manifests and paths cross the worker boundary. There is no
cross-operation verification cache, and incomplete writes retain recovery journals.
"""
from types import SimpleNamespace

from app.runtime.media_worker import check_canceled
from app.storage.artifact_index import matches_metadata
from app.storage.local_store import LocalArtifactStore


class MediaSnapshotStore(LocalArtifactStore):
    def __init__(self, store, *, repository=None):
        manifests = tuple(store.list_artifacts())
        self.catalog = {m.artifact_id: m for m in manifests}
        self.pending = []
        self.reads = {}
        self.small = {}
        self.canceled = lambda: False
        repository = repository or SimpleNamespace(workspace=store.root.parent)
        index = SimpleNamespace(root=store.root, repository=repository,
            manifests=lambda: tuple(self.catalog.values()),
            manifest_by_id=self.catalog.get,
            manifest_by_key=lambda key: next((m for m in self.catalog.values() if m.storage_key == key), None),
            find_manifests=lambda kind, filters: tuple(m for m in self.catalog.values()
                if (kind is None or m.artifact_type == kind) and matches_metadata(m, filters)))
        super().__init__(store.root, index=index)

    def import_stream(self, name, source, metadata=None):
        check_canceled(self.canceled)
        manifest, stage = self._stage_stream(name, source, metadata)
        self.pending.append((manifest, stage))
        self.catalog[manifest.artifact_id] = manifest
        return manifest

    def open_artifact(self, key):
        check_canceled(self.canceled)
        manifest = self.manifest_by_key(key)
        if manifest is None:
            raise FileNotFoundError(key)
        path = self._artifact_path(key)
        signature = self.signature(path)
        if key in self.reads and self.reads[key] != signature:
            raise ValueError("Media changed during verification; refresh the source.")
        self.reads[key] = signature
        stream = path.open('rb')
        canceled = self.canceled
        class Checked:
            def __enter__(self): return self
            def __exit__(self, *args): stream.close()
            def read(self, count=-1):
                check_canceled(canceled)
                return stream.read(count)
            def readinto(self, buffer):
                check_canceled(canceled)
                return stream.readinto(buffer)
            def __getattr__(self, name): return getattr(stream, name)
        return Checked()

    def open_artifact_id(self, artifact_id):
        manifest = self.manifest_by_id(artifact_id)
        if manifest is None:
            raise FileNotFoundError(artifact_id)
        return self.open_artifact(manifest.storage_key)

    def read_artifact(self, key):
        if key in self.small:
            check_canceled(self.canceled)
            return self.small[key]
        raw = super().read_artifact(key)
        if len(raw) <= 8 * 1024 * 1024:
            self.small[key] = raw
        return raw

    @staticmethod
    def signature(path):
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def validate_reads(self):
        check_canceled(self.canceled)
        for key, signature in self.reads.items():
            if self.signature(self._artifact_path(key)) != signature:
                raise ValueError("Media changed during preparation; refresh and retry.")

    def publish(self, live):
        """Called only on the original catalog owner, after its identity gate."""
        for manifest, stage in self.pending:
            live._commit_import(manifest, stage)
        self.pending.clear()

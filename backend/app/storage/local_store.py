"""Local filesystem artifact store."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from dataclasses import dataclass
from typing import BinaryIO

from app.domain.types import JsonDict

from .paths import contained_path, import_path, relative_key, storage_root
from .artifact_store import StreamingArtifactStore
from .artifact_index import ProjectArtifactIndex
from .manifest import ArtifactManifest, _coerce_metadata


def _normalize_prefix(prefix: str) -> str:
    return relative_key(str(prefix).replace("\\", "/").rstrip("/"), label="Artifact prefix") if prefix else ""


def _normalize_key(key: str) -> str:
    return relative_key(key)


def _content_bytes(content: bytes | str) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    raise TypeError("Artifact content must be bytes or str.")


CHUNK_SIZE = 1024 * 1024


def _publish_file(source: Path, destination: Path) -> None:
    """Atomic same-filesystem visibility with no replacement of an existing key."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows refuses an existing destination.
    else:
        os.link(source, destination)  # POSIX rename would overwrite.
        source.unlink()


def _write_json(path: Path, payload) -> None:
    with path.open("x", encoding="utf-8") as output:
        json.dump(payload, output, indent=2, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())


@dataclass(frozen=True)
class PublicationRecovery:
    publication: str
    state: str
    storage_key: str | None = None


class LocalArtifactStore(StreamingArtifactStore):
    """Immutable local publication. Serialize imports/recovery per storage root.

    Legacy roots use atomic JSON sidecars as their catalog. Project roots use a
    SQLite index tied to the open D003 session. Neither catalog advertises staging.
    """

    def __init__(self, root: Path | str, *, index: ProjectArtifactIndex | None = None) -> None:
        if index is not None:
            contained_path(index.repository.workspace, "artifacts")
        self._root = storage_root(root)
        if index is not None and index.root != self._root:
            raise ValueError("Artifact index and storage root do not match.")
        self._root.mkdir(parents=True, exist_ok=True)
        self._manifest_root = contained_path(self._root, ".artifacts")
        self._manifest_root.resolve().relative_to(self._root)
        self._manifest_root.mkdir(parents=True, exist_ok=True)
        self._index = index
        if index is None and contained_path(self._root, ".artifacts/index.sqlite").exists():
            raise ValueError("Project artifacts require LocalArtifactStore.for_project(repository).")
        self._staging = contained_path(self._root, ".artifacts/staging")
        self._staging.resolve().relative_to(self._root)
        self._staging.mkdir(exist_ok=True)
        self._publishing = False
        self.recovery_report: tuple[PublicationRecovery, ...] = ()

    @classmethod
    def for_project(cls, repository) -> "LocalArtifactStore":
        index = ProjectArtifactIndex(repository)
        store = cls(index.root, index=index)
        store.recovery_report = store.recover()
        return store

    @property
    def root(self) -> Path:
        return self._root

    def save_artifact(
        self,
        name: str,
        content: bytes | str,
        metadata: JsonDict | None = None,
    ) -> ArtifactManifest:
        return self.import_stream(name, io.BytesIO(_content_bytes(content)), metadata)

    def import_file(self, name: str, source: Path | str,
                    metadata: JsonDict | None = None, *,
                    source_root: Path | None = None) -> ArtifactManifest:
        with import_path(source, source_root).open("rb") as stream:
            return self.import_stream(name, stream, metadata)

    def import_stream(self, name: str, source: BinaryIO,
                      metadata: JsonDict | None = None) -> ArtifactManifest:
        """Read at most CHUNK_SIZE at a time; never close the caller's stream."""
        if self._publishing:
            raise RuntimeError("A publication is already running on this store.")
        if self._index is not None:
            self._index.repository.project()  # Fail before I/O if the owner is closed.
        self._publishing = True
        try:
            return self._import_stream(name, source, metadata)
        finally:
            self._publishing = False

    def _import_stream(self, name, source, metadata):
        metadata_dict = _coerce_metadata(metadata)
        manifest = ArtifactManifest.create(
            name=name,
            artifact_type=str(
                metadata_dict.get("artifact_type")
                or metadata_dict.get("artifactType")
                or Path(name).suffix.lstrip(".")
                or Path(name).name
            ),
            workflow_run_id=str(
                metadata_dict.get("workflow_run_id")
                or metadata_dict.get("workflowRunId")
                or ""
            ),
            module_name=str(metadata_dict.get("module_name") or metadata_dict.get("moduleName") or ""),
            metadata={
                key: value
                for key, value in metadata_dict.items()
                if key not in {
                    "artifact_type",
                    "artifactType",
                    "module_name",
                    "moduleName",
                    "workflow_run_id",
                    "workflowRunId",
                }
            },
            artifact_version=str(
                metadata_dict.get("artifact_version")
                or metadata_dict.get("artifactVersion")
                or metadata_dict.get("version")
                or "1"
            ),
        )
        # Serialize metadata before transferring media; freeze caller-owned nested values.
        manifest = ArtifactManifest.from_payload(json.loads(json.dumps(manifest.to_payload())))
        artifact_path = self._artifact_path(manifest.storage_key)
        stage = Path(tempfile.mkdtemp(prefix="publication-", dir=self._checked(self._staging)))
        _write_json(self._checked(stage / "intent.json"), manifest.to_payload())
        digest, size = hashlib.sha256(), 0
        with self._checked(stage / "payload").open("xb") as output:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not isinstance(chunk, bytes):
                    raise TypeError("Artifact stream must return bytes.")
                if len(chunk) > CHUNK_SIZE:
                    raise ValueError("Artifact stream exceeded the requested read size.")
                if not chunk:
                    break
                if output.write(chunk) != len(chunk):
                    raise OSError("Incomplete staged write.")
                digest.update(chunk)
                size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if self._checked(stage / "payload").stat().st_size != size:
            raise OSError("Staged file size does not match transferred bytes.")
        manifest.checksum, manifest.size_bytes = digest.hexdigest(), size
        _write_json(self._checked(stage / "complete.json"), manifest.to_payload())
        os.replace(self._checked(stage / "complete.json"), self._checked(stage / "intent.json"))  # Private journal only.
        artifact_path = self._artifact_path(manifest.storage_key)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        _publish_file(self._checked(stage / "payload"), self._artifact_path(manifest.storage_key))
        # Catalog commit is last. Failures leave a journal for deterministic recovery;
        # never delete a final file on an ambiguous commit outcome.
        if self._index is not None:
            self._index.register(manifest)
        else:
            self._write_manifest(manifest, stage)
        self._discard_stage(stage)
        return manifest

    def read_artifact(self, key: str) -> bytes:
        # Compatibility API for existing small-payload clients.
        with self.open_artifact(key) as stream:
            return stream.read()

    def _checked(self, path: Path) -> Path:
        return contained_path(self._root, path.relative_to(self._root).as_posix())

    def _artifact_path(self, key: str) -> Path:
        normalized = _normalize_key(key)
        if normalized.split("/", 1)[0].casefold() == ".artifacts":
            raise ValueError("Artifact key cannot access the private catalog.")
        return contained_path(self._root, normalized)

    def open_artifact(self, key: str) -> BinaryIO:
        normalized = _normalize_key(key)
        if not any(manifest.storage_key == normalized for manifest in self.list_artifacts()):
            raise FileNotFoundError(key)
        return self._artifact_path(normalized).open("rb")

    def open_artifact_id(self, artifact_id: str) -> BinaryIO:
        """IDs are opaque and owned by this catalog, never filesystem references."""
        manifest = next((m for m in self.list_artifacts() if m.artifact_id == artifact_id), None)
        if manifest is None:
            raise FileNotFoundError(artifact_id)
        return self._artifact_path(manifest.storage_key).open("rb")

    def list_artifacts(self, prefix: str = "") -> tuple[ArtifactManifest, ...]:
        normalized_prefix = _normalize_prefix(prefix)
        manifests = []
        for manifest in self._registered():
            if normalized_prefix and not manifest.storage_key.startswith(normalized_prefix):
                continue
            manifests.append(manifest)
        return tuple(sorted(manifests, key=lambda manifest: manifest.storage_key))

    def _registered(self) -> tuple[ArtifactManifest, ...]:
        if self._index is not None:
            return self._index.manifests()
        return tuple(ArtifactManifest.from_payload(json.loads(self._checked(path).read_text(encoding="utf-8")))
                     for path in sorted(self._checked(self._manifest_root).glob("*.json")))

    def _write_manifest(self, manifest: ArtifactManifest, stage: Path) -> None:
        manifest_path = self._manifest_root / f"{manifest.artifact_id}.json"
        self._checked(manifest_path)
        _write_json(self._checked(stage / "manifest.json"), manifest.to_payload())
        _publish_file(self._checked(stage / "manifest.json"), self._checked(manifest_path))

    def _discard_stage(self, stage: Path) -> None:
        # Only remove known files in this private staging directory, never recurse.
        self._checked(stage).relative_to(self._staging)
        for name in ("payload", "intent.json", "complete.json", "manifest.json"):
            self._checked(stage / name).unlink(missing_ok=True)
        self._checked(stage).rmdir()

    def recover(self) -> tuple[PublicationRecovery, ...]:
        """Clean incomplete staging; report unindexed finals without adopting/deleting them.

        Run only with no active imports. Project startup holds the D003 writer guard.
        This scans publication journals, not all workspace files or retained artifacts.
        """
        if self._publishing:
            raise RuntimeError("Cannot recover during publication.")
        registered = {manifest.storage_key: manifest for manifest in self._registered()}
        report = []
        for stage in sorted(self._checked(self._staging).glob("publication-*")):
            key = None
            try:
                self._checked(stage).relative_to(self._staging)
                if not stage.is_dir():
                    continue
                manifest = ArtifactManifest.from_payload(json.loads(self._checked(stage / "intent.json").read_text(encoding="utf-8")))
                key = manifest.storage_key
                final = self._artifact_path(key)
                stored = registered.get(key)
                if stored is not None:
                    if stored != manifest or not final.is_file():
                        state = "inconsistent"
                    else:
                        checksum = hashlib.sha256()
                        with final.open("rb") as stream:
                            while chunk := stream.read(CHUNK_SIZE):
                                checksum.update(chunk)
                        if checksum.hexdigest() != stored.checksum or final.stat().st_size != stored.size_bytes:
                            state = "inconsistent"
                        else:
                            self._discard_stage(stage)
                            state = "committed"
                elif final.exists():
                    # May also be a collision with another owner's file. Preserve it.
                    state = "orphan"
                else:
                    self._discard_stage(stage)
                    state = "discarded"
            except (ValueError, KeyError, OSError):
                # A torn intent is quarantined in place, never auto-adopted.
                state = "invalid"
            report.append(PublicationRecovery(stage.name, state, key))
        return tuple(report)

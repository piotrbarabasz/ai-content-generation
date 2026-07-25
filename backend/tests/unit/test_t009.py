from __future__ import annotations

import json
import shutil
from pathlib import Path
from contextlib import contextmanager

import pytest

from app.storage.local_store import LocalArtifactStore
from app.storage.manifest import ArtifactManifest


@contextmanager
def _workspace_tempdir(name: str):
    root = Path(__file__).resolve().parents[3] / ".tmp" / name
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_artifact_manifest_round_trips_through_payload() -> None:
    manifest = ArtifactManifest.create(
        name="script.txt",
        artifact_type="script",
        workflow_run_id="workflow_run_1",
        module_name="scriptGeneration",
        metadata={"language": "en", "version": 3},
        artifact_version="2",
        checksum="abc123",
        size_bytes=42,
    )

    payload = manifest.to_payload()
    encoded = json.dumps(payload)
    restored = ArtifactManifest.from_payload(payload)

    assert manifest.artifact_id == restored.artifact_id
    assert manifest.name == restored.name
    assert manifest.artifact_type == restored.artifact_type
    assert manifest.workflow_run_id == restored.workflow_run_id
    assert manifest.module_name == restored.module_name
    assert manifest.artifact_version == restored.artifact_version
    assert manifest.checksum == restored.checksum
    assert manifest.size_bytes == restored.size_bytes
    assert manifest.metadata == restored.metadata
    assert manifest.storage_key == restored.storage_key
    assert "\"artifact_type\": \"script\"" in encoded


def test_local_artifact_store_saves_reads_and_lists_artifacts() -> None:
    with _workspace_tempdir("test_t009_store") as store_root:
        store = LocalArtifactStore(store_root)

        first = store.save_artifact(
            "manifest.json",
            "{\"ok\": true}",
            metadata={
                "workflow_run_id": "workflow_run_1",
                "module_name": "export",
                "artifact_type": "manifest",
                "kind": "bundle",
            },
        )
        second = store.save_artifact(
            "script.txt",
            "A campaign script.",
            metadata={
                "workflowRunId": "workflow_run_1",
                "moduleName": "scriptGeneration",
                "artifactType": "script",
            },
        )

        first_path = store_root / first.storage_key
        second_path = store_root / second.storage_key

        assert first.storage_key.endswith("manifest.json")
        assert second.storage_key.endswith("script.txt")
        assert not Path(first.storage_key).is_absolute()
        assert first_path.read_text(encoding="utf-8") == "{\"ok\": true}"
        assert second_path.read_text(encoding="utf-8") == "A campaign script."
        assert store.read_artifact(first.storage_key) == b"{\"ok\": true}"
        assert store.read_artifact(second.storage_key) == b"A campaign script."

        manifests = store.list_artifacts()
        manifest_keys = [manifest.storage_key for manifest in manifests]
        assert manifest_keys == sorted(manifest_keys)
        assert {manifest.artifact_type for manifest in manifests} == {"manifest", "script"}
        assert any(manifest.metadata == {"kind": "bundle"} for manifest in manifests)
        assert store.list_artifacts(prefix=first.storage_key.rsplit("/", 1)[0]) == (
            next(manifest for manifest in manifests if manifest.storage_key == first.storage_key),
        )


def test_local_artifact_store_rejects_path_traversal_keys() -> None:
    with _workspace_tempdir("test_t009_traversal") as store_root:
        store = LocalArtifactStore(store_root)

        with pytest.raises(ValueError, match="Artifact key cannot traverse directories"):
            store.read_artifact("../secret.txt")

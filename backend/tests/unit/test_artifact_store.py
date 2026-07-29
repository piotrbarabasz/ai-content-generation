from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

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


def test_local_artifact_store_persists_content_and_manifest_sidecar() -> None:
    with _workspace_tempdir("test_artifact_store_persistence") as store_root:
        store = LocalArtifactStore(store_root)

        manifest = store.save_artifact(
            "exports/script.txt",
            "Hello from the artifact store.",
            metadata={
                "workflow_run_id": "workflow_run_42",
                "module_name": "scriptGeneration",
                "artifact_type": "script",
                "quality": "draft",
            },
        )

        artifact_path = store.root / manifest.storage_key
        manifest_path = store.root / ".artifacts" / f"{manifest.artifact_id}.json"
        saved_manifest = ArtifactManifest.from_payload(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )

        assert artifact_path.read_text(encoding="utf-8") == "Hello from the artifact store."
        assert store.read_artifact(manifest.storage_key) == b"Hello from the artifact store."
        assert manifest.storage_key.startswith("workflow_run_42/scriptGeneration/")
        assert manifest.storage_key.endswith("script.txt")
        assert manifest.metadata == {"quality": "draft"}
        assert saved_manifest == manifest
        assert store.list_artifacts() == (manifest,)
        assert store.list_artifacts(prefix="workflow_run_42/scriptGeneration") == (manifest,)


def test_local_artifact_store_rejects_invalid_keys_and_prefixes() -> None:
    with _workspace_tempdir("test_artifact_store_invalid_inputs") as store_root:
        store = LocalArtifactStore(store_root)

        with pytest.raises(ValueError, match="Artifact key is required"):
            store.read_artifact("")

        with pytest.raises(ValueError, match="Artifact key cannot traverse directories"):
            store.read_artifact("../secret.txt")

        with pytest.raises(ValueError, match="Artifact prefix cannot traverse directories"):
            store.list_artifacts("../secret")

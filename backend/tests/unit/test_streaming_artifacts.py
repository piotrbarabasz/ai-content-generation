"""Bounded I/O, immutable keys and compatibility of local publication."""

import hashlib
import io
import json
from pathlib import Path

import pytest

from app.providers.mock_storage import MockStorageProvider
from app.storage.artifact_store import ArtifactStore, StreamingArtifactStore
from app.storage import local_store, manifest as manifest_module
from app.storage.local_store import CHUNK_SIZE, LocalArtifactStore
from app.storage.manifest import ArtifactManifest


class GuardedStream(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.read_sizes = []

    def read(self, size=-1):
        assert 0 < size <= CHUNK_SIZE, "Unbounded media read"
        self.read_sizes.append(size)
        return super().read(size)


def test_streaming_is_optional_for_existing_stores(tmp_path):
    assert isinstance(MockStorageProvider(), ArtifactStore)
    assert not isinstance(MockStorageProvider(), StreamingArtifactStore)
    assert isinstance(LocalArtifactStore(tmp_path), StreamingArtifactStore)


def test_multichunk_stream_hashes_actual_bytes_and_retains_caller_ownership(tmp_path):
    data = b"frame" * (CHUNK_SIZE // 2)
    source = GuardedStream(data)
    store = LocalArtifactStore(tmp_path)
    result = store.import_stream("preview.mp4", source, {"quality": "preview"})
    assert len(source.read_sizes) >= 4
    assert not source.closed
    assert result.size_bytes == len(data)
    assert result.checksum == hashlib.sha256(data).hexdigest()
    with store.open_artifact(result.storage_key) as published:
        assert hashlib.file_digest(published, "sha256").hexdigest() == result.checksum
    assert not list(store._staging.iterdir())


def test_file_import_uses_bounded_reads_without_read_bytes(tmp_path, monkeypatch):
    source = tmp_path / "large input.mp4"
    block = b"x" * CHUNK_SIZE
    with source.open("wb") as output:
        for _ in range(3):
            output.write(block)
        output.write(b"tail")
    real_open = Path.open
    reads = []

    class Reader:
        def __init__(self, file): self.file = file
        def __enter__(self): return self
        def __exit__(self, *_args): self.file.close()
        def read(self, size=-1):
            assert 0 < size <= CHUNK_SIZE
            reads.append(size)
            return self.file.read(size)

    def guarded_open(path, *args, **kwargs):
        file = real_open(path, *args, **kwargs)
        return Reader(file) if path == source else file

    def forbidden(*_args):
        pytest.fail("Streaming file import called Path.read_bytes")

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    store = LocalArtifactStore(tmp_path / "store")
    result = store.import_file("movie.mp4", source)
    assert len(reads) == 5
    assert result.size_bytes == 3 * CHUNK_SIZE + 4
    assert result.checksum == hashlib.sha256(block * 3 + b"tail").hexdigest()


def test_repeated_names_are_distinct_and_collision_never_overwrites(tmp_path, monkeypatch):
    store = LocalArtifactStore(tmp_path)
    monkeypatch.setattr(manifest_module, "new_id", lambda _prefix: "artifact_fixed")
    first = store.save_artifact("voice.wav", b"first")
    with pytest.raises(FileExistsError):
        store.save_artifact("voice.wav", b"second")
    assert store.read_artifact(first.storage_key) == b"first"
    assert store.list_artifacts() == (first,)
    assert store.recover()[0].state == "inconsistent"
    assert store.read_artifact(first.storage_key) == b"first"
    monkeypatch.undo()
    second = store.save_artifact("voice.wav", b"second")
    assert second.artifact_id != first.artifact_id
    assert second.storage_key != first.storage_key
    assert len(store.list_artifacts()) == 2


@pytest.mark.parametrize("name", ["../../escape.wav", r"C:\secret\file.wav", r"file.a\..\..\escape", "file.wav:stream"])
def test_generated_keys_remain_inside_root(tmp_path, name):
    store = LocalArtifactStore(tmp_path / "store")
    result = store.save_artifact(name, b"safe")
    (store.root / result.storage_key).resolve().relative_to(store.root)
    assert "\\" not in result.storage_key
    assert ":" not in result.storage_key
    assert store.read_artifact(result.storage_key) == b"safe"


@pytest.mark.parametrize("failure", ["transfer", "invalid-stream", "before-move", "metadata"])
def test_failed_publication_never_advertises_partial_media(tmp_path, monkeypatch, failure):
    store = LocalArtifactStore(tmp_path)

    class BrokenStream(GuardedStream):
        def read(self, size=-1):
            if self.tell():
                if failure == "invalid-stream":
                    return None
                raise OSError("injected transfer failure")
            return super().read(size)

    def fail(*_args): raise OSError("injected publication failure")

    if failure == "before-move":
        monkeypatch.setattr(local_store, "_publish_file", fail)
    elif failure == "metadata":
        monkeypatch.setattr(store, "_write_manifest", fail)
    source = BrokenStream(b"x" * (CHUNK_SIZE + 7)) if failure in ("transfer", "invalid-stream") else io.BytesIO(b"whole")
    with pytest.raises((OSError, TypeError)):
        store.import_stream("result.wav", source)
    assert store.list_artifacts() == ()
    stage = next(store._staging.iterdir())
    intent = json.loads((stage / "intent.json").read_text())
    with pytest.raises(FileNotFoundError):
        store.read_artifact(intent["storage_key"])
    recovery = store.recover()
    assert recovery[0].state == ("orphan" if failure == "metadata" else "discarded")
    assert store.recover() == (recovery if failure == "metadata" else ())


def test_old_manifest_roots_remain_readable_and_small_payloads_compatible(tmp_path):
    original = ArtifactManifest.create(name="script.txt", artifact_type="script", checksum=hashlib.sha256(b"old").hexdigest(), size_bytes=3)
    file = tmp_path / original.storage_key
    file.parent.mkdir(parents=True)
    file.write_bytes(b"old")
    manifests = tmp_path / ".artifacts"
    manifests.mkdir()
    (manifests / f"{original.artifact_id}.json").write_text(json.dumps(original.to_payload()))
    store = LocalArtifactStore(tmp_path)
    assert store.read_artifact(original.storage_key) == b"old"
    added = store.save_artifact("new.txt", "Zażółć", {"moduleName": "scriptGeneration"})
    assert store.read_artifact(added.storage_key) == "Zażółć".encode()
    assert {item.artifact_id for item in store.list_artifacts()} == {original.artifact_id, added.artifact_id}


def test_torn_or_unsafe_recovery_intent_is_reported_without_touching_external_file(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    external = tmp_path / "keep.txt"
    external.write_text("keep")
    stage = store._staging / "publication-torn"
    stage.mkdir()
    (stage / "intent.json").write_text("{")
    assert store.recover()[0].state == "invalid"
    manifest = ArtifactManifest.create(name="bad", artifact_type="bad", storage_key="../keep.txt")
    (stage / "intent.json").write_text(json.dumps(manifest.to_payload()))
    assert store.recover()[0].state == "invalid"
    assert external.read_text() == "keep"

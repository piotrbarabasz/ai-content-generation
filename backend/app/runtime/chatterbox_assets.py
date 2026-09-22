"""Explicit offline intake of pinned public model assets into configured storage."""

import hashlib
import os
from pathlib import Path
import shutil
from uuid import uuid4

from .chatterbox_profile import MODEL_FILES, MODEL_REVISION, profile_fingerprint
from .model_index import _plain
from .provisioning import _install_lock
from app.storage.paths import contained_path


class ChatterboxAssets:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def _verified(self, directory):
        _plain(directory)
        names = {name for name, _, _ in MODEL_FILES}
        derived = {"models--ResembleAI--chatterbox"} if "Cangjie5_TC.json" in names else set()
        if {p.name for p in directory.iterdir()} != names | derived:
            raise ValueError("Chatterbox model directory has missing or unexpected files.")
        for name, size, digest in MODEL_FILES:
            path = contained_path(directory, name)
            _plain(path)
            if not path.is_file() or path.stat().st_size != size:
                raise ValueError("Chatterbox model size mismatch: " + name)
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError("Chatterbox model checksum mismatch: " + name)
        if derived:
            hub = directory / "models--ResembleAI--chatterbox"
            ref = hub / "refs" / "main"
            cached = hub / "snapshots" / MODEL_REVISION / "Cangjie5_TC.json"
            for path in (hub, hub / "refs", hub / "snapshots", hub / "snapshots" / MODEL_REVISION,
                         ref, cached):
                _plain(path)
            if ref.read_text(encoding="ascii") != MODEL_REVISION or cached.read_bytes() != (directory / "Cangjie5_TC.json").read_bytes():
                raise ValueError("Chatterbox Cangjie tokenizer cache differs from the pinned model asset.")
        return directory

    def installed(self):
        directory = contained_path(self.root, profile_fingerprint())
        if not directory.exists():
            return None
        return self._verified(directory)

    def install(self, source, *, canceled=lambda: False, progress=lambda name, done, total: None):
        """Source.open(pin) returns bytes; no network, model load or implicit download.

        Verified completed versions are immutable. Failed/canceled candidates are
        retained but never selected. A Hugging Face cache is a valid explicit source;
        its bytes are checked against the shipped upstream hashes on every intake.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        _plain(self.root)
        with _install_lock(self.root):
            existing = self.installed()
            if existing is not None:
                return existing
            if shutil.disk_usage(self.root).free < sum(size for _, size, _ in MODEL_FILES) + 64 * 1024 * 1024:
                raise ValueError("Insufficient space for Chatterbox models and safety reserve.")
            directory = contained_path(self.root, "candidate-" + uuid4().hex)
            directory.mkdir()
            for name, size, digest in MODEL_FILES:
                count, checksum = 0, hashlib.sha256()
                # Minimal source contract: filenames are fixed shipped basenames.
                with source.open(name) as incoming, (directory / name).open("xb") as output:
                    while True:
                        if canceled():
                            raise InterruptedError("Chatterbox model intake canceled; candidate retained.")
                        chunk = incoming.read(min(1024 * 1024, size - count + 1))
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > size:
                            raise ValueError("Chatterbox model exceeds pinned size: " + name)
                        checksum.update(chunk)
                        output.write(chunk)
                        progress(name, count, size)
                    output.flush()
                    os.fsync(output.fileno())
                if count != size or checksum.hexdigest() != digest:
                    raise ValueError("Chatterbox model checksum/size mismatch: " + name)
            # The upstream tokenizer asks huggingface_hub for this already pinned
            # file. Materialize the exact offline cache layout so that the worker
            # never attempts a network lookup even though it uses from_local().
            if "Cangjie5_TC.json" in {name for name, _, _ in MODEL_FILES}:
                hub = directory / "models--ResembleAI--chatterbox"
                (hub / "refs").mkdir(parents=True)
                snapshot = hub / "snapshots" / MODEL_REVISION
                snapshot.mkdir(parents=True)
                (hub / "refs" / "main").write_text(MODEL_REVISION, encoding="ascii")
                shutil.copyfile(directory / "Cangjie5_TC.json", snapshot / "Cangjie5_TC.json")
            if canceled():
                raise InterruptedError("Chatterbox activation canceled; complete candidate retained.")
            destination = contained_path(self.root, profile_fingerprint())
            os.replace(directory, destination)
            return self._verified(destination)


class ChatterboxDirectorySource:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    def open(self, name):
        if name not in {item[0] for item in MODEL_FILES}:
            raise ValueError("Unknown pinned Chatterbox asset.")
        # HF snapshot entries may be symlinks into its blob cache. Only the input
        # is followed; the managed destination consists of copied verified files.
        return (self.directory / name).open("rb")

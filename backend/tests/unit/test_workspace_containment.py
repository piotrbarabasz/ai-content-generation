"""D044: real filesystem boundaries, offline Windows capabilities and ownership."""

from contextlib import contextmanager
import io
import os
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest

from app.application.projects import ProjectSession
from app.jobs.repository import JobRepository
from app.storage.local_store import LocalArtifactStore
from app.storage.paths import contained_path, import_path
from app.storage.project_repository import ProjectRepository
from app.tts.post_processing import AudioPostProcessingError, process_pcm_wav_tempo


@pytest.fixture(params=["symlink", "junction"])
def redirect(request):
    @contextmanager
    def make(link, target):
        try:
            if request.param == "junction":
                if os.name != "nt":
                    pytest.skip("Windows junctions unavailable on this OS")
                import _winapi
                _winapi.CreateJunction(str(target), str(link))
            else:
                link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows symlink privilege unavailable (1314)")
            raise
        assert link.resolve() == target.resolve()
        try:
            yield
        finally:
            # Remove only the link itself; never recurse through a junction.
            if request.param == "junction":
                link.rmdir()
            else:
                link.unlink()
    return make


@pytest.mark.parametrize("key", [
    "../secret", "safe/../../secret", r"safe\..\secret", "/secret",
    r"C:\secret", "C:secret", r"\\server\share\secret", r"\\?\C:\secret",
    r"\\.\NUL", "safe/file.wav:secret", "safe/file::$DATA", "safe/CON.txt",
    "safe/NUL", "safe/LPT1.wav", "safe/COM¹", "safe /../secret", "safe./file",
    "safe/file.", "safe/file ", "safe/\x00file", "safe//file", "safe/./file",
])
def test_untrusted_keys_are_rejected_before_any_filesystem_probe(tmp_path, monkeypatch, key):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid key touched the filesystem")
    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(Path, "lstat", forbidden)
    with pytest.raises(ValueError):
        contained_path(tmp_path, key)


def test_unicode_spaces_and_shell_metacharacters_are_plain_paths(tmp_path):
    root = tmp_path / "Zażółć gęślą & (media)"
    root.mkdir()
    source = root / "Źródło głosu.wav"
    source.write_bytes(b"audio")
    store = LocalArtifactStore(root / "Mój projekt")
    artifact = store.import_file(source.name, source.name, source_root=root)
    with store.open_artifact_id(artifact.artifact_id) as stream:
        assert stream.read() == b"audio"
    assert source.read_bytes() == b"audio"


@pytest.mark.parametrize("selection", ["../secret", r"..\secret", "C:secret", "voice.wav:stream"])
def test_rooted_import_refuses_unsafe_selections(tmp_path, selection):
    store = LocalArtifactStore(tmp_path / "store")
    with pytest.raises(ValueError):
        store.import_file("voice.wav", selection, source_root=tmp_path)
    assert store.list_artifacts() == ()


def test_rooted_import_rejects_external_absolute_file(tmp_path):
    root = tmp_path / "selected"
    root.mkdir()
    secret = tmp_path / "secret.wav"
    secret.write_bytes(b"private")
    with pytest.raises(ValueError):
        import_path(secret, root)


def test_unknown_and_foreign_ids_never_become_file_paths(tmp_path, monkeypatch):
    with ProjectSession.create(tmp_path / "one", name="one", repository_factory=ProjectRepository) as one:
        with ProjectSession.create(tmp_path / "two", name="two", repository_factory=ProjectRepository) as two:
            first = LocalArtifactStore.for_project(one.repository)
            second = LocalArtifactStore.for_project(two.repository)
            artifact = first.save_artifact("private.txt", b"private")
            def forbidden(*args):
                pytest.fail("Unknown identity reached path resolution")
            monkeypatch.setattr(second, "_artifact_path", forbidden)
            for identity in (artifact.artifact_id, "../secret", r"C:\secret", "missing"):
                with pytest.raises(FileNotFoundError):
                    second.open_artifact_id(identity)
            with pytest.raises(FileNotFoundError):
                second.open_artifact(artifact.storage_key)


def test_redirected_read_and_import_preserve_outside_files(tmp_path, redirect):
    store = LocalArtifactStore(tmp_path / "store")
    artifact = store.save_artifact("voice.wav", b"inside")
    path = store.root / artifact.storage_key
    original = path.parent.with_name("retained")
    path.parent.rename(original)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / path.name).write_bytes(b"private")
    with redirect(path.parent, outside):
        with pytest.raises(ValueError):
            store.open_artifact_id(artifact.artifact_id)
        with pytest.raises(ValueError):
            store.import_file("copy.wav", path, source_root=store.root)
        with pytest.raises(ValueError):
            store.import_file("copy.wav", path)
    assert (outside / path.name).read_bytes() == b"private"
    assert (original / path.name).read_bytes() == b"inside"


def test_destination_redirected_during_transfer_is_rechecked(tmp_path, redirect):
    store = LocalArtifactStore(tmp_path / "store")
    outside = tmp_path / "outside"
    outside.mkdir()
    with redirect(store.root / "workflow", outside):
        with pytest.raises(ValueError):
            store.save_artifact("voice.wav", b"audio")
    # Install a redirect only after the store's initial destination validation.
    manager = redirect(store.root / "workflow", outside)
    entered = False
    class Stream(io.BytesIO):
        def read(self, size):
            nonlocal entered
            if not entered:
                manager.__enter__()
                entered = True
            return super().read(size)
    try:
        with pytest.raises(ValueError):
            store.import_stream("voice.wav", Stream(b"audio"))
    finally:
        if entered:
            manager.__exit__(None, None, None)
    assert list(outside.iterdir()) == []
    assert store.list_artifacts() == ()


@pytest.mark.parametrize("private", [".artifacts", ".artifacts/staging"])
def test_redirected_private_directories_fail_closed(tmp_path, redirect, private):
    store = LocalArtifactStore(tmp_path / "store")
    directory = store.root / private
    directory.rename(directory.with_name(directory.name + "-retained"))
    outside = tmp_path / "outside"
    outside.mkdir()
    with redirect(directory, outside):
        with pytest.raises(ValueError):
            store.save_artifact("voice.wav", b"audio")
        with pytest.raises(ValueError):
            store.recover()
    assert list(outside.iterdir()) == []


def test_recovery_quarantines_redirected_stage_without_read_or_cleanup(tmp_path, redirect):
    store = LocalArtifactStore(tmp_path / "store")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "intent.json").write_text("private", encoding="utf-8")
    with redirect(store._staging / "publication-redirected", outside):
        report = store.recover()
        assert [(item.state, item.storage_key) for item in report] == [("invalid", None)]
    assert (outside / "intent.json").read_text() == "private"


def test_index_rechecks_parent_after_store_construction(tmp_path, redirect):
    with ProjectSession.create(tmp_path / "project", name="one", repository_factory=ProjectRepository) as session:
        store = LocalArtifactStore.for_project(session.repository)
        directory = store._index.path.parent
        directory.rename(directory.with_name("retained"))
        outside = tmp_path / "outside"
        outside.mkdir()
        with redirect(directory, outside):
            with pytest.raises(ValueError):
                store.list_artifacts()
        assert list(outside.iterdir()) == []


def test_project_open_rejects_redirected_database_before_sqlite(tmp_path, redirect, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    def forbidden(*args, **kwargs):
        pytest.fail("SQLite opened an unsafe path")
    monkeypatch.setattr("app.storage.project_repository.sqlite3.connect", forbidden)
    with redirect(workspace / "project.sqlite", outside):
        with pytest.raises(ValueError):
            ProjectRepository.open(workspace)
    with redirect(workspace / "project.sqlite-journal", outside):
        with pytest.raises(ValueError):
            ProjectRepository.open(workspace)


def test_redirected_store_root_is_not_rebound(tmp_path, redirect):
    store = LocalArtifactStore(tmp_path / "store")
    artifact = store.save_artifact("keep.txt", b"keep")
    store.root.rename(tmp_path / "retained")
    outside = tmp_path / "outside"
    outside.mkdir()
    with redirect(store.root, outside):
        with pytest.raises(ValueError):
            store.open_artifact_id(artifact.artifact_id)
        with pytest.raises(ValueError):
            store.save_artifact("new.txt", b"new")
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("database", ["jobs.sqlite", "artifacts/.artifacts/index.sqlite"])
def test_sqlite_rejects_redirected_journals(tmp_path, database):
    with ProjectSession.create(tmp_path / "project", name="one", repository_factory=ProjectRepository) as session:
        store = LocalArtifactStore.for_project(session.repository)
        jobs = JobRepository(session.repository)
        outside = tmp_path / "private"
        outside.write_bytes(b"keep")
        journal = session.repository.workspace / (database + "-journal")
        try:
            journal.symlink_to(outside)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows file symlink privilege unavailable (1314)")
            raise
        try:
            with pytest.raises(ValueError):
                if database == "jobs.sqlite":
                    jobs.jobs()
                else:
                    store.list_artifacts()
            assert outside.read_bytes() == b"keep"
        finally:
            journal.unlink()


@pytest.mark.skipif(os.name != "nt", reason="NTFS ADS requires Windows")
def test_real_ads_cannot_be_imported(tmp_path):
    source = tmp_path / "voice.wav"
    source.write_bytes(b"public")
    stream = Path(str(source) + ":private")
    stream.write_bytes(b"secret")
    store = LocalArtifactStore(tmp_path / "store")
    with pytest.raises(ValueError):
        store.import_file("copy.wav", stream)
    assert source.read_bytes() == b"public"
    assert stream.read_bytes() == b"secret"


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        writer.writeframes(b"\x00\x00" * 800)
    return output.getvalue()


def test_subprocess_paths_with_unicode_spaces_and_metacharacters_are_single_arguments(tmp_path):
    root = tmp_path / "Zażółć & (media)"
    root.mkdir()
    audio = wav_bytes()
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        assert isinstance(command, list) and not kwargs.get("shell", False)
        source = Path(command[command.index("-i") + 1])
        output = Path(command[-1])
        assert source.is_absolute() and output.is_absolute()
        source.relative_to(root)
        output.relative_to(root)
        assert source.read_bytes() == audio
        output.write_bytes(audio)
        return SimpleNamespace(returncode=0)
    result = process_pcm_wav_tempo(audio, 1.1, work_root=root, process_runner=runner,
                                   ffmpeg_locator=lambda _: "fixture-ffmpeg")
    assert result.audio_bytes == audio and len(calls) == 1


def test_subprocess_redirected_output_is_not_read(tmp_path):
    outside = tmp_path / "private.wav"
    audio = wav_bytes()
    outside.write_bytes(audio)
    def runner(command, **kwargs):
        try:
            Path(command[-1]).symlink_to(outside)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows file symlink privilege unavailable (1314)")
            raise
        return SimpleNamespace(returncode=0)
    with pytest.raises(AudioPostProcessingError):
        process_pcm_wav_tempo(audio, 1.1, work_root=tmp_path, process_runner=runner,
                              ffmpeg_locator=lambda _: "fixture-ffmpeg")
    assert outside.read_bytes() == audio

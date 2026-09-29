"""Selected published render UX never resolves a preview or arbitrary MP4."""

from contextlib import closing
from hashlib import sha256
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QFileDialog

from app.desktop.export_panel import ExportPanel


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


class FakeStore:
    def __init__(self, root, manifests, content):
        self.root = root
        self.manifests = manifests
        self.content = content
        (root / "published").mkdir(parents=True, exist_ok=True)
        (root / "published" / "chosen.mp4").write_bytes(content["chosen"])
        (root / "work" / "render").mkdir(parents=True)
        (root / "work" / "render" / "preview.mp4").write_bytes(b"proxy")
        (root / "cache" / "previews").mkdir(parents=True)
        (root / "cache" / "previews" / "latest.mp4").write_bytes(b"preview cache")

    def list_artifacts(self):
        return self.manifests

    def open_artifact_id(self, artifact_id):
        manifest = next(value for value in self.manifests if value.artifact_id == artifact_id)
        return (self.root / manifest.storage_key).open("rb")

    def _artifact_path(self, key):
        return self.root / key


def _manifest(artifact_id, content, name="chosen.mp4", *, key="published/chosen.mp4"):
    checksum = sha256(content).hexdigest()
    return SimpleNamespace(artifact_id=artifact_id, artifact_type="video_render", name=name,
        storage_key=key, checksum=checksum, size_bytes=len(content),
        metadata={"render": {"checksum": checksum, "size_bytes": len(content),
            "video_duration": [123, 2], "width": 1280, "height": 720,
            "video_codec": "h264", "audio_codec": "aac", "profile": "static-mp4-720p25-v1"}})


def _panel(tmp_path, selection):
    payload = b"selected immutable mp4 bytes"
    chosen = _manifest("chosen", payload)
    newest = _manifest("newest", b"unselected", "newest.mp4", key="published/newest.mp4")
    (tmp_path / "published").mkdir(exist_ok=True)
    (tmp_path / "published" / "newest.mp4").write_bytes(b"unselected")
    store = FakeStore(tmp_path, (chosen, newest), {"chosen": payload, "newest": b"unselected"})
    index = SimpleNamespace(selected=lambda: selection)
    from app.storage.video_render import ProjectVideoRender
    resolver = ProjectVideoRender.__new__(ProjectVideoRender)
    resolver.index, resolver.store = index, store
    panel = ExportPanel(render_resolver=resolver)
    panel.bind(SimpleNamespace(), index=index, store=store)
    return panel, store, chosen


def test_export_panel_uses_selected_artifact_and_play_and_folder_target(qt, tmp_path, monkeypatch):
    from app.desktop import export_panel
    panel, store, chosen = _panel(tmp_path, {"project:video_render": "chosen"})
    opened = []
    monkeypatch.setattr(export_panel.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    assert panel.manifest is chosen
    assert panel.state.text() == "Final video ready"
    assert "1:02" in panel.details.text()
    assert panel.play()
    assert panel.open_folder()
    assert [Path(value).resolve() for value in opened] == [
        (tmp_path / "published" / "chosen.mp4").resolve(), (tmp_path / "published").resolve()]
    assert store.open_artifact_id("chosen").read() == b"selected immutable mp4 bytes"
    panel.close()


def test_export_copy_is_byte_identical_and_keeps_source_unchanged(qt, tmp_path, monkeypatch):
    panel, store, chosen = _panel(tmp_path, {"project:video_render": "chosen"})
    destination = tmp_path / "friendly-name.mp4"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(destination), "MP4 video (*.mp4)"))
    assert panel.save_copy_as() == destination
    assert destination.read_bytes() == b"selected immutable mp4 bytes"
    assert store.open_artifact_id("chosen").read() == b"selected immutable mp4 bytes"
    assert chosen.checksum == sha256(destination.read_bytes()).hexdigest()
    panel.close()


def test_export_has_clear_empty_state_without_selected_render(qt, tmp_path):
    panel, _store, _chosen = _panel(tmp_path, {})
    assert panel.manifest is None
    assert panel.state.text() == "No selected final render."
    assert not panel.play_button.isEnabled()
    assert not panel.folder_button.isEnabled()
    assert not panel.copy_button.isEnabled()
    panel.close()


def test_export_action_rejects_changed_selected_artifact(qt, tmp_path, monkeypatch):
    from app.desktop import export_panel
    panel, store, chosen = _panel(tmp_path, {"project:video_render": "chosen"})
    opened = []
    monkeypatch.setattr(export_panel.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    store._artifact_path(chosen.storage_key).write_bytes(b"X" * len(store.content["chosen"]))
    assert not panel.play()
    assert not opened
    assert "failed verification" in panel.state.text()
    panel.close()

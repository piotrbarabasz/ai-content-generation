"""D022 Qt behavior: scene-local edits, variants and failure preservation."""

from dataclasses import replace
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from app.desktop.scene_panel import ScenePanel
from app.desktop.scene_services import ImageVariant, PromptVariant, SceneView
from app.desktop.editor import ProjectEditor
from app.desktop.__main__ import LocalProjects


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415408d763f8ffff3f0005fe02fe0def46b80000000049454e44ae426082"
)


def view(number, *, prompt="", prompt_id=None, image_id=None):
    return SceneView(f"scene-{number}", "acceptance", number, f"Scene text {number}",
                     f"Visual {number}", f"{number}.00–{number + 1}.00 s", prompt, prompt_id,
                     f"prompt-selection-{number}" if prompt_id else None,
                     (PromptVariant(prompt_id, "manual: " + prompt),) if prompt_id else (), image_id,
                     f"image-selection-{number}" if image_id else None,
                     (ImageVariant(image_id, "imported: image.png (1×1)"),) if image_id else ())


class Services:
    def __init__(self):
        self.values = [view(1, prompt="Manual one", prompt_id="prompt-1"),
                       view(2, prompt="Manual two", prompt_id="prompt-2")]
        self.calls = []
        self.fail_prompt = False

    def scenes(self, section):
        return tuple(self.values)

    def _change(self, scene_id, **values):
        index = next(i for i, value in enumerate(self.values) if value.id == scene_id)
        self.values[index] = replace(self.values[index], **values)
        return self.values[index]

    def save_prompt(self, scene_id, text):
        self.calls.append(("save_prompt", scene_id, text))
        return self._change(scene_id, prompt=text, prompt_id="manual-new",
                            prompt_selection_id="prompt-selection-new",
                            prompts=self.values[0].prompts + (PromptVariant("manual-new", "manual: " + text),))

    def regenerate_prompt(self, scene_id):
        self.calls.append(("regenerate_prompt", scene_id))
        if self.fail_prompt:
            raise RuntimeError("provider failed")
        return self._change(scene_id, prompt="Generated", prompt_id="generated",
                            prompt_selection_id="generated-selection",
                            prompts=(PromptVariant("generated", "generated: Generated"),))

    def select_prompt(self, scene_id, revision_id):
        self.calls.append(("select_prompt", scene_id, revision_id))
        return self._change(scene_id, prompt_id=revision_id)

    def import_image(self, scene_id, path):
        self.calls.append(("import_image", scene_id, path))
        return self._change(scene_id, image_id="imported", image_selection_id="image-imported",
                            images=(ImageVariant("imported", "imported: chosen.png (1×1)"),))

    def generate_image(self, scene_id, **settings):
        self.calls.append(("generate_image", scene_id, settings))
        return self._change(scene_id, image_id="generated", image_selection_id="image-generated",
                            images=(ImageVariant("generated", "generated: image.png (1×1)"),))

    def select_image(self, scene_id, artifact_id):
        self.calls.append(("select_image", scene_id, artifact_id))
        return self._change(scene_id, image_id=artifact_id)

    def image_bytes(self, artifact_id):
        return PNG


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qt):
    widget, services = ScenePanel(), Services()
    widget.bind(services)
    widget.select_section(object())
    yield widget, services
    widget.close()


def test_cards_show_independent_text_and_timing(panel):
    widget, _ = panel
    assert widget.scenes.count() == 2
    assert "1.00–2.00 s" in widget.scenes.item(0).text()
    assert "Scene text 1" in widget.source.text()
    widget.scenes.setCurrentRow(1)
    assert "Scene text 2" in widget.source.text()


def test_editing_one_prompt_does_not_touch_other_scene_or_tts(panel):
    widget, services = panel
    other = services.values[1]
    widget.prompt.setPlainText("Edited one")
    QTest.mouseClick(widget.buttons["Save prompt"], Qt.LeftButton)
    assert services.calls[-1] == ("save_prompt", "scene-1", "Edited one")
    assert services.values[1] == other
    assert all(call[0] != "tts" for call in services.calls)


def test_failed_generation_preserves_manual_text_and_selection(panel):
    widget, services = panel
    before = widget.current
    services.fail_prompt = True
    QTest.mouseClick(widget.buttons["Regenerate prompt"], Qt.LeftButton)
    assert "provider failed" in widget.status.text()
    assert widget.prompt.toPlainText() == before.prompt
    assert widget.current.prompt_id == before.prompt_id
    assert services.values[0].prompt_id == before.prompt_id


def test_import_generation_and_variant_selection_are_scene_local(panel, monkeypatch):
    widget, services = panel
    changes = []
    widget.media_changed.connect(lambda: changes.append(True))
    other = services.values[1]
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: ("chosen.png", ""))
    QTest.mouseClick(widget.buttons["Import image"], Qt.LeftButton)
    assert services.calls[-1] == ("import_image", "scene-1", "chosen.png")
    widget.width.setValue(64)
    widget.height.setValue(48)
    widget.seed.setValue(7)
    QTest.mouseClick(widget.buttons["Generate image"], Qt.LeftButton)
    assert services.calls[-1] == ("generate_image", "scene-1", {"width": 64, "height": 48, "seed": 7})
    QTest.mouseClick(widget.buttons["Select image"], Qt.LeftButton)
    assert services.calls[-1][0:2] == ("select_image", "scene-1")
    assert services.values[1] == other
    assert len(changes) == 3


def test_editor_wires_scene_factory_and_preserves_unsaved_prompt(qt, tmp_path):
    services = Services()
    widget = ProjectEditor(LocalProjects(), scene_factory=lambda session: services)
    widget.load_project(tmp_path / "project", create=True)
    try:
        widget.title.setText("Section")
        widget.text.setPlainText("Saved text")
        widget.save()
        assert widget.visuals.services is services and widget.visuals.current.id == "scene-1"
        widget.visuals.prompt.setPlainText("Unsaved visual prompt")
        widget.sections.setCurrentRow(-1)
        assert widget.sections.currentRow() == 0
        assert widget.visuals.prompt.toPlainText() == "Unsaved visual prompt"
        assert not widget.close()
    finally:
        widget.visuals.prompt_dirty = False
        widget.dirty = False
        widget.close()

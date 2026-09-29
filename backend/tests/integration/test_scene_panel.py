"""D022 Qt behavior: scene-local edits, variants and failure preservation."""

from dataclasses import replace
from io import BytesIO
import os
import threading
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QSpinBox
from PIL import Image

from app.desktop.scene_panel import ScenePanel
from app.desktop.scene_services import ImageVariant, PromptVariant, SceneView
from app.desktop.editor import ProjectEditor
from app.desktop.__main__ import LocalProjects


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415408d763f8ffff3f0005fe02fe0def46b80000000049454e44ae426082"
)


def sized_png(size):
    output = BytesIO()
    Image.new("RGB", size, (80, 100, 120)).save(output, format="PNG")
    return output.getvalue()


def test_qt_can_display_retained_webp_bytes(qt):
    output = BytesIO()
    Image.new("RGB", (32, 18), "navy").save(output, format="WEBP")
    pixmap = QPixmap()
    assert pixmap.loadFromData(output.getvalue())
    assert (pixmap.width(), pixmap.height()) == (32, 18)


def test_orientation_and_final_resolution_presets_update_exact_sizes(qt):
    panel = ScenePanel()
    assert [panel.orientation.itemText(i) for i in range(2)] == ["Landscape (16:9)", "Portrait (9:16)"]
    assert [panel.resolution.itemText(i) for i in range(4)] == ["Draft / Source", "Full HD", "QHD / 1440p", "4K UHD"]
    assert panel.orientation.currentData() == "landscape" and panel.resolution.currentData() == "fhd"
    assert panel.generation_size.text() == "640 × 360" and panel.final_size.text() == "1920 × 1080"
    assert len(panel.findChildren(QSpinBox)) == 1  # Seed only.
    panel.resolution.setCurrentIndex(0)
    assert panel.final_size.text() == "640 × 360"
    panel.resolution.setCurrentIndex(2)
    assert panel.final_size.text() == "2560 × 1440"
    panel.resolution.setCurrentIndex(3)
    assert panel.final_size.text() == "3840 × 2160"
    panel.orientation.setCurrentIndex(1)
    assert panel.generation_size.text() == "360 × 640"
    assert panel.final_size.text() == "2160 × 3840"
    panel.resolution.setCurrentIndex(2)
    assert panel.final_size.text() == "1440 × 2560"
    panel.resolution.setCurrentIndex(1)
    assert panel.final_size.text() == "1080 × 1920"
    panel.close()


def test_generator_catalog_updates_source_size_seed_and_keeps_selected_image(qt):
    class CatalogServices(Services):
        def __init__(self):
            super().__init__()
            self.values[0] = replace(self.values[0], image_id="legacy", image_selection_id="selection-1")
            self.options = (SimpleNamespace(id="local-sd15", label="Local — Stable Diffusion 1.5"),
                            SimpleNamespace(id="openai-gpt-image-2", label="OpenAI — GPT Image 2"))
            self.caps = {"local-sd15": SimpleNamespace(seeded=True),
                         "openai-gpt-image-2": SimpleNamespace(seeded=False)}

        def image_generator_options(self):
            return self.options

        def image_generator_default(self):
            return "local-sd15"

        def image_capabilities(self, generator_id=None):
            return self.caps.get(generator_id)

        def image_generation_dimensions(self, generator_id, orientation):
            return {"local-sd15": {"landscape": (640, 360), "portrait": (360, 640)},
                    "openai-gpt-image-2": {"landscape": (1280, 720), "portrait": (720, 1280)}}[generator_id][orientation]

    services = CatalogServices()
    services.image_payloads["legacy"] = sized_png((640, 360))
    panel = ScenePanel()
    panel.bind(services)
    panel.select_section(object())
    try:
        panel.seed.setValue(43)
        assert panel.image_generator.count() == 2
        assert panel.generation_size.text() == "640 × 360"
        assert panel.seed.isEnabled() and panel.seed.value() == 43
        panel.image_generator.setCurrentIndex(panel.image_generator.findData("openai-gpt-image-2"))
        assert panel.generation_size.text() == "1280 × 720"
        assert not panel.seed.isEnabled() and panel.seed.value() == 0
        panel.orientation.setCurrentIndex(1)
        assert panel.generation_size.text() == "720 × 1280"
        assert panel.current.image_id == "legacy"
        assert panel.current.image_selection_id == "selection-1"
        panel.image_generator.setCurrentIndex(panel.image_generator.findData("local-sd15"))
        assert panel.generation_size.text() == "360 × 640" and panel.seed.isEnabled()
    finally:
        panel.close()


def test_generate_passes_selected_orientation_dimensions(panel):
    widget, services = panel
    widget.orientation.setCurrentIndex(1)
    QTest.mouseClick(widget.buttons["Generate image"], Qt.LeftButton)
    assert services.calls[-1] == ("generate_image", "scene-1", {"width": 360, "height": 640, "seed": 0})


@pytest.mark.parametrize("orientation,label", [(0, "Landscape (16:9)"), (1, "Portrait (9:16)")])
def test_square_history_is_explicitly_incompatible_without_selection_mutation(panel, orientation, label):
    widget, services = panel
    services.image_payloads["legacy-square"] = sized_png((2048, 2048))
    services.values[0] = replace(services.values[0], image_id="legacy-square",
                                 image_selection_id="persisted-square-selection",
                                 images=(ImageVariant("legacy-square", "upscaled: Real-ESRGAN ×4 · 2048×2048"),))
    widget.select_section(object())
    before_calls = tuple(services.calls)
    widget.orientation.setCurrentIndex(orientation)
    assert f"Not compatible with {label}" in widget.image.text()
    assert "Selected image: 2048 × 2048 (1:1)" in widget.image.text()
    assert widget.current.image_id == "legacy-square"
    assert widget.current.image_selection_id == "persisted-square-selection"
    assert widget.image_variants.count() == 1
    assert tuple(services.calls) == before_calls


@pytest.mark.parametrize("orientation,size", [(0, (640, 360)), (1, (360, 640))])
def test_successful_orientation_generation_replaces_preview_with_exact_aspect(panel, orientation, size):
    widget, services = panel
    widget.orientation.setCurrentIndex(orientation)
    QTest.mouseClick(widget.buttons["Generate image"], Qt.LeftButton)
    assert (services.calls[-1][2]["width"], services.calls[-1][2]["height"]) == size
    assert widget.current.image_id == "generated"
    assert widget.image_variants.currentText() == f"generated: generated-image.png ({size[0]} × {size[1]})"
    assert not widget.image.pixmap().isNull()


def test_generation_timeout_keeps_the_prior_selection_and_reports_failure(panel):
    widget, services = panel
    services.image_payloads["legacy-square"] = sized_png((2048, 2048))
    services.values[0] = replace(services.values[0], image_id="legacy-square",
                                 image_selection_id="persisted-square-selection")
    widget.select_section(object())

    def timeout(*args, **kwargs):
        raise RuntimeError("Local image generation timed out after 600 s; last worker phase: inference_started.")

    services.generate_image = timeout
    QTest.mouseClick(widget.buttons["Generate image"], Qt.LeftButton)
    assert "timed out after 600 s" in widget.status.text()
    assert widget.current.image_id == "legacy-square"
    assert widget.current.image_selection_id == "persisted-square-selection"
    assert "Not compatible with Landscape (16:9)" in widget.image.text()


def test_draft_source_does_not_start_the_final_image_provider(panel):
    widget, services = panel
    calls = []
    services.upscale = SimpleNamespace(provider=SimpleNamespace(upscale=lambda request: calls.append(request)))
    widget.resolution.setCurrentIndex(widget.resolution.findData("draft"))
    widget.generate_image()
    assert services.calls[-1][0] == "generate_image"
    assert calls == []

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
        self.image_payloads = {}
        self.context = SimpleNamespace(brief="", style="", brief_revision_id=None, style_revision_id=None)

    def visual_context(self):
        return self.context

    def save_visual_context(self, brief, style):
        self.calls.append(("save_visual_context", brief, style))
        self.context = SimpleNamespace(brief=brief, style=style,
                                       brief_revision_id="brief-1", style_revision_id="style-1")
        return self.context

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
        width, height = settings["width"], settings["height"]
        self.image_payloads["generated"] = sized_png((width, height))
        return self._change(scene_id, image_id="generated", image_selection_id="image-generated",
                            images=(ImageVariant("generated", f"generated: generated-image.png ({width} × {height})"),))

    def select_image(self, scene_id, artifact_id):
        self.calls.append(("select_image", scene_id, artifact_id))
        return self._change(scene_id, image_id=artifact_id)

    def image_bytes(self, artifact_id):
        return self.image_payloads.get(artifact_id, PNG)


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
    widget.seed.setValue(7)
    QTest.mouseClick(widget.buttons["Generate image"], Qt.LeftButton)
    assert services.calls[-1] == ("generate_image", "scene-1", {"width": 640, "height": 360, "seed": 7})
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


def test_visual_context_draft_survives_navigation_and_blocks_project_loss(qt, tmp_path):
    services = Services()
    widget = ProjectEditor(LocalProjects(), scene_factory=lambda session: services)
    widget.load_project(tmp_path / "project", create=True)
    try:
        widget.visuals.film_brief.setPlainText("Unsaved brief")
        assert widget.visuals.context_dirty
        widget.tabs.setCurrentWidget(widget.script_tab)
        widget.tabs.setCurrentWidget(widget.visuals_tab)
        widget.visuals.scenes.setCurrentRow(1)
        assert widget.visuals.film_brief.toPlainText() == "Unsaved brief"
        widget.load_project(tmp_path / "other", create=True)
        assert widget.session.project.name != "other"
        assert not widget.close()
        QTest.mouseClick(widget.visuals.save_context_button, Qt.LeftButton)
        assert not widget.visuals.context_dirty
        assert services.calls[-1] == ("save_visual_context", "Unsaved brief", "")
    finally:
        widget.visuals.context_dirty = False
        widget.close()


def test_local_image_inference_runs_off_gui_thread_and_finishes_on_owner_thread(qt):
    class BackgroundServices(Services):
        def __init__(self):
            super().__init__()
            self.provider = SimpleNamespace(requires_background=True)
            self.provider.generate = self.generate
            self.generation = SimpleNamespace(provider=self.provider)
            self.gui_thread = threading.get_ident()

        def prepare_background_image(self, scene_id, **settings):
            self.calls.append(("prepare", threading.get_ident(), settings))
            return (SimpleNamespace(id="claim"), scene_id, self.provider, object()), None

        def generate(self, request):
            self.calls.append(("infer", threading.get_ident()))
            return PNG

        def finish_background_image(self, claim, scene_id, result=None, error=None):
            self.calls.append(("finish", threading.get_ident()))
            assert result == PNG and error is None
            return self._change(scene_id, image_id="generated", image_selection_id="image-generated",
                                images=(ImageVariant("generated", "generated: image.png (1×1)"),))

    services = BackgroundServices()
    widget = ScenePanel()
    widget.bind(services)
    widget.select_section(object())
    try:
        QTest.mouseClick(widget.buttons["Generate image"], Qt.LeftButton)
        for _ in range(100):
            qt.processEvents()
            if not widget.busy:
                break
            QTest.qWait(10)
        assert not widget.busy and widget.current.image_id == "generated"
        assert next(call[1] for call in services.calls if call[0] == "infer") != services.gui_thread
        assert next(call[1] for call in services.calls if call[0] == "finish") == services.gui_thread
    finally:
        widget.close()


def test_catalog_image_inference_runs_off_gui_thread_and_finishes_on_owner_thread(qt):
    class BackgroundServices(Services):
        def __init__(self):
            super().__init__()
            self.gui_thread = threading.get_ident()
            self.provider = SimpleNamespace(generate=self.generate)
            self.option = SimpleNamespace(id="openai-gpt-image-2", label="OpenAI GPT Image 2",
                                          provider=self.provider)

        def image_generator_options(self):
            return (self.option,)

        def image_generator_default(self):
            return self.option.id

        def image_generator_seeded(self, generator_id):
            return False

        def image_generation_dimensions(self, generator_id, orientation):
            return (1280, 720) if orientation == "landscape" else (720, 1280)

        def prepare_background_image(self, scene_id, **settings):
            self.calls.append(("prepare", settings))
            return (SimpleNamespace(id="claim"), scene_id, self.option.id,
                    self.provider, object(), "selection-before"), None

        def generate(self, request):
            self.calls.append(("infer", threading.get_ident()))
            return PNG

        def finish_background_image(self, claim, scene_id, result=None, error=None,
                                    *, generator_id=None, expected_selection_id=...):
            self.calls.append(("finish", threading.get_ident(), generator_id, expected_selection_id))
            assert result == PNG and error is None
            return self._change(scene_id, image_id="generated", image_selection_id="generated-selection",
                                images=(ImageVariant("generated", "generated: image.png (1×1)"),))

    services = BackgroundServices()
    widget = ScenePanel()
    widget.bind(services)
    widget.select_section(object())
    try:
        QTest.mouseClick(widget.buttons["Generate image"], Qt.LeftButton)
        for _ in range(100):
            qt.processEvents()
            if not widget.busy:
                break
            QTest.qWait(10)
        assert not widget.busy and widget.current.image_id == "generated"
        assert next(call[1] for call in services.calls if call[0] == "infer") != services.gui_thread
        finish = next(call for call in services.calls if call[0] == "finish")
        assert finish[1] == services.gui_thread
        assert finish[2:] == ("openai-gpt-image-2", "selection-before")
    finally:
        widget.close()

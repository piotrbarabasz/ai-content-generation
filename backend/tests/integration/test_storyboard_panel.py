"""Offline behavior and long-list smoke checks for the project storyboard."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.desktop.storyboard_panel import StoryboardPanel


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def _png():
    image = QImage(32, 20, QImage.Format_RGB32)
    image.fill(Qt.blue)
    data = QByteArray()
    buffer = QBuffer(data)
    assert buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(data)


def _view(i, *, image=True, label="generated: GPT Image 2 · 1280×720"):
    variants = (SimpleNamespace(id=f"image-{i}", label=label),) if image else ()
    return SimpleNamespace(id=f"scene-{i}", index=i, time_label=f"{i * 6:.2f}–{(i + 1) * 6:.2f} s",
        text=f"Narration excerpt for scene {i}.", prompt_id="prompt" if image else None,
        image_id=f"image-{i}" if image else None, images=variants)


class FakeServices:
    def __init__(self):
        self.calls = 0
        self.image_reads = 0

    def scenes(self, section):
        self.calls += 1
        return section.views

    def image_bytes(self, _artifact):
        self.image_reads += 1
        return _png()


def test_social_storyboard_thumbnail_and_empty_image_state(qt):
    service = FakeServices()
    panel = StoryboardPanel()
    panel.bind(service)
    section = SimpleNamespace(section_id="social", title="Opening hook",
                              views=tuple(_view(i, image=i != 5) for i in range(1, 6)))
    panel.set_section(section)
    assert panel.scene_count == 5
    assert service.calls == 1 and service.image_reads == 4
    assert panel._cards["scene-1"][1].pixmap() is not None
    assert "No image" in panel._cards["scene-5"][1].text()
    assert "6.00" in panel._cards["scene-1"][2].text()
    assert "GPT Image 2" in panel._cards["scene-1"][4].text()
    panel.close()


def test_standard_storyboard_populates_sixty_cards_and_selection_does_not_reload(qt):
    service = FakeServices()
    panel = StoryboardPanel()
    panel.bind(service)
    section = SimpleNamespace(section_id="standard", title="Chapter 1",
        views=tuple(_view(i, label=("final: Full HD · 1920×1080" if i == 1 else
                                   "generated: Local SD 1.5 · 512×512"))
                    for i in range(1, 61)))
    selected = []
    panel.scene_selected.connect(selected.append)
    panel.set_section(section)
    assert panel.scene_count == 60
    assert len(panel._cards) == 60
    assert service.calls == 1 and service.image_reads == 60
    card = panel._cards["scene-37"][0]
    panel.set_section(section)
    assert panel._cards["scene-37"][0] is card
    assert service.image_reads == 60
    QTest.mouseClick(card, Qt.LeftButton)
    assert selected == ["scene-37"]
    assert service.calls == 2 and service.image_reads == 60
    assert "Local SD 1.5" in panel._cards["scene-37"][4].text()
    assert "Final ready" in panel._cards["scene-1"][4].text()
    panel.close()


def test_storyboard_open_does_not_call_generation(qt):
    service = FakeServices()
    service.generate_image = lambda *_args, **_kwargs: pytest.fail("Storyboard must stay read-only")
    panel = StoryboardPanel()
    panel.bind(service)
    panel.set_section(SimpleNamespace(section_id="social", title="Fact", views=(_view(1),)))
    assert panel.scene_count == 1
    panel.close()

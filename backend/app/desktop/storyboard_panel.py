"""Read-only, bounded-thumbnail storyboard list for scene navigation."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)


class _SceneCard(QFrame):
    def __init__(self, selected, callback):
        super().__init__()
        self._callback = callback
        self.setFrameShape(QFrame.StyledPanel)
        self.setProperty("selected", selected)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def mousePressEvent(self, event):
        self._callback()
        super().mousePressEvent(event)


class StoryboardPanel(QWidget):
    scene_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.services = None
        self._section = None
        self._views = ()
        self._selected_scene_id = None
        self._cards = {}
        self._card_views = {}
        self._thumbnail_cache = {}
        root = QVBoxLayout(self)
        self.summary = QLabel("Select a script section to see its storyboard.")
        root.addWidget(self.summary)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumWidth(300)
        self.list_root = QWidget()
        self.list_layout = QVBoxLayout(self.list_root)
        self.list_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.list_root)
        root.addWidget(self.scroll, 1)

    @property
    def scene_count(self):
        return len(self._views)

    def bind(self, services):
        self.services = services
        self._thumbnail_cache.clear()
        self.set_section(None)

    def set_section(self, section, *, diagnostics=()):
        previous_id = self._section.section_id if self._section is not None else None
        next_id = section.section_id if section is not None else None
        if previous_id != next_id:
            self._selected_scene_id = None
        previous_views = self._views
        self._section = section
        self._views = ()
        if section is not None and self.services is not None:
            try:
                self._views = tuple(self.services.scenes(section))
            except Exception as exc:
                self.summary.setText(str(exc))
        if previous_id == next_id and previous_views == self._views and self._cards:
            self.summary.setText(f"{section.title} · {len(self._views)} scenes")
            self._update_card_status(diagnostics)
            return
        self.set_scene_views(section, self._views, diagnostics=diagnostics)

    def set_scene_views(self, section, views, *, diagnostics=()):
        """Render already-resolved scene data; useful for batched coordinator reads."""
        self._section = section
        self._views = tuple(views)
        self._render(diagnostics)

    def select_scene(self, scene_id):
        if scene_id not in self._cards:
            return False
        self._selected_scene_id = scene_id
        for key, (frame, *_rest) in self._cards.items():
            frame.setProperty("selected", key == scene_id)
            frame.style().unpolish(frame)
            frame.style().polish(frame)
        return True

    def _clear(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()
        self._card_views.clear()

    def _render(self, diagnostics=()):
        self._clear()
        if self._section is None:
            self.summary.setText("Select a script section to see its storyboard.")
            return
        self.summary.setText(f"{self._section.title} · {len(self._views)} scenes")
        for view in self._views:
            self._add_card(view)
        self.list_layout.addStretch(1)
        self._update_card_status(diagnostics)

    def _add_card(self, view):
        frame = _SceneCard(view.id == self._selected_scene_id,
                           lambda sid=view.id: self._select(sid))
        row = QHBoxLayout(frame)
        thumb = QLabel("No image")
        thumb.setFixedSize(150, 84)
        thumb.setAlignment(Qt.AlignCenter)
        image_label = "No selected image"
        if view.image_id and self.services is not None:
            try:
                pixmap = self._thumbnail_cache.get(view.image_id)
                if pixmap is None:
                    decoded = QPixmap()
                    if decoded.loadFromData(self.services.image_bytes(view.image_id)):
                        pixmap = decoded.scaled(150, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        self._thumbnail_cache[view.image_id] = pixmap
                if pixmap is not None:
                    thumb.setPixmap(pixmap)
                    image_label = next((item.label for item in view.images if item.id == view.image_id), "Image selected")
                else:
                    thumb.setText("Image unavailable")
            except Exception:
                thumb.setText("Image unavailable")
        row.addWidget(thumb)
        details = QVBoxLayout()
        row.addLayout(details, 1)
        title = QLabel(f"SCENE {view.index:02d} · {view.time_label}")
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        details.addWidget(title)
        excerpt = view.text.strip().replace("\n", " ")
        excerpt = excerpt if len(excerpt) <= 160 else excerpt[:157].rsplit(" ", 1)[0] + "…"
        narrative = QLabel(f"“{excerpt}”")
        narrative.setWordWrap(True)
        details.addWidget(narrative)
        metadata = QLabel("")
        metadata.setWordWrap(True)
        details.addWidget(metadata)
        self.list_layout.addWidget(frame)
        self._cards[view.id] = (frame, thumb, title, narrative, metadata)
        self._card_views[view.id] = (view, image_label)

    def _update_card_status(self, diagnostics):
        if self._section is None:
            return
        by_scene = {d.scene_id: d for d in diagnostics
                    if d.scene_id and d.stage == "VISUALS"}
        voice_diagnostic = next((d for d in diagnostics if d.stage == "VOICE"
                                 and d.section_id == self._section.section_id), None)
        timing_diagnostic = next((d for d in diagnostics if d.stage == "SCENES"
                                  and d.section_id == self._section.section_id), None)
        for scene_id, (view, image_label) in self._card_views.items():
            voice = "Ready" if voice_diagnostic and voice_diagnostic.state == "OK" else "Pending"
            final = "Final ready" if image_label.lower().startswith("final:") else "Final pending"
            visual_diagnostic = by_scene.get(scene_id)
            visual_state = visual_diagnostic.state if visual_diagnostic else "PENDING"
            timing = timing_diagnostic.state if timing_diagnostic else "PENDING"
            state = ("READY" if voice == "Ready" and visual_state == "OK" and timing == "OK"
                     else "REVIEW" if visual_state in {"REVIEW", "REJECT"} or timing in {"REVIEW", "REJECT"}
                     else "PENDING")
            self._cards[scene_id][4].setText(
                f"Voice  {voice}     Visual  {image_label} ({visual_state})     {final}     Status  {state}")

    def _select(self, scene_id):
        self.select_scene(scene_id)
        self.scene_selected.emit(scene_id)

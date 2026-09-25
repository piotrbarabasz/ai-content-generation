"""Qt scene/prompt/image controls over an injected D022 service port."""

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)


class ImageGenerationThread(QThread):
    outcome = Signal(object, object)

    def __init__(self, provider, request, parent=None):
        super().__init__(parent)
        self.provider, self.request = provider, request

    def run(self):
        try:
            self.outcome.emit(self.provider.generate(self.request), None)
        except Exception as exc:
            self.outcome.emit(None, exc)


class ScenePanel(QWidget):
    media_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.services = self.section = self.current = None
        self.views = ()
        self.loading = self.prompt_dirty = self.context_dirty = False
        self.context_saved = ("", "")
        self.image_worker = self.image_claim = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("VISUAL CONTEXT"))
        context_form = QFormLayout()
        self.film_brief, self.visual_style = QPlainTextEdit(), QPlainTextEdit()
        for field in (self.film_brief, self.visual_style):
            field.setMaximumHeight(80)
            field.textChanged.connect(self._context_changed)
        context_form.addRow("Film brief", self.film_brief)
        context_form.addRow("Visual style", self.visual_style)
        layout.addLayout(context_form)
        self.save_context_button = QPushButton("Save visual context")
        self.save_context_button.clicked.connect(self.save_visual_context)
        layout.addWidget(self.save_context_button)
        self.scenes = QListWidget()
        self.scenes.setMaximumHeight(150)
        self.scenes.currentRowChanged.connect(self._select_row)
        layout.addWidget(self.scenes)
        self.source = QLabel("No scene selected.")
        self.source.setWordWrap(True)
        layout.addWidget(self.source)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Visual prompt")
        self.prompt.setMaximumHeight(100)
        self.prompt.textChanged.connect(self._prompt_changed)
        layout.addWidget(self.prompt)
        prompt_actions = QHBoxLayout()
        layout.addLayout(prompt_actions)
        self.prompt_variants = QComboBox()
        prompt_actions.addWidget(self.prompt_variants)
        self.buttons = {}
        self._button(prompt_actions, "Save prompt", self.save_prompt)
        self._button(prompt_actions, "Regenerate prompt", self.regenerate_prompt)
        self._button(prompt_actions, "Select prompt", self.select_prompt)
        self.image = QLabel("No selected image")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setFixedHeight(180)
        layout.addWidget(self.image)
        image_actions = QHBoxLayout()
        layout.addLayout(image_actions)
        self.image_variants = QComboBox()
        image_actions.addWidget(self.image_variants)
        self._button(image_actions, "Import image", self.import_image)
        self._button(image_actions, "Generate image", self.generate_image)
        self._button(image_actions, "Cancel image", self.cancel_image)
        self._button(image_actions, "Select image", self.select_image)
        settings = QFormLayout()
        self.width, self.height, self.seed = QSpinBox(), QSpinBox(), QSpinBox()
        for field in (self.width, self.height):
            field.setRange(1, 8192)
            field.setValue(512)
        self.seed.setRange(0, 2**31 - 1)
        settings.addRow("Width", self.width)
        settings.addRow("Height", self.height)
        settings.addRow("Seed", self.seed)
        layout.addLayout(settings)
        self.status = QLabel("Scene services are not configured.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._enable()

    def _button(self, layout, label, callback):
        button = QPushButton(label)
        button.clicked.connect(callback)
        layout.addWidget(button)
        self.buttons[label] = button

    @property
    def busy(self):
        return self.image_worker is not None

    def bind(self, services):
        if self.busy:
            raise ValueError("Wait for local image generation to finish before switching projects.")
        if self.context_dirty:
            raise ValueError("Save the visual context draft before switching projects.")
        self.services, self.section, self.current = services, None, None
        self.views = ()
        context_reader = getattr(services, "visual_context", None)
        context = context_reader() if callable(context_reader) else None
        self.loading = True
        self.film_brief.setPlainText(context.brief if context else "")
        self.visual_style.setPlainText(context.style if context else "")
        self.loading = False
        self.context_saved = (self.film_brief.toPlainText(), self.visual_style.toPlainText())
        self.context_dirty = False
        self.scenes.clear()
        self._show(None)
        if services:
            try:
                capability_reader = getattr(services, "image_capabilities", None)
                capabilities = capability_reader() if callable(capability_reader) else None
                if capabilities is not None:
                    self.width.setMaximum(capabilities.max_dimension)
                    self.height.setMaximum(capabilities.max_dimension)
                    if capabilities.supported_sizes:
                        width, height = capabilities.supported_sizes[0]
                        self.width.setValue(width)
                        self.height.setValue(height)
                    self.seed.setEnabled(capabilities.seeded)
                    if not capabilities.seeded:
                        self.seed.setValue(0)
            except Exception as exc:
                self.status.setText(str(exc))
                self._enable()
                return
        self.status.setText("Select a section." if services else "Scene services are not configured.")
        self._enable()

    def select_section(self, section):
        self.section, self.current = section, None
        self.prompt_dirty = False
        self.scenes.clear()
        if not self.services or section is None:
            self.views = ()
            self._show(None)
            self._enable()
            return
        try:
            self.views = self.services.scenes(section)
            for view in self.views:
                item = QListWidgetItem(f"Scene {view.index} · {view.time_label}")
                item.setData(Qt.UserRole, view.id)
                item.setToolTip(view.text)
                self.scenes.addItem(item)
            self.scenes.setCurrentRow(0 if self.views else -1)
            self.status.setText("Ready" if self.views else "No accepted scene plan for this saved section revision.")
        except Exception as exc:
            self.views = ()
            self.status.setText(str(exc))
        self._enable()

    def _select_row(self, row):
        if self.loading:
            return
        if self.prompt_dirty and self.current is not None:
            self.loading = True
            previous = next((i for i, value in enumerate(self.views) if value.id == self.current.id), -1)
            self.scenes.setCurrentRow(previous)
            self.loading = False
            self.status.setText("Save the prompt before switching scenes.")
            return
        self._show(self.views[row] if 0 <= row < len(self.views) else None)

    def _prompt_changed(self):
        if not self.loading and self.current is not None:
            self.prompt_dirty = self.prompt.toPlainText() != self.current.prompt
            self._enable()

    def _context_changed(self):
        if not self.loading:
            self.context_dirty = (self.film_brief.toPlainText(), self.visual_style.toPlainText()) != self.context_saved
            self._enable()

    def save_visual_context(self):
        if self.services is None or not self.context_dirty:
            return
        try:
            saved = self.services.save_visual_context(self.film_brief.toPlainText(),
                                                      self.visual_style.toPlainText())
            self.context_saved = saved.brief, saved.style
            self.context_dirty = False
            self.status.setText("Visual context saved.")
        except Exception as exc:
            self.status.setText(str(exc))
        self._enable()

    def _show(self, view):
        self.current = view
        self.loading = True
        self.source.setText("No scene selected." if view is None else
                            f"{view.text}\n\nVisual description: {view.visual_description}\nTiming: {view.time_label}")
        self.prompt.setPlainText(view.prompt if view else "")
        self.prompt_variants.clear()
        self.image_variants.clear()
        if view:
            for variant in view.prompts:
                self.prompt_variants.addItem(variant.label, variant.id)
            for variant in view.images:
                self.image_variants.addItem(variant.label, variant.id)
            self._select_combo(self.prompt_variants, view.prompt_id)
            self._select_combo(self.image_variants, view.image_id)
        self.loading = False
        self.prompt_dirty = False
        self._load_image(view.image_id if view else None)
        self._enable()

    @staticmethod
    def _select_combo(combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _load_image(self, artifact_id):
        self.image.setPixmap(QPixmap())
        self.image.setText("No selected image")
        if artifact_id and self.services:
            try:
                pixmap = QPixmap()
                if not pixmap.loadFromData(self.services.image_bytes(artifact_id)):
                    raise ValueError("Selected image cannot be decoded for display.")
                self.image.setText("")
                self.image.setPixmap(pixmap.scaled(320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            except Exception as exc:
                self.status.setText(str(exc))

    def _enable(self):
        ready = self.services is not None and self.current is not None and not self.busy
        context_available = self.services is not None and hasattr(self.services, "save_visual_context")
        self.film_brief.setEnabled(context_available and not self.busy)
        self.visual_style.setEnabled(context_available and not self.busy)
        self.save_context_button.setEnabled(context_available and self.context_dirty and not self.busy)
        self.prompt.setEnabled(ready)
        self.buttons["Save prompt"].setEnabled(ready and self.prompt_dirty and not self.context_dirty)
        self.buttons["Regenerate prompt"].setEnabled(ready and not self.prompt_dirty and not self.context_dirty)
        self.buttons["Regenerate prompt"].setText("Regenerate prompt" if ready and self.current.prompt_id else
                                                  "Generate prompt")
        self.buttons["Select prompt"].setEnabled(ready and not self.prompt_dirty and self.prompt_variants.count() > 0)
        self.buttons["Import image"].setEnabled(ready and not self.prompt_dirty)
        self.buttons["Generate image"].setEnabled(ready and not self.prompt_dirty and bool(self.current.prompt_id if ready else False))
        self.buttons["Cancel image"].setEnabled(self.busy)
        self.buttons["Select image"].setEnabled(ready and not self.prompt_dirty and self.image_variants.count() > 0)

    def _replace(self, view, message):
        self.views = tuple(view if item.id == view.id else item for item in self.views)
        self._show(view)
        self.status.setText(message)

    def _act(self, callback, message, *, media_changed=False):
        try:
            self._replace(callback(), message)
            if media_changed:
                self.media_changed.emit()
        except Exception as exc:
            self.status.setText(str(exc))
            self._enable()

    def save_prompt(self):
        if self.current:
            text = self.prompt.toPlainText()
            self._act(lambda: self.services.save_prompt(self.current.id, text), "Prompt saved and selected.")

    def regenerate_prompt(self):
        if self.current and not self.prompt_dirty and not self.context_dirty:
            self._act(lambda: self.services.regenerate_prompt(self.current.id), "Prompt regenerated and selected.")

    def select_prompt(self):
        if self.current and self.prompt_variants.currentData():
            value = self.prompt_variants.currentData()
            self._act(lambda: self.services.select_prompt(self.current.id, value), "Prompt variant selected.")

    def import_image(self):
        if not self.current:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import scene image", filter="Images (*.png *.jpg *.jpeg)")
        if path:
            self._act(lambda: self.services.import_image(self.current.id, path), "Image imported and selected.",
                      media_changed=True)

    def generate_image(self):
        if self.current:
            provider = getattr(self.services.generation, "provider", None) if hasattr(self.services, "generation") else None
            if getattr(provider, "requires_background", False):
                try:
                    pending, cached = self.services.prepare_background_image(
                        self.current.id, width=self.width.value(), height=self.height.value(), seed=self.seed.value())
                    if cached is not None:
                        self._replace(cached, "Cached image selected.")
                        self.media_changed.emit()
                        return
                    claim, scene_id, provider, request = pending
                    self.image_claim = claim, scene_id
                    self.image_worker = ImageGenerationThread(provider, request, self)
                    self.image_worker.outcome.connect(self._image_generated)
                    self.image_worker.start()
                    self.status.setText("Generating image in the managed CUDA worker…")
                    self._enable()
                except Exception as exc:
                    self.status.setText(str(exc))
            else:
                self._act(lambda: self.services.generate_image(
                    self.current.id, width=self.width.value(), height=self.height.value(), seed=self.seed.value()),
                    "Image generated and selected.", media_changed=True)

    def _image_generated(self, result, error):
        claim, scene_id = self.image_claim
        try:
            if error is not None:
                self.services.finish_background_image(claim, scene_id, error=error)
            else:
                view = self.services.finish_background_image(claim, scene_id, result=result)
                self._replace(view, "Image generated and selected.")
                self.media_changed.emit()
        except Exception as exc:
            self.status.setText(str(exc))
        finally:
            self.image_worker.wait()
            self.image_worker.deleteLater()
            self.image_worker = self.image_claim = None
            self._enable()

    def cancel_image(self):
        if self.busy:
            claim, _ = self.image_claim
            self.services.cancel_background_image(claim)
            self.status.setText("Cancel requested; waiting for local image worker cleanup.")

    def select_image(self):
        if self.current and self.image_variants.currentData():
            value = self.image_variants.currentData()
            self._act(lambda: self.services.select_image(self.current.id, value), "Image variant selected.",
                      media_changed=True)


__all__ = ["ScenePanel"]

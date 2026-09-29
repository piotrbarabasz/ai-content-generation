"""Qt scene/prompt/image controls over an injected D022 service port."""

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from app.application.image_presets import (ORIENTATIONS, RESOLUTIONS, final_dimensions,
                                           aspect_label, generation_dimensions, orientation_compatible)


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


class ImageUpscaleThread(QThread):
    outcome = Signal(object, object)

    def __init__(self, provider, request, parent=None):
        super().__init__(parent)
        self.provider, self.request = provider, request

    def run(self):
        try:
            self.outcome.emit(self.provider.upscale(self.request), None)
        except Exception as exc:
            self.outcome.emit(None, exc)


class ScenePanel(QWidget):
    media_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.services = self.section = self.current = None
        self.image_generator_options = ()
        self.views = ()
        self.loading = self.prompt_dirty = self.context_dirty = False
        self.context_saved = ("", "")
        self.image_worker = self.image_claim = None
        self.upscale_worker = self.upscale_prepared = None
        self.upscale_cancel_requested = False
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
        self.image_generator = QComboBox()
        self.image_generator.currentIndexChanged.connect(self._generator_changed)
        settings.addRow("Image generator", self.image_generator)
        self.orientation = QComboBox()
        for key, value in ORIENTATIONS.items():
            self.orientation.addItem(value["label"], key)
        self.orientation.setCurrentIndex(0)
        self.orientation.currentIndexChanged.connect(self._orientation_changed)
        self.resolution = QComboBox()
        for key, label in RESOLUTIONS.items():
            self.resolution.addItem(label, key)
        self.resolution.setCurrentIndex(1)
        self.resolution.currentIndexChanged.connect(self._update_preset_sizes)
        self.generation_size = QLabel()
        self.final_size = QLabel()
        self.seed = QSpinBox()
        self.seed.setRange(0, 2**31 - 1)
        settings.addRow("Orientation", self.orientation)
        settings.addRow("Generation size", self.generation_size)
        settings.addRow("Final resolution", self.resolution)
        settings.addRow("Final size", self.final_size)
        settings.addRow("Seed", self.seed)
        layout.addLayout(settings)
        self._button(layout, "Create final image", self.create_final_image)
        self._button(layout, "Cancel upscale", self.cancel_upscale)
        self._update_preset_sizes()
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
        return self.image_worker is not None or self.upscale_worker is not None

    def bind(self, services):
        if self.busy:
            raise ValueError("Wait for local image work to finish before switching projects.")
        if self.context_dirty:
            raise ValueError("Save the visual context draft before switching projects.")
        self.services, self.section, self.current = services, None, None
        self.image_generator.blockSignals(True)
        self.image_generator.clear()
        option_reader = getattr(services, "image_generator_options", None)
        self.image_generator_options = tuple(option_reader()) if callable(option_reader) else ()
        for option in self.image_generator_options:
            self.image_generator.addItem(option.label, option.id)
        default_reader = getattr(services, "image_generator_default", None)
        default_id = default_reader() if callable(default_reader) else None
        default_index = self.image_generator.findData(default_id) if default_id else -1
        self.image_generator.setCurrentIndex(default_index)
        self.image_generator.blockSignals(False)
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
                generator_id = self.image_generator.currentData()
                capabilities = capability_reader(generator_id) if callable(capability_reader) and generator_id else None
                if not self.image_generator_options:
                    capabilities = capability_reader() if callable(capability_reader) else None
                if capabilities is not None:
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
        self._update_preset_sizes()
        self._enable()

    def _update_preset_sizes(self):
        orientation = self.orientation.currentData()
        resolution = self.resolution.currentData()
        generator_id = self.image_generator.currentData()
        dimension_reader = getattr(self.services, "image_generation_dimensions", None)
        dimensions = dimension_reader(generator_id, orientation) if callable(dimension_reader) and generator_id else None
        gen_width, gen_height = dimensions or generation_dimensions(orientation)
        final_width, final_height = final_dimensions(orientation, resolution)
        self.generation_size.setText(f"{gen_width} × {gen_height}")
        self.final_size.setText(f"{final_width} × {final_height}")
        if self.current is not None:
            self._load_image(self.current.image_id)

    def _orientation_changed(self):
        self._update_preset_sizes()

    def _generator_changed(self):
        if self.services is not None and self.image_generator.currentData():
            capabilities = self.services.image_capabilities(self.image_generator.currentData())
            self.seed.setEnabled(capabilities.seeded)
            if not capabilities.seeded:
                self.seed.setValue(0)
        else:
            self.seed.setEnabled(True)
        self._update_preset_sizes()
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
                width, height = pixmap.width(), pixmap.height()
                orientation = self.orientation.currentData()
                if not orientation_compatible(width, height, orientation):
                    self.image.setText(
                        f"Selected image: {width} × {height} ({aspect_label(width, height)})\n"
                        f"Not compatible with {ORIENTATIONS[orientation]['label']}.\n"
                        "Generate or select an image with this orientation."
                    )
                    return
                self.image.setText("")
                self.image.setPixmap(pixmap.scaled(320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            except Exception as exc:
                self.status.setText(str(exc))

    def _enable(self):
        ready = self.services is not None and self.current is not None and not self.busy
        self.scenes.setEnabled(not self.busy)
        context_available = self.services is not None and hasattr(self.services, "save_visual_context")
        self.film_brief.setEnabled(context_available and not self.busy)
        self.visual_style.setEnabled(context_available and not self.busy)
        self.save_context_button.setEnabled(context_available and self.context_dirty and not self.busy)
        self.prompt.setEnabled(ready)
        self.buttons["Save prompt"].setEnabled(ready and self.prompt_dirty and not self.context_dirty)
        prompt_service = getattr(self.services, "prompts", None)
        prompt_available = prompt_service is None or getattr(prompt_service, "provider", None) is not None
        self.buttons["Regenerate prompt"].setEnabled(ready and prompt_available and not self.prompt_dirty and not self.context_dirty)
        self.buttons["Regenerate prompt"].setToolTip("" if prompt_available else "LLM provider is not configured.")
        self.buttons["Regenerate prompt"].setText("Regenerate prompt" if ready and self.current.prompt_id else
                                                  "Generate prompt")
        self.buttons["Select prompt"].setEnabled(ready and not self.prompt_dirty and self.prompt_variants.count() > 0)
        self.buttons["Import image"].setEnabled(ready and not self.prompt_dirty)
        generator_selected = not self.image_generator_options or self.image_generator.currentData() is not None
        self.buttons["Generate image"].setEnabled(ready and generator_selected and not self.prompt_dirty
                                                   and bool(self.current.prompt_id if ready else False))
        self.buttons["Cancel image"].setEnabled(self.image_worker is not None)
        self.buttons["Select image"].setEnabled(ready and not self.prompt_dirty and self.image_variants.count() > 0)
        configured = bool(self.services and getattr(self.services, "upscale", None)
                          and self.services.upscale.provider is not None)
        self.orientation.setEnabled(not self.busy)
        self.image_generator.setEnabled(not self.busy and bool(self.image_generator_options))
        self.resolution.setEnabled(not self.busy)
        self.buttons["Create final image"].setEnabled(ready and configured and bool(self.current.image_id)
                                                     and self.resolution.currentData() != "draft")
        self.buttons["Cancel upscale"].setEnabled(self.upscale_worker is not None)

    def _replace(self, view, message):
        self.views = tuple(view if item.id == view.id else item for item in self.views)
        self._show(view)
        self.status.setText(message)

    def _act(self, callback, message, *, media_changed=False):
        try:
            self._replace(callback(), message)
            if media_changed:
                self.media_changed.emit()
            return True
        except Exception as exc:
            self.status.setText(str(exc))
            self._enable()
            return False

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
        path, _ = QFileDialog.getOpenFileName(self, "Import scene image", filter="Images (*.png *.jpg *.jpeg *.webp)")
        if path:
            self._act(lambda: self.services.import_image(self.current.id, path), "Image imported and selected.",
                      media_changed=True)

    def generate_image(self):
        if self.current:
            generator_id = self.image_generator.currentData() if self.image_generator_options else None
            if self.image_generator_options and generator_id is None:
                self.status.setText("Select an image generator.")
                return
            dimension_reader = getattr(self.services, "image_generation_dimensions", None)
            dimensions = dimension_reader(generator_id, self.orientation.currentData()) if callable(dimension_reader) and generator_id else None
            width, height = dimensions or generation_dimensions(self.orientation.currentData())
            provider = getattr(self.services.generation, "provider", None) if hasattr(self.services, "generation") else None
            if generator_id is not None or getattr(provider, "requires_background", False):
                try:
                    pending, cached = self.services.prepare_background_image(
                        self.current.id, width=width, height=height, seed=self.seed.value(),
                        **({"generator_id": generator_id} if generator_id is not None else {}))
                    if cached is not None:
                        self._replace(cached, "Cached image selected.")
                        self.media_changed.emit()
                        self._auto_upscale()
                        return
                    if len(pending) == 6:
                        claim, scene_id, generator_id, provider, request, expected_selection_id = pending
                        self.image_claim = claim, scene_id, generator_id, expected_selection_id
                    else:
                        claim, scene_id, provider, request = pending
                        generator_id, expected_selection_id = None, ...
                        self.image_claim = claim, scene_id
                    self.image_worker = ImageGenerationThread(provider, request, self)
                    self.image_worker.outcome.connect(self._image_generated)
                    self.image_worker.start()
                    self.status.setText("Generating image in a background worker…")
                    self._enable()
                except Exception as exc:
                    self.status.setText(str(exc))
            else:
                succeeded = self._act(lambda: self.services.generate_image(
                    self.current.id, width=width, height=height, seed=self.seed.value()),
                    "Image generated and selected.", media_changed=True)
                if succeeded:
                    self._auto_upscale()

    def _image_generated(self, result, error):
        if len(self.image_claim) == 4:
            claim, scene_id, generator_id, expected_selection_id = self.image_claim
        else:
            claim, scene_id = self.image_claim
            generator_id, expected_selection_id = None, ...
        succeeded = False
        try:
            if error is not None:
                if generator_id is None:
                    self.services.finish_background_image(claim, scene_id, error=error)
                else:
                    self.services.finish_background_image(claim, scene_id, error=error, generator_id=generator_id)
            else:
                if generator_id is None:
                    view = self.services.finish_background_image(claim, scene_id, result=result)
                else:
                    view = self.services.finish_background_image(claim, scene_id, result=result,
                        generator_id=generator_id, expected_selection_id=expected_selection_id)
                self._replace(view, "Image generated and selected.")
                self.media_changed.emit()
                succeeded = True
        except Exception as exc:
            self.status.setText(str(exc))
        finally:
            self.image_worker.wait()
            self.image_worker.deleteLater()
            self.image_worker = self.image_claim = None
            self._enable()
            if succeeded:
                self._auto_upscale()

    def _auto_upscale(self):
        if self.current and self.current.image_id and self.resolution.currentData() != "draft":
            self._start_final_image(self.current.image_id)

    def create_final_image(self):
        if self.current and self.current.image_id:
            self._start_final_image(self.current.image_id)

    def _start_final_image(self, artifact_id):
        if self.busy or self.resolution.currentData() == "draft":
            return
        try:
            prepared = self.services.prepare_final_image(artifact_id, self.orientation.currentData(),
                                                         self.resolution.currentData())
            cached = self.services.cached_final_image(prepared)
            if cached is not None:
                self._replace(self.services.select_cached_final_image(prepared, cached), "Cached final image selected.")
                self.media_changed.emit()
                return
            self.upscale_prepared = prepared
            self.upscale_cancel_requested = False
            reset_cancel = getattr(self.services.upscale.provider, "reset_cancel", None)
            if callable(reset_cancel):
                reset_cancel()
            self.upscale_worker = ImageUpscaleThread(self.services.upscale.provider, prepared[2], self)
            self.upscale_worker.outcome.connect(self._upscale_finished)
            self.upscale_worker.start()
            self.status.setText("Upscaling selected image in the managed CUDA worker…")
            self._enable()
        except Exception as exc:
            self.status.setText(str(exc))
            self._enable()

    def _upscale_finished(self, result, error):
        try:
            if self.upscale_cancel_requested:
                raise RuntimeError("Local upscaling canceled.")
            if error is not None:
                raise error
            self._replace(self.services.finish_final_image(self.upscale_prepared, result), "Final image selected.")
            self.media_changed.emit()
        except Exception as exc:
            self.status.setText(str(exc))
        finally:
            self.upscale_worker.wait()
            self.upscale_worker.deleteLater()
            self.upscale_worker = self.upscale_prepared = None
            self.upscale_cancel_requested = False
            self._enable()

    def cancel_upscale(self):
        if self.upscale_worker is not None:
            self.upscale_cancel_requested = True
            self.services.upscale.provider.cancel()
            self.status.setText("Cancel requested; waiting for upscale worker cleanup.")

    def cancel_image(self):
        if self.busy:
            claim = self.image_claim[0]
            generator_id = self.image_claim[2] if len(self.image_claim) == 4 else None
            if generator_id is None:
                self.services.cancel_background_image(claim)
            else:
                self.services.cancel_background_image(claim, generator_id=generator_id)
            self.status.setText("Cancel requested; waiting for image worker completion.")

    def select_image(self):
        if self.current and self.image_variants.currentData():
            value = self.image_variants.currentData()
            self._act(lambda: self.services.select_image(self.current.id, value), "Image variant selected.",
                      media_changed=True)


__all__ = ["ScenePanel"]

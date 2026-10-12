"""Explicit manual publication preparation; retained edits and actual asset previews."""

import asyncio

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from app.desktop.artifact_export import copy_artifact
from app.domain.publishing_package import TITLE_STYLES, chapter_text, parse_chapters


class PublishPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = self.draft = self.revision = self.package = self.loop = self.task = None
        self.busy = self.loading = self.dirty = self.canceled = self.thumbnail_dirty = False
        self.deferred_sources = False
        root = QVBoxLayout(self); scroll = QScrollArea(); scroll.setWidgetResizable(True)
        self.body = QWidget(); root.addWidget(scroll); scroll.setWidget(self.body); layout = QVBoxLayout(self.body)
        self.status = QLabel("MISSING: open a completed project."); self.status.setWordWrap(True); root.addWidget(self.status)
        self.refresh_button = QPushButton("Refresh verified sources"); self.refresh_button.clicked.connect(lambda: self.run(self.activate)); root.addWidget(self.refresh_button)
        self.cancel_button = QPushButton("Cancel preparation"); self.cancel_button.clicked.connect(self.cancel); self.cancel_button.setEnabled(False); root.addWidget(self.cancel_button)
        def section(name):
            group = QGroupBox(name); layout.addWidget(group); return QFormLayout(group)
        def button(text, callback, form):
            value = QPushButton(text); value.clicked.connect(lambda: self.run(callback)); form.addRow(value); return value
        form = section("Source Video")
        self.source = QComboBox(); form.addRow("Verified source", self.source)
        self.source_details = QLabel(); self.source_details.setWordWrap(True); form.addRow(self.source_details)
        self.rebase_button = button("Review these edits for the selected source / context", self.rebase, form)
        self.source.currentIndexChanged.connect(lambda: self.run(self.source_changed))
        form = section("Metadata — editorial review required")
        self.generate_button = button("Generate YouTube Metadata", lambda: self.start(self.generate()), form)
        self.candidates = QComboBox(); form.addRow("Retained candidate sets", self.candidates)
        button("Load candidate (replace editable metadata)", self.load_candidate, form)
        self.titles = {k: QLineEdit() for k in TITLE_STYLES}
        for style, field in self.titles.items(): form.addRow(style.title() + " title", field); field.textEdited.connect(self.edited)
        self.preferred = QComboBox(); self.preferred.addItems(TITLE_STYLES); form.addRow("Selected title", self.preferred)
        self.preferred.currentIndexChanged.connect(self.edited)
        self.description = QPlainTextEdit(); self.description.setMaximumHeight(160); form.addRow("Description (chapters added at export)", self.description)
        self.tags = QLineEdit(); self.hashtags = QLineEdit(); self.links = QPlainTextEdit(); self.links.setMaximumHeight(55)
        self.pinned = QPlainTextEdit(); self.pinned.setMaximumHeight(65)
        for label, field in (("Keywords (comma separated)", self.tags), ("Hashtags (space separated)", self.hashtags),
                             ("Configured channel URLs (one per line)", self.links), ("Optional pinned comment", self.pinned)):
            form.addRow(label, field)
        for field in (self.description, self.pinned, self.links): field.textChanged.connect(self.edited)
        self.tags.textEdited.connect(self.edited); self.hashtags.textEdited.connect(self.edited)
        self.approved = QCheckBox("I reviewed source status, title/content accuracy, links, chapters and thumbnail relevance")
        form.addRow(self.approved); self.approved.toggled.connect(lambda: self.edited(clear_approval=False))
        self.save_button = button("Save new metadata / selection revision", self.save, form)
        form = section("Chapters")
        self.use_chapters = QCheckBox("Include chapters (optional for Social)"); form.addRow(self.use_chapters)
        self.use_chapters.toggled.connect(self.edited)
        button("Generate chapters from actual Timeline", self.generate_chapters, form)
        self.chapter_editor = QPlainTextEdit(); self.chapter_editor.setMaximumHeight(130); form.addRow(self.chapter_editor)
        self.chapter_editor.textChanged.connect(self.edited)
        button("Validate chapter timestamps", self.validate_chapters, form)
        form = section("Subtitles")
        self.subtitle_status = QLabel("MISSING: generate/reuse measured captions explicitly."); self.subtitle_status.setWordWrap(True); form.addRow(self.subtitle_status)
        self.alignment_button = button("Generate missing alignment", lambda: self.start(self.align()), form)
        button("Generate / reuse synchronized SRT", self.generate_subtitles, form)
        self.subtitle_preview = QPlainTextEdit(); self.subtitle_preview.setReadOnly(True); self.subtitle_preview.setMaximumHeight(100); form.addRow(self.subtitle_preview)
        button("Omit optional subtitles", self.omit_subtitles, form)
        button("Export verified SRT", self.export_srt, form)
        form = section("Thumbnail — local, deterministic layouts")
        button("Suggest three video frames", lambda: self.start(self.frames()), form)
        button("Load current selected scene images", self.scene_images, form)
        button("Import custom image", self.import_image, form)
        self.images = QComboBox(); form.addRow("Image source", self.images)
        self.images.currentIndexChanged.connect(lambda: (self.run(self.preview_image), self.thumbnail_edited()))
        self.headline = QLineEdit(); form.addRow("Headline", self.headline)
        self.font_size = QSpinBox(); self.font_size.setRange(24, 200); self.font_size.setValue(100); form.addRow("Font size", self.font_size)
        self.position = QComboBox(); self.position.addItems(["bottom", "top", "center"]); form.addRow("Text position", self.position)
        self.darkening = QDoubleSpinBox(); self.darkening.setRange(0, .85); self.darkening.setValue(.35); form.addRow("Darkening", self.darkening)
        self.accent = QLineEdit("#F3C553"); form.addRow("Accent #RRGGBB", self.accent)
        self.use_logo = QCheckBox("Use verified configured channel logo"); form.addRow(self.use_logo)
        for field in (self.headline, self.accent): field.textEdited.connect(self.thumbnail_edited)
        self.font_size.valueChanged.connect(self.thumbnail_edited); self.position.currentIndexChanged.connect(self.thumbnail_edited)
        self.darkening.valueChanged.connect(self.thumbnail_edited); self.use_logo.toggled.connect(self.thumbnail_edited)
        button("Generate three thumbnail layouts", self.generate_thumbnails, form)
        self.thumbnail_choices = QComboBox(); form.addRow("Candidate layout", self.thumbnail_choices)
        self.thumbnail_choices.currentIndexChanged.connect(lambda: self.run(self.preview_thumbnail))
        button("Select this thumbnail for the draft", self.select_thumbnail, form)
        self.preview = QLabel("Select an image to inspect the actual preview."); self.preview.setMinimumHeight(160); self.preview.setAlignment(Qt.AlignCenter); form.addRow(self.preview)
        form = section("Publishing Package — manual YouTube Studio handoff")
        button("Validate saved revision / assets", self.validate, form)
        self.build_button = button("Build Publishing Package ZIP", self.build_package, form)
        button("Open package output folder", self.open_folder, form)
        button("Save package copy as…", self.save_package_copy, form)
        self.timer = QTimer(self); self.timer.setInterval(10); self.timer.timeout.connect(self.tick)

    def run(self, callback):
        try: return callback()
        except Exception as exc: self.status.setText(str(exc)); return None

    def edited(self, *_args, clear_approval=True):
        if self.loading: return
        self.dirty = True; self.package = None
        if clear_approval:
            self.approved.blockSignals(True); self.approved.setChecked(False); self.approved.blockSignals(False)
        self.status.setText("REVIEW REQUIRED: unsaved edits. Save a reviewed revision before packaging.")

    def thumbnail_edited(self, *_):
        if self.loading: return
        self.thumbnail_dirty = True; self.edited()

    def bind(self, service, *, defer_sources=False):
        self.service = service; self.draft = self.revision = self.package = None
        self.deferred_sources = defer_sources
        if service:
            self.revision, self.draft = service.artifacts.latest()
        self.source.blockSignals(True); self.source.clear(); self.source.blockSignals(False)
        if not defer_sources:
            self.refresh_sources()
            if self.draft is None and self.source.currentData(): self.draft = service.starting_draft(self.source.currentData())
        else: self.body.setEnabled(False)
        self.populate(); self.refresh_candidates()
        self.alignment_button.setEnabled(bool(service and service.alignment))
        if service and not service.alignment: self.subtitle_status.setText("MISSING: reuse measured alignment artifacts, or configure an alignment provider for explicit generation.")

    def activate(self):
        self.deferred_sources = False
        self.status.setText("Verifying selected final video and current Timeline bindings…")
        self.refresh_sources()
        if self.draft is None and self.source.currentData():
            self.draft = self.service.starting_draft(self.source.currentData()); self.populate()

    def refresh_sources(self):
        previous = self.source.currentData()
        self.source.blockSignals(True); self.source.clear()
        if self.service:
            for kind, manifest, evidence in self.service.sources():
                self.source.addItem({"original": "Original Final MP4", "branded": "Branded MP4", "delivery": "YouTube Delivery MP4"}[kind], kind)
            index = self.source.findData(previous or (self.draft or {}).get("source_kind"))
            if index >= 0: self.source.setCurrentIndex(index)
        self.source.blockSignals(False)
        self.body.setEnabled(self.service is not None and self.source.count() > 0 and not self.busy)
        self.source_changed()

    def source_changed(self, *_):
        if not self.service or not self.source.currentData():
            message = "MISSING: no current verified final video. Build/select a render for the saved Timeline."
            if self.service and not self.deferred_sources:
                try: self.service.original()
                except (ValueError, OSError, KeyError) as exc: message = str(exc)
            elif self.service: message = "Open Publish or refresh sources to verify eligible final videos. Retained edits are preserved."
            self.status.setText(message); return
        source, evidence, _ = self.service.source(self.source.currentData())
        duration = evidence["video_duration"][0] / evidence["video_duration"][1]
        self.source_details.setText(f"{source.artifact_id}\n{evidence['width']}×{evidence['height']} · {duration:.2f}s · {self.service.session.project.language}\n"
            f"Source: {evidence.get('status', 'verified')} · Package: requires saved review/validation")
        if self.draft:
            _, fingerprint = self.service.context(self.source.currentData(), self.draft["links"])
            if self.draft["source_kind"] != self.source.currentData() or self.draft["context_fingerprint"] != fingerprint:
                self.status.setText("STALE: retained edits were preserved. Explicitly review for the selected source, then regenerate timed components.")
                self.package = None

    def populate(self):
        self.loading = True
        try:
            if self.draft:
                index = self.source.findData(self.draft["source_kind"])
                if index >= 0: self.source.setCurrentIndex(index)
                m = self.draft["metadata"] or {"titles": {}, "description": "", "keywords": [], "hashtags": [], "pinned_comment": ""}
                for k, field in self.titles.items(): field.setText(m["titles"].get(k, ""))
                self.description.setPlainText(m["description"]); self.tags.setText(", ".join(m["keywords"]))
                self.hashtags.setText(" ".join(m["hashtags"])); self.pinned.setPlainText(m["pinned_comment"])
                self.links.setPlainText("\n".join(self.draft["links"])); self.preferred.setCurrentText(self.draft["selected_title"])
                self.approved.setChecked(self.draft["approved"]); self.use_chapters.setChecked(self.draft["chapters_enabled"])
                self.chapter_editor.setPlainText(chapter_text(self.draft["chapters"]))
                if self.draft["thumbnail_id"]:
                    self.thumbnail_choices.clear(); self.thumbnail_choices.addItem("Retained selected thumbnail", self.draft["thumbnail_id"])
                    m = self.service.artifacts.one(self.draft["thumbnail_id"], "thumbnail")
                    settings = m.metadata["identity"]["settings"]
                    self.headline.setText(settings["headline"]); self.font_size.setValue(settings.get("font_size", 100))
                    self.position.setCurrentText(settings.get("position", "bottom")); self.darkening.setValue(settings.get("darkening", .35))
                    self.accent.setText(settings.get("accent", "#F3C553")); self.use_logo.setChecked(bool(settings.get("logo_id")))
                    image = self.service.artifacts.one(m.metadata["identity"]["image_id"])
                    self.images.clear(); self.images.addItem(image.name, image.artifact_id)
                    self.preview_thumbnail()
                if self.draft["subtitle_id"]:
                    self.subtitle_preview.setPlainText(self.service.artifacts.bytes(self.service.artifacts.one(self.draft["subtitle_id"], "subtitles")).decode("utf-8"))
            self.dirty = self.thumbnail_dirty = False
        finally: self.loading = False
        self.source_changed()

    def refresh_candidates(self):
        self.candidates.clear()
        if self.service:
            for m in self.service.artifacts.history("candidates"):
                value = self.service.artifacts.read(m)
                self.candidates.addItem(f"{m.created_at:%H:%M:%S} · {value['origin']}", m.artifact_id)
            self.candidates.setCurrentIndex(self.candidates.count() - 1)

    async def generate(self):
        manifest = await self.service.generate_metadata(self.source.currentData(), self.links.toPlainText().split())
        self.refresh_candidates()
        if self.canceled:
            self.status.setText("Preparation canceled. Completed candidate set retained without replacing edits.")
            return manifest
        if self.draft and self.draft["metadata"] is None and not self.dirty: self.load_candidate()
        self.status.setText("REVIEW REQUIRED: candidate set retained. Load it explicitly to replace existing editable metadata.")
        return manifest

    def load_candidate(self):
        manifest = self.service.artifacts.one(self.candidates.currentData(), "candidates")
        value = self.service.artifacts.read(manifest)
        if self.draft is None: self.draft = self.service.starting_draft(self.source.currentData())
        self.draft = {**self.draft, "metadata": value["metadata"], "candidate_id": manifest.artifact_id, "approved": False}
        self.loading = True
        try:
            m = value["metadata"]
            for k, field in self.titles.items(): field.setText(m["titles"][k])
            self.description.setPlainText(m["description"]); self.tags.setText(", ".join(m["keywords"]))
            self.hashtags.setText(" ".join(m["hashtags"])); self.pinned.setPlainText(m["pinned_comment"])
        finally: self.loading = False
        self.edited()

    def rebase(self):
        if self.draft is None: self.draft = self.service.starting_draft(self.source.currentData())
        kind = self.source.currentData(); links = self.links.toPlainText().split()
        self.draft.update(source_kind=kind, context_fingerprint=self.service.context(kind, links)[1], links=links,
                          subtitle_id=None, approved=False)
        self.subtitle_preview.clear(); self.subtitle_status.setText("MISSING: regenerate subtitles for this source.")
        self.edited(); self.status.setText("REVIEW REQUIRED: edits retained for this source. Review chapter timings and regenerate subtitles before approval.")

    def save(self):
        if not self.draft: raise ValueError("MISSING: prepare metadata first.")
        if self.source.currentData() != self.draft["source_kind"]:
            raise ValueError("STALE: explicitly review these edits for the selected source before saving.")
        value = {**self.draft, "metadata": {"titles": {k: f.text() for k, f in self.titles.items()},
            "description": self.description.toPlainText(), "keywords": [s.strip() for s in self.tags.text().split(",") if s.strip()],
            "hashtags": self.hashtags.text().split(), "pinned_comment": self.pinned.toPlainText()},
            "selected_title": self.preferred.currentText(), "links": self.links.toPlainText().split(),
            "approved": self.approved.isChecked(), "chapters_enabled": self.use_chapters.isChecked(), "chapters": parse_chapters(self.chapter_editor.toPlainText())}
        self.revision = self.service.save_draft(value, expected_revision_id=self.revision.artifact_id if self.revision else None)
        self.draft = self.service.artifacts.read(self.revision); self.dirty = False
        self.status.setText("Saved new approved revision; validate package assets." if value["approved"] else "Saved draft — REVIEW REQUIRED.")

    def generate_chapters(self):
        chapters = self.service.chapters(self.source.currentData())
        self.chapter_editor.setPlainText(chapter_text(chapters)); self.status.setText("Chapters derived from exact Timeline timing." if chapters else "Chapters not applicable to this Social video.")

    def validate_chapters(self):
        from app.domain.publishing_package import validate_chapters
        _, evidence, _ = self.service.source(self.source.currentData())
        validate_chapters(parse_chapters(self.chapter_editor.toPlainText()), evidence["video_duration"][0] / evidence["video_duration"][1])
        self.status.setText("Chapter timestamp rules passed. Review chapter titles manually.")

    async def align(self):
        count = await self.service.generate_missing_alignment(self.source.currentData(), canceled=lambda: self.canceled)
        self.subtitle_status.setText(f"Aligned {count} missing section(s). Generate/reuse SRT explicitly.")

    def generate_subtitles(self):
        m = self.service.subtitles(self.source.currentData()); self.draft["subtitle_id"] = m.artifact_id
        self.subtitle_preview.setPlainText(self.service.artifacts.bytes(m).decode("utf-8")); self.edited()
        self.subtitle_status.setText("Verified synchronized UTF-8 SRT available; save the selection revision.")

    def omit_subtitles(self):
        self.draft["subtitle_id"] = None; self.subtitle_preview.clear(); self.edited()
        self.subtitle_status.setText("Optional subtitles deliberately omitted; package README will record this.")

    def verified_srt(self):
        m = self.service.subtitles(self.source.currentData(), create=False)
        if m.artifact_id != self.draft["subtitle_id"]: raise ValueError("STALE: regenerate the selected SRT.")
        return m

    def export_srt(self):
        return copy_artifact(self, self.service.store, self.verified_srt(), title="Save synchronized SRT", suggested="subtitles.srt",
            revalidate=self.verified_srt, suffix=".srt", file_filter="SRT subtitles (*.srt)")

    async def frames(self):
        values = await self.service.frame_suggestions(self.source.currentData(), canceled=lambda: self.canceled)
        self.set_images(values); self.status.setText("Three actual frame previews available. Review visual suitability before selecting.")

    def scene_images(self): self.set_images(self.service.scene_images(self.source.currentData()))
    def set_images(self, values):
        self.images.clear()
        for m in values: self.images.addItem(m.name, m.artifact_id)
        self.preview_image()
    def import_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import thumbnail source", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if path: self.set_images([self.service.import_image(path)])
    def show_preview(self, raw):
        pixmap = QPixmap();
        if not pixmap.loadFromData(raw): raise ValueError("Invalid thumbnail preview.")
        self.preview.setPixmap(pixmap.scaled(480, 270, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    def preview_image(self):
        if self.images.currentData(): self.show_preview(self.service.artifacts.bytes(self.service.artifacts.one(self.images.currentData())))
    def preview_thumbnail(self):
        if self.thumbnail_choices.currentData(): self.show_preview(self.service.verify_thumbnail(self.service.artifacts.one(self.thumbnail_choices.currentData(), "thumbnail")))
    def generate_thumbnails(self):
        logo_id = self.service.finishing.branding.current().logo_id if self.use_logo.isChecked() and self.service.finishing else None
        if self.use_logo.isChecked() and not logo_id: raise ValueError("MISSING: configure and import a channel logo in Export first.")
        values = self.service.thumbnail_candidates(self.images.currentData(), {"source_kind": self.source.currentData(),
            "headline": self.headline.text(), "font_size": self.font_size.value(), "position": self.position.currentText(),
            "darkening": self.darkening.value(), "accent": self.accent.text(), "logo_id": logo_id})
        self.thumbnail_choices.clear()
        for m in values: self.thumbnail_choices.addItem(m.metadata["style"], m.artifact_id)
        self.preview_thumbnail()
    def select_thumbnail(self):
        m = self.service.artifacts.one(self.thumbnail_choices.currentData(), "thumbnail"); self.service.verify_thumbnail(m)
        self.draft["thumbnail_id"] = m.artifact_id; self.thumbnail_dirty = False
        self.edited(); self.status.setText("Thumbnail selected; save the revision.")

    def validate(self):
        if self.dirty or self.revision is None: raise ValueError("REVIEW REQUIRED: save the edited revision first.")
        if self.source.currentData() != self.draft["source_kind"]:
            raise ValueError("STALE: review the retained edits for the selected source first.")
        if self.thumbnail_dirty: raise ValueError("REVIEW REQUIRED: generate/select layouts for the changed thumbnail settings.")
        self.service.validate(self.revision); self.status.setText("READY: machine checks passed and editorial review was approved. No upload will occur.")
    def build_package(self):
        self.validate(); self.package = self.service.build_package(self.revision)
        self.status.setText("READY: validated publishing ZIP retained. Save a copy for manual YouTube Studio publication.")
    def verified_package(self):
        self.validate()
        if not self.package:
            identity = self.service.validate(self.revision)[-1]
            from app.domain.dependencies import content_fingerprint
            self.package = self.service.artifacts.cached(content_fingerprint(identity))
        if not self.package: raise ValueError("MISSING: build the current publishing ZIP.")
        return self.service.artifacts.verify_package(self.package)
    def open_folder(self):
        m = self.verified_package()
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.service.store._artifact_path(m.storage_key).parent)))
    def save_package_copy(self):
        return copy_artifact(self, self.service.store, self.verified_package(), title="Save publishing ZIP", suggested="publishing-package.zip",
            revalidate=self.verified_package, suffix=".zip", file_filter="ZIP package (*.zip)")

    def start(self, coroutine):
        if self.busy: coroutine.close(); return
        self.busy = True; self.canceled = False; self.body.setEnabled(False); self.cancel_button.setEnabled(True)
        self.status.setText("Preparing publishing assets…")
        self.loop = asyncio.new_event_loop(); self.task = self.loop.create_task(coroutine); self.timer.start()
    def cancel(self): self.canceled = True; self.status.setText("Cancellation requested; waiting for provider/process cleanup.")
    def tick(self):
        self.loop.call_soon(self.loop.stop); self.loop.run_forever()
        if not self.task.done(): return
        self.timer.stop(); self.busy = False
        try:
            self.task.result()
        except Exception as exc: self.status.setText(str(exc))
        finally:
            self.loop.close(); self.loop = self.task = None
            self.body.setEnabled(self.source.count() > 0); self.cancel_button.setEnabled(False)

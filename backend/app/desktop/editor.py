"""Section editor presentation; persistence and editing rules live in services."""

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from app.application.script_generation import ScriptGenerationService
from app.desktop.audio_panel import AudioPanel
from app.desktop.scene_panel import ScenePanel
from app.desktop.scene_planning_panel import ScenePlanningPanel
from app.desktop.timeline_panel import TimelinePanel
from app.desktop.preview_panel import PreviewPanel
from app.desktop.regeneration_panel import RegenerationPanel


class GenerationThread(QThread):
    outcome = Signal(object, str)

    def __init__(self, snapshot, provider, request, parent=None):
        super().__init__(parent)
        self.snapshot, self.provider, self.request = snapshot, provider, request

    def run(self):
        # D014 constructs/validates a revision here without crossing SQLite threads.
        class CapturedSession:
            active_script = self.snapshot

            def save_script(self, revision, *, expected_active_revision_id):
                if expected_active_revision_id != self.active_script.id:
                    raise ValueError("Generation snapshot changed.")

        try:
            revision = ScriptGenerationService(CapturedSession(), self.provider).generate(
                self.request, expected_active_revision_id=self.snapshot.id)
            self.outcome.emit(revision, "")
        except Exception as exc:
            self.outcome.emit(None, str(exc))


class ProjectEditor(QMainWindow):
    def __init__(self, projects, provider=None, audio_factory=None, scene_factory=None, timeline_factory=None,
                 preview_factory=None, regeneration_factory=None):
        super().__init__()
        self.projects, self.provider = projects, provider
        self.audio_factory, self.scene_factory = audio_factory, scene_factory
        self.timeline_factory = timeline_factory
        self.preview_factory = preview_factory
        self.regeneration_factory = regeneration_factory

        self.regeneration = RegenerationPanel(self)
        self.regeneration.before_start = self._regeneration_ready
        self.regeneration.busy_changed.connect(self._regenerating)
        self.regeneration.finished.connect(self._regenerated)
        self.timeline = TimelinePanel(self)
        self.preview = PreviewPanel(self)
        self.timeline.changed.connect(self._timeline_changed)
        self.audio = AudioPanel(self)
        self.scene_plans = ScenePlanningPanel(self)
        self.scene_plans.plan_changed.connect(self._scene_plan_changed)
        self.visuals = ScenePanel(self)
        self.visuals.media_changed.connect(lambda: self.preview.timeline_changed(self.timeline.edit))
        self.session = self.snapshot = self.selected_id = None
        self.worker = None
        self.dirty = False
        self.loading = False
        self.setWindowTitle("AI Content Studio — Project editor")
        self.resize(1000, 720)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        project_bar = QHBoxLayout()
        self.project_name = QLineEdit("My project")
        self.language = QLineEdit("en")
        project_bar.addWidget(QLabel("Project name"))
        project_bar.addWidget(self.project_name)
        project_bar.addWidget(QLabel("Language"))
        project_bar.addWidget(self.language)
        layout.addLayout(project_bar)
        self.buttons = {}
        self._button(project_bar, "Create project", lambda: self._choose_project(True))
        self._button(project_bar, "Open project", lambda: self._choose_project(False))
        self.status = QLabel("Create or open a project." if provider else
                             "Create or open a project. Generation requires a configured provider.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        section_bar = QHBoxLayout()
        section_bar.addWidget(QLabel("Narrative section"))
        self.section_choice = QComboBox()
        self.section_choice.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.section_choice.currentIndexChanged.connect(self._section_choice_changed)
        section_bar.addWidget(self.section_choice, 1)
        layout.addLayout(section_bar)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.script_tab = QWidget()
        script_layout = QVBoxLayout(self.script_tab)
        self.sections = QListWidget()
        self.sections.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.sections.currentRowChanged.connect(self._select)
        script_layout.addWidget(self.sections)
        form = QFormLayout()
        self.title, self.role = QLineEdit(), QLineEdit("body")
        self.text = QPlainTextEdit()
        form.addRow("Section title", self.title)
        form.addRow("Role", self.role)
        form.addRow("Text", self.text)
        script_layout.addLayout(form)
        for field in (self.title, self.role, self.text):
            field.textChanged.connect(self._dirty)
        actions = QHBoxLayout()
        script_layout.addLayout(actions)
        for label, action in (("New section", self.new_section), ("Save", self.save),
                              ("Discard draft", self.discard), ("Split at cursor", self.split),
                              ("Merge selected", self.merge), ("Move up", lambda: self.move(-1)),
                              ("Move down", lambda: self.move(1))):
            self._button(actions, label, action)
        self.request = QPlainTextEdit()
        self.request.setPlaceholderText("Describe the script to generate (replaces saved sections).")
        self.request.setMaximumHeight(90)
        script_layout.addWidget(self.request)
        self.generate_button = QPushButton("Generate replacement script")
        self.generate_button.clicked.connect(self.generate)
        self.generate_button.setEnabled(provider is not None)
        self.generate_button.setToolTip("Generate with the configured structured script provider." if provider else
                                       "Generation is unavailable until a structured script provider is configured.")
        script_layout.addWidget(self.generate_button)
        self.tabs.addTab(self.script_tab, "Script")

        self.voice_tab = QWidget()
        voice_layout = QVBoxLayout(self.voice_tab)
        self.voice_section = QLabel("No narrative section selected.")
        voice_layout.addWidget(self.voice_section)
        voice_layout.addWidget(self.audio, 1)
        self.tabs.addTab(self.voice_tab, "Voice")

        self.tabs.addTab(self.scene_plans, "Scenes")

        self.visuals_tab = QWidget()
        visuals_layout = QVBoxLayout(self.visuals_tab)
        self.visuals_section = QLabel("No narrative section selected.")
        visuals_layout.addWidget(self.visuals_section)
        visuals_layout.addWidget(self.visuals, 1)
        self.tabs.addTab(self.visuals_tab, "Visuals")

        self.timeline_tab = QWidget()
        timeline_layout = QVBoxLayout(self.timeline_tab)
        timeline_splitter = QSplitter(Qt.Horizontal)
        timeline_splitter.addWidget(self.timeline)
        timeline_splitter.addWidget(self.preview)
        timeline_splitter.setStretchFactor(0, 3)
        timeline_splitter.setStretchFactor(1, 2)
        timeline_layout.addWidget(timeline_splitter)
        self.tabs.addTab(self.timeline_tab, "Timeline")

        self.export_tab = QWidget()
        export_layout = QVBoxLayout(self.export_tab)
        self.export_status = QLabel("Open a project to inspect final-output readiness.")
        self.export_status.setWordWrap(True)
        export_layout.addWidget(self.export_status)
        self.final_render_button = QPushButton("Build/rebuild final render")
        self.final_render_button.clicked.connect(self._rebuild_final_render)
        self.final_render_button.setEnabled(False)
        export_layout.addWidget(self.final_render_button)
        export_layout.addWidget(QLabel("Advanced / selective regeneration"))
        export_layout.addWidget(self.regeneration, 1)
        self.tabs.addTab(self.export_tab, "Export")

        for field in (self.title, self.role, self.text):
            field.setEnabled(False)

    def _button(self, layout, label, callback):
        button = QPushButton(label)
        button.clicked.connect(callback)
        layout.addWidget(button)
        self.buttons[label] = button

    def _section_choice_changed(self, index):
        if self.loading or self.snapshot is None:
            return
        section_id = self.section_choice.itemData(index)
        ids = [section.section_id for section in self.snapshot.sections]
        self.sections.setCurrentRow(ids.index(section_id) if section_id in ids else -1)
        if self.selected_id != section_id:
            self._sync_section_choice()

    def _sync_section_choice(self):
        index = self.section_choice.findData(self.selected_id)
        self.section_choice.blockSignals(True)
        self.section_choice.setCurrentIndex(index)
        self.section_choice.blockSignals(False)

    def _timeline_changed(self, edit):
        self.preview.timeline_changed(edit)
        if self.session is None:
            self.export_status.setText("Open a project to inspect final-output readiness.")
        elif edit is None:
            self.export_status.setText("No saved timeline exists. Build the timeline before preview or final render.")
        else:
            self.export_status.setText(
                f"Timeline ready: {len(edit.timeline.clips)} clips. Use preview in Timeline; "
                "use selective regeneration below to inspect or rebuild the final render."
            )

    def _scene_plan_changed(self):
        section = self.snapshot.section(self.selected_id) if self.snapshot and self.selected_id else None
        self.visuals.select_section(section)
        if self.timeline.services:
            self.timeline.run(self.timeline.refresh)
        self._run(self._bind_regeneration)

    def _rebuild_final_render(self):
        index = self.regeneration.outputs.findData("project:video_render")
        if index < 0:
            self.regeneration.status.setText("Final rendering is unavailable for the current project configuration.")
            return
        self.regeneration.outputs.setCurrentIndex(index)
        self.regeneration.start()

    def _dirty(self):
        if not self.loading:
            self.dirty = True
            self.audio.set_draft(True)

    def _ready(self, clean=True):
        if self.regeneration.busy:
            raise ValueError("Wait for regeneration cleanup.")
        if self.worker is not None:
            raise ValueError("Wait for generation to finish.")
        if self.session is None:
            raise ValueError("Create or open a project first.")
        if clean and self.dirty:
            raise ValueError("Save or discard your draft first.")
        if self.visuals.prompt_dirty:
            raise ValueError("Save the scene prompt draft first.")

    def _run(self, action):
        try:
            action()
        except Exception as exc:
            self.status.setText(str(exc))

    def _choose_project(self, create):
        path = QFileDialog.getExistingDirectory(self, "Project folder")
        if path:
            self.load_project(path, create=create)

    def load_project(self, path, *, create=False):
        def action():
            if self.worker is not None or self.dirty or self.audio.busy or self.preview.busy or self.regeneration.busy or self.visuals.busy or self.visuals.prompt_dirty:
                raise ValueError("Finish generation and save or discard your draft first.")
            candidate = (self.projects.create(path, name=self.project_name.text(), language=self.language.text())
                         if create else self.projects.open(path))
            try:
                snapshot, project = candidate.active_script, candidate.project
                audio_services = self.audio_factory(candidate) if self.audio_factory else None
                scene_services = self.scene_factory(candidate) if self.scene_factory else None
                timeline_services = self.timeline_factory(candidate) if self.timeline_factory else None
                preview_services = (self.preview_factory(candidate, timeline_services)
                                    if self.preview_factory and timeline_services else None)
            except Exception:
                candidate.close()
                raise
            if self.session:
                self.audio.stop()
                self.preview.stop()
                self.session.close()
            self.session, self.snapshot = candidate, snapshot
            self.audio.bind(audio_services, project.language)
            self.scene_plans.bind(scene_services)
            self.visuals.bind(scene_services)
            self.preview.bind(preview_services)
            self.timeline.bind(timeline_services)
            self.project_name.setText(project.name)
            self.language.setText(project.language)
            self._refresh()
        self._run(action)

    def _refresh(self, selected=None):
        self.snapshot = self.session.active_script
        for field in (self.title, self.role, self.text):
            field.setEnabled(True)
        self.loading = True
        self.sections.blockSignals(True)
        self.section_choice.blockSignals(True)
        self.sections.clear()
        self.section_choice.clear()
        for section in self.snapshot.sections:
            item = QListWidgetItem(section.title)
            item.setData(Qt.UserRole, section.section_id)
            self.sections.addItem(item)
            self.section_choice.addItem(section.title, section.section_id)
        ids = [s.section_id for s in self.snapshot.sections]
        row = ids.index(selected) if selected in ids else (0 if ids else -1)
        self.sections.setCurrentRow(row)
        self.sections.blockSignals(False)
        self.section_choice.setCurrentIndex(row)
        self.section_choice.blockSignals(False)
        self.loading = False
        self.dirty = False
        self._select(row)
        self._bind_regeneration()
        self.status.setText("Saved.")

    def _bind_regeneration(self):
        if self.regeneration_factory and self.session and not self.regeneration.busy:
            def selection(section):
                choice = self.audio.voices.currentData()
                if choice is None:
                    raise ValueError("Choose an audio voice before rebuilding narration.")
                return self.audio.services.selection(choice)
            self.regeneration.bind(self.regeneration_factory(self.session, self.audio.services,
                self.visuals.services, self.timeline.services, self.preview.services, selection))
            self.final_render_button.setEnabled(
                self.regeneration.outputs.findData("project:video_render") >= 0
            )

    def _regenerating(self, busy):
        for index in range(5):
            self.tabs.setTabEnabled(index, not busy)
        self.section_choice.setEnabled(not busy)
        self.buttons["Create project"].setEnabled(not busy)
        self.buttons["Open project"].setEnabled(not busy)
        self.final_render_button.setEnabled(
            not busy and self.regeneration.outputs.findData("project:video_render") >= 0
        )
        if busy:
            self.audio.stop()
            self.preview.stop()

    def _regeneration_ready(self):
        self._ready()
        if self.audio.busy or self.preview.busy or self.visuals.busy:
            raise ValueError("Finish audio, image, or preview work before regeneration.")

    def _regenerated(self):
        if self.timeline.services:
            self.timeline.run(self.timeline.refresh)
        self._run(self._bind_regeneration)

    def _select(self, row):
        if self.loading:
            return
        if (self.dirty or self.worker is not None or self.audio.busy or self.preview.busy or self.visuals.busy
                or self.regeneration.busy or self.visuals.prompt_dirty):
            self.sections.blockSignals(True)
            ids = [s.section_id for s in self.snapshot.sections]
            self.sections.setCurrentRow(ids.index(self.selected_id) if self.selected_id in ids else -1)
            self.sections.blockSignals(False)
            self._sync_section_choice()
            self.status.setText(
                "Save or discard drafts and finish audio, preview, or regeneration work before switching sections."
            )
            return
        section = self.snapshot.sections[row] if self.snapshot and row >= 0 else None
        self.selected_id = section.section_id if section else None
        self.loading = True
        self.title.setText(section.title if section else "")
        self.role.setText(section.role if section else "body")
        self.text.setPlainText(section.text if section else "")
        self.loading = False
        self.audio.select_section(section)
        self.scene_plans.select_section(section)
        self.visuals.select_section(section)
        label = f"Selected section: {section.title}" if section else "No narrative section selected."
        self.voice_section.setText(label)
        self.visuals_section.setText(label)
        self._sync_section_choice()

    def new_section(self):
        def action():
            self._ready()
            self.sections.setCurrentRow(-1)
            self._select(-1)
        self._run(action)

    def save(self):
        def action():
            self._ready(clean=False)
            if self.selected_id and not self.dirty:
                return
            service = ScriptGenerationService(self.session)
            kwargs = dict(title=self.title.text(), role=self.role.text(), expected_active_revision_id=self.snapshot.id)
            if self.selected_id:
                revision = service.edit_text(self.selected_id, self.text.toPlainText(), **kwargs)
                selected = self.selected_id
            else:
                revision = service.append_text(self.text.toPlainText(), **kwargs)
                selected = revision.sections[-1].section_id
            self._refresh(selected)
        self._run(action)

    def discard(self):
        def action():
            self._ready(clean=False)
            self._refresh(self.selected_id)
        self._run(action)

    def split(self):
        def action():
            self._ready()
            # QTextCursor positions count UTF-16 units; D038 counts Python code points.
            units = self.text.textCursor().position()
            source = self.snapshot.section(self.selected_id).text
            # Qt displays CRLF as one paragraph break. Keep offsets in the exact
            # retained source, including both bytes/code points of that pair.
            boundary = consumed = 0
            while boundary < len(source) and consumed < units:
                char = source[boundary]
                boundary += 2 if source[boundary:boundary + 2] == "\r\n" else 1
                consumed += 2 if ord(char) > 0xFFFF else 1
            if consumed != units:
                raise ValueError("Place the cursor between complete characters.")
            result = self.session.split_section(self.selected_id, boundary,
                                                expected_active_revision_id=self.snapshot.id)
            self._refresh(result.lineages[0].section_id)
        self._run(action)

    def merge(self):
        def action():
            self._ready()
            ids = [self.sections.item(i).data(Qt.UserRole) for i in range(self.sections.count())
                   if self.sections.item(i).isSelected()]
            result = self.session.merge_sections(ids, role=self.role.text(),
                                                 expected_active_revision_id=self.snapshot.id)
            self._refresh(result.lineages[0].section_id)
        self._run(action)

    def move(self, delta):
        def action():
            self._ready()
            ids = [s.section_id for s in self.snapshot.sections]
            index = ids.index(self.selected_id)
            target = index + delta
            if not 0 <= target < len(ids):
                return
            ids[index], ids[target] = ids[target], ids[index]
            self.session.reorder_sections(ids, expected_active_revision_id=self.snapshot.id)
            self._refresh(self.selected_id)
        self._run(action)

    def generate(self):
        def action():
            self._ready()
            if self.provider is None:
                raise ValueError("Configure a structured script provider first.")
            if not self.request.toPlainText().strip():
                raise ValueError("Describe the script to generate first.")
            if self.snapshot.sections and QMessageBox.question(
                    self, "Replace script", "Replace saved sections? Previous revisions will be retained.") != QMessageBox.Yes:
                return
            self.worker = GenerationThread(self.snapshot, self.provider, self.request.toPlainText(), self)
            self.worker.outcome.connect(self._generated)
            self.worker.finished.connect(self._generation_finished)
            for field in (self.title, self.role, self.text, self.request):
                field.setEnabled(False)
            self.generate_button.setEnabled(False)
            self.status.setText("Generating script…")
            self.worker.start()
        self._run(action)

    def _generated(self, revision, error):
        if error:
            self.status.setText(error)
            return
        try:
            self.session.save_script(revision, expected_active_revision_id=self.snapshot.id)
            # Worker still exists until finished; refresh only after that signal.
            self._generated_selection = True
        except Exception as exc:
            self.status.setText(str(exc))

    def _generation_finished(self):
        self.worker.deleteLater()
        self.worker = None
        for field in (self.title, self.role, self.text, self.request):
            field.setEnabled(True)
        self.generate_button.setEnabled(self.provider is not None)
        if getattr(self, "_generated_selection", False):
            self._generated_selection = False
            self._refresh()

    def closeEvent(self, event):
        if self.worker is not None or self.dirty or self.audio.busy or self.preview.busy or self.regeneration.busy or self.visuals.busy or self.visuals.prompt_dirty:
            self.status.setText("Finish generation and save or discard your draft before closing.")
            event.ignore()
            return
        if self.session:
            self.audio.stop()
            self.preview.stop()
            self.session.close()
            self.session = None
        event.accept()

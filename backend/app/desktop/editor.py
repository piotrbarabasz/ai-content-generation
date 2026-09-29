"""Project workspace coordinator; persistence and editing rules live in services."""

import asyncio
import logging

from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget, QToolButton,
)

from app.application.script_generation import ScriptGenerationService
from app.application.automatic_workflow import (AutomaticWorkflow, AutomaticWorkflowBlocked,
                                                 AutomaticWorkflowCanceled, AutomaticWorkflowConfig)
from app.application.pipeline_diagnostics import PipelineDiagnostics
from app.desktop.audio_panel import AudioPanel
from app.desktop.pipeline_driver import DesktopPipelineDriver
from app.desktop.scene_panel import ScenePanel
from app.desktop.scene_planning_panel import ScenePlanningPanel
from app.desktop.timeline_panel import TimelinePanel
from app.desktop.preview_panel import PreviewPanel
from app.desktop.regeneration_panel import RegenerationPanel
from app.desktop.video_plan_panel import VideoPlanPanel
from app.desktop.project_header import ProjectHeader
from app.desktop.project_outline import ProjectOutline
from app.desktop.storyboard_panel import StoryboardPanel
from app.desktop.export_panel import ExportPanel
from app.desktop.script_panel import ScriptPanel


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
                 preview_factory=None, regeneration_factory=None, workflow_mode="manual"):
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
        self.visuals.media_changed.connect(self._visual_media_changed)
        self.visuals.scene_selected.connect(self._visual_scene_selected)
        self.audio.readiness_changed.connect(self._queue_diagnostics)
        self.session = self.snapshot = self.selected_id = None
        self.worker = None
        self.dirty = False
        self.loading = False
        self.pipeline_diagnostics = PipelineDiagnostics()
        self.automatic_busy = False
        self.automatic_loop = None
        self.automatic_task = None
        self.automatic_workflow = None
        self.automatic_driver = None
        self.setWindowTitle("AI Content Studio ? Project editor")
        self.resize(1366, 768)
        self.setMinimumSize(820, 580)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        self.header = ProjectHeader(provider is not None, workflow_mode, self)
        layout.addWidget(self.header)
        self.project_name, self.language = self.header.project_name, self.header.language
        self.workflow_mode = self.header.workflow_mode
        self.workflow_status, self.workflow_summary = self.header.status, self.header.summary
        self.status = self.workflow_summary
        self.buttons = {"Create project": self.header.create_button,
                        "Open project": self.header.open_button}
        self.header.create_button.clicked.connect(lambda: self._choose_project(True))
        self.header.open_button.clicked.connect(lambda: self._choose_project(False))
        self.diagnose_button = self.header.diagnose_button
        self.diagnose_button.clicked.connect(self.diagnose_pipeline)
        self.auto_run_button = self.header.run_button
        self.auto_run_button.clicked.connect(self.run_automatic_workflow)
        self.auto_stop_button = self.header.stop_button
        self.auto_stop_button.clicked.connect(self.stop_automatic_workflow)
        self.workflow_mode.currentIndexChanged.connect(self._workflow_mode_changed)

        # Kept as a synchronized compatibility adapter for existing editing code.
        self.section_choice = QComboBox()
        self.section_choice.currentIndexChanged.connect(self._section_choice_changed)
        self.section_choice.hide()
        self.tabs = QTabWidget()
        self.outline = ProjectOutline(self)
        self.outline.setMinimumWidth(190)
        self.outline.setMaximumWidth(330)
        self.outline.section_activated.connect(self._outline_section_activated)
        self.outline.group_activated.connect(self._outline_group_activated)

        self.video_plan = VideoPlanPanel(provider, self)
        self.video_plan.plan_changed.connect(self._plan_changed)
        self.tabs.addTab(self.video_plan, "Plan")

        self.script_tab = QWidget()
        script_layout = QVBoxLayout(self.script_tab)
        self.sections = QListWidget()
        self.sections.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.sections.currentRowChanged.connect(self._select)
        script_layout.addWidget(self.sections)
        self.sections.hide()
        self.script_panel = ScriptPanel({"new": self.new_section, "save": self.save,
            "discard": self.discard, "split": self.split, "merge": self.merge,
            "up": lambda: self.move(-1), "down": lambda: self.move(1),
            "generate": self.generate, "dirty": self._dirty,
            "resume": lambda: self.video_plan.resume_script_generation()}, parent=self)
        script_layout.addWidget(self.script_panel, 1)
        self.title, self.role, self.text = self.script_panel.title, self.script_panel.role, self.script_panel.text
        self.request, self.generate_button = self.script_panel.request, self.script_panel.generate_button
        self.buttons.update(self.script_panel.buttons)
        self.generate_button.setEnabled(provider is not None)
        self.generate_button.setToolTip("Generate with the configured structured script provider." if provider else
                                       "Generation is unavailable until a structured script provider is configured.")
        self.tabs.addTab(self.script_tab, "Script")
        self.voice_section = QLabel("No narrative section selected.")
        self.visuals_section = QLabel("No narrative section selected.")
        self.storyboard = StoryboardPanel(self)
        self.storyboard.scene_selected.connect(self._storyboard_scene_selected)
        self.storyboard_inspector = QTabWidget()
        self.storyboard_inspector.addTab(self.audio, "Narration")
        self.storyboard_inspector.addTab(self.scene_plans, "Scene plan")
        visual_scroll = QScrollArea()
        visual_scroll.setWidgetResizable(True)
        visual_scroll.setWidget(self.visuals)
        self.storyboard_inspector.addTab(visual_scroll, "Visual")
        storyboard_split = QSplitter(Qt.Horizontal)
        storyboard_split.addWidget(self.storyboard)
        storyboard_split.addWidget(self.storyboard_inspector)
        storyboard_split.setStretchFactor(0, 3)
        storyboard_split.setStretchFactor(1, 2)
        storyboard_split.setChildrenCollapsible(False)
        self.tabs.addTab(storyboard_split, "Storyboard")

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
        self.export_panel = ExportPanel(self)
        export_layout.addWidget(self.export_panel)
        self.export_status = self.export_panel.state
        self.final_render_button = QPushButton("Build / rebuild final render")
        self.final_render_button.clicked.connect(self._rebuild_final_render)
        export_layout.addWidget(self.final_render_button)
        self.advanced_regeneration = QToolButton()
        self.advanced_regeneration.setText("Advanced · selective regeneration")
        self.advanced_regeneration.setCheckable(True)
        self.advanced_regeneration.setChecked(False)
        self.advanced_regeneration.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advanced_regeneration.setArrowType(Qt.RightArrow)
        self.advanced_content = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_content)
        advanced_layout.addWidget(self.regeneration)
        self.advanced_content.hide()
        self.advanced_regeneration.toggled.connect(self.advanced_content.setVisible)
        self.advanced_regeneration.toggled.connect(
            lambda expanded: self.advanced_regeneration.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow))
        export_layout.addWidget(self.advanced_regeneration)
        export_layout.addWidget(self.advanced_content, 1)
        self.tabs.addTab(self.export_tab, "Export")

        workspace = QSplitter(Qt.Horizontal)
        workspace.addWidget(self.outline)
        workspace.addWidget(self.tabs)
        workspace.setStretchFactor(0, 1)
        workspace.setStretchFactor(1, 5)
        workspace.setChildrenCollapsible(False)
        layout.addWidget(workspace, 1)
        for field in (self.title, self.role, self.text):
            field.setEnabled(False)
        self.automatic_timer = QTimer(self)
        self.automatic_timer.setInterval(10)
        self.automatic_timer.timeout.connect(self._automatic_tick)
        self._update_workflow_controls()

    def _section_choice_changed(self, index):
        if self.loading or self.snapshot is None:
            return
        section_id = self.section_choice.itemData(index)
        ids = [section.section_id for section in self.snapshot.sections]
        self.sections.setCurrentRow(ids.index(section_id) if section_id in ids else -1)
        if self.selected_id != section_id:
            self._sync_section_choice()

    def _outline_section_activated(self, section_id):
        if self.snapshot is None:
            return
        row = next((i for i, section in enumerate(self.snapshot.sections)
                    if section.section_id == section_id), -1)
        if row >= 0:
            self.tabs.setCurrentIndex(1)
            self.sections.setCurrentRow(row)
        else:
            self.selected_id = section_id
            self.loading = True
            self.title.clear()
            self.role.setText("body")
            self.text.clear()
            self.loading = False
            self.audio.select_section(None)
            self.scene_plans.select_section(None)
            self.visuals.select_section(None)
            self.storyboard.set_section(None)
            self.section_choice.setCurrentIndex(-1)
            self._update_script_panel_progress()
            self.tabs.setCurrentIndex(0)

    def _outline_group_activated(self, _group_id):
        self.tabs.setCurrentIndex(0)

    def _plan_changed(self, plan):
        self.outline.populate(self.snapshot.sections if self.snapshot else (), plan,
                              self._last_report.diagnostics if getattr(self, "_last_report", None) else (),
                              self.selected_id)
        self._update_script_panel_progress()
        self._queue_diagnostics()

    def _queue_diagnostics(self):
        if self.session is not None:
            QTimer.singleShot(0, self.diagnose_pipeline)

    def _visual_media_changed(self):
        self.preview.timeline_changed(self.timeline.edit)
        self.diagnose_pipeline()

    def _update_script_panel_progress(self):
        plan = self.video_plan.plans.selected() if self.video_plan.plans else None
        try:
            states = self.video_plan.script_service.progress(plan) if plan and self.video_plan.script_service else ()
        except Exception:
            states = ("pending",) * len(plan.groups) if plan else ()
        section = next((s for s in self.snapshot.sections if s.section_id == self.selected_id), None) if self.snapshot else None
        self.script_panel.show_plan_progress(plan, states, section)

    def _storyboard_scene_selected(self, scene_id):
        self.visuals.select_scene_id(scene_id)

    def _visual_scene_selected(self, scene_id):
        self.storyboard.select_scene(scene_id)

    def _sync_section_choice(self):
        index = self.section_choice.findData(self.selected_id)
        self.section_choice.blockSignals(True)
        self.section_choice.setCurrentIndex(index)
        self.section_choice.blockSignals(False)

    def _timeline_changed(self, edit):
        self.preview.timeline_changed(edit)
        self.export_panel.refresh()
        self._queue_diagnostics()

    def _scene_plan_changed(self):
        section = self.snapshot.section(self.selected_id) if self.snapshot and self.selected_id else None
        self.visuals.select_section(section)
        self.storyboard.set_section(section,
            diagnostics=self._last_report.diagnostics if getattr(self, "_last_report", None) else ())
        if self.timeline.services:
            self.timeline.run(self.timeline.refresh)
        self._run(self._bind_regeneration)
        self.diagnose_pipeline()

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
        if self.automatic_busy:
            raise ValueError("Wait for the automatic workflow to finish or stop.")
        if self.regeneration.busy:
            raise ValueError("Wait for regeneration cleanup.")
        if self.worker is not None:
            raise ValueError("Wait for generation to finish.")
        if self.video_plan.busy:
            raise ValueError("Wait for the current planned script group to finish or cancel it.")
        if self.session is None:
            raise ValueError("Create or open a project first.")
        if clean and self.dirty:
            raise ValueError("Save or discard your draft first.")
        if self.visuals.prompt_dirty or self.visuals.context_dirty:
            raise ValueError("Save the scene prompt or visual context draft first.")

    def set_plan_script_busy(self, busy):
        for index in range(1, self.tabs.count()):
            self.tabs.setTabEnabled(index, not busy)
        self.buttons["Create project"].setEnabled(not busy and not self.automatic_busy)
        self.buttons["Open project"].setEnabled(not busy and not self.automatic_busy)
        self.section_choice.setEnabled(not busy and self.session is not None)
        self.outline.setEnabled(not busy)
        self.workflow_mode.setEnabled(not busy and not self.automatic_busy)
        self.auto_run_button.setEnabled(not busy and self.session is not None
                                        and self.workflow_mode.currentData() == "automatic"
                                        and not self.automatic_busy)

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
            if self.automatic_busy or self.worker is not None or self.video_plan.busy or self.dirty or self.audio.busy or self.preview.busy or self.regeneration.busy or self.visuals.busy or self.visuals.prompt_dirty or self.visuals.context_dirty:
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
            self.video_plan.bind(candidate)
            self.storyboard.bind(scene_services)
            self.export_panel.bind(candidate)
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
        self._update_script_panel_progress()
        selected_plan = self.video_plan.plans.selected() if self.video_plan.plans else None
        self.outline.populate(self.snapshot.sections, selected_plan,
                              self._last_report.diagnostics if getattr(self, "_last_report", None) else (),
                              self.selected_id)
        self._bind_regeneration()
        self.status.setText("Saved.")
        self._update_workflow_controls()
        self.diagnose_pipeline()

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
            self.export_panel.refresh()

    def _regenerating(self, busy):
        for index in range(3):
            self.tabs.setTabEnabled(index, not busy)
        self.outline.setEnabled(not busy)
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
        self.diagnose_pipeline()

    def _select(self, row):
        if self.loading:
            return
        if (self.automatic_busy or self.dirty or self.worker is not None or self.audio.busy or self.preview.busy or self.visuals.busy
                or self.regeneration.busy or self.visuals.prompt_dirty):
            self.sections.blockSignals(True)
            ids = [s.section_id for s in self.snapshot.sections]
            self.sections.setCurrentRow(ids.index(self.selected_id) if self.selected_id in ids else -1)
            self.sections.blockSignals(False)
            self._sync_section_choice()
            self.outline.select_section(self.selected_id)
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
        self.storyboard.set_section(section,
            diagnostics=self._last_report.diagnostics if getattr(self, "_last_report", None) else ())
        label = f"Selected section: {section.title}" if section else "No narrative section selected."
        self.voice_section.setText(label)
        self.visuals_section.setText(label)
        self._sync_section_choice()
        if self.selected_id is None:
            self.outline.setCurrentItem(None)
            self.outline.clearSelection()
        else:
            self.outline.select_section(self.selected_id)
        self._update_script_panel_progress()

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
            if self.video_plan.script_artifacts and self.video_plan.script_artifacts.active_binding() is not None:
                raise ValueError("This script is bound to a Video Plan. Use Resume script generation or Start script from new plan explicitly.")
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

    def _workflow_mode_changed(self, *_):
        if self.automatic_busy:
            return
        mode = self.workflow_mode.currentData()
        self.workflow_status.setText("Manual mode" if mode == "manual" else "Automatic mode is ready; start it explicitly.")
        self._update_workflow_controls()

    def _update_workflow_controls(self):
        busy = self.automatic_busy
        has_project = self.session is not None
        self.workflow_mode.setEnabled(not busy)
        self.diagnose_button.setEnabled(has_project and not busy)
        self.auto_run_button.setEnabled(has_project and not busy and not self.video_plan.busy
                                        and self.workflow_mode.currentData() == "automatic")
        self.auto_stop_button.setEnabled(busy and self.automatic_workflow is not None)
        self.buttons["Create project"].setEnabled(not busy)
        self.buttons["Open project"].setEnabled(not busy)
        self.section_choice.setEnabled(not busy and has_project)
        self.project_name.setEnabled(not busy)
        self.language.setEnabled(not busy)
        self.tabs.setEnabled(not busy)
        if self.video_plan.busy:
            for index in range(1, self.tabs.count()):
                self.tabs.setTabEnabled(index, False)
        self.final_render_button.setEnabled(
            not busy and self.regeneration.outputs.findData("project:video_render") >= 0
        )

    def diagnose_pipeline(self):
        if self.session is None:
            self.workflow_summary.setText("Open a project to diagnose its pipeline.")
            return None
        try:
            report = self.pipeline_diagnostics.inspect_project(
                self.session, audio=self.audio, scenes=self.visuals.services,
                timeline=self.timeline.services, unsaved_draft=self.dirty,
                audio_choice=self.audio.voices.currentData(),
                video_plans=self.video_plan.plans,
                plan_script=self.video_plan.script_service,
                video_render=self.export_panel.render_media)
        except Exception:
            logging.getLogger("aics.pipeline").exception("[AICS][PIPELINE][DIAGNOSE][FAIL] unexpected_error")
            self.workflow_summary.setText("Pipeline diagnosis failed unexpectedly. See console for details.")
            return None
        summary = report.summary
        if report.timeline_rejected:
            summary += f"\n{report.timeline_rejected} timeline candidate(s) rejected. See console for exact reasons."
        self._last_report = report
        selected_plan = self.video_plan.plans.selected() if self.video_plan.plans else None
        self.header.show_diagnostics(report, project_name=self.project_name.text(),
                                     language=self.language.text(), plan=selected_plan)
        self.workflow_summary.setText(summary)
        self.outline.populate(self.snapshot.sections, selected_plan, report.diagnostics, self.selected_id)
        current_section = next((section for section in self.snapshot.sections
                                if section.section_id == self.selected_id), None)
        self.storyboard.set_section(current_section, diagnostics=report.diagnostics)
        self.workflow_status.setText("Diagnostics complete; no media was generated.")
        return report

    def run_automatic_workflow(self):
        if self.automatic_busy or self.workflow_mode.currentData() != "automatic":
            return
        try:
            self._ready()
            if self.audio.services is None:
                raise ValueError("Audio services are not configured for this project.")
            if self.visuals.services is None:
                raise ValueError("Scene and visual services are not configured for this project.")
            if self.timeline.services is None:
                raise ValueError("Timeline services are not configured for this project.")
            choice = self.audio.voices.currentData()
            if choice is None:
                raise ValueError("Choose a TTS voice before starting the automatic workflow.")
            generator_id = self.visuals.image_generator.currentData()
            if self.visuals.image_generator_options and generator_id is None:
                raise ValueError("Choose an image generator before starting the automatic workflow.")
            visual_services = self.visuals.services
            generation = visual_services._generation_for(generator_id)
            if generation.provider is None:
                raise ValueError("Configure the selected image generator before starting the automatic workflow.")
            resolution = self.visuals.resolution.currentData()
            if resolution != "draft" and (visual_services.upscale is None or visual_services.upscale.provider is None):
                raise ValueError("Configure the optional upscaler before requesting final-resolution images.")
            selected_plan = self.video_plan.plans.selected() if self.video_plan.plans else None
            if selected_plan is None:
                contexts = visual_services.prompts.prompts
                if contexts.current_context("film_brief") is None or contexts.current_context("visual_style") is None:
                    raise ValueError("Save Film Brief and Visual Style before automatic processing.")
            config = AutomaticWorkflowConfig(
                choice, generator_id, self.visuals.orientation.currentData(),
                resolution, "original")
            self.automatic_driver = DesktopPipelineDriver(self, config)
            self.automatic_workflow = AutomaticWorkflow(self.automatic_driver)
            self.automatic_loop = asyncio.new_event_loop()
            self.automatic_task = self.automatic_loop.create_task(self.automatic_workflow.run(config))
            self.automatic_busy = True
            self.workflow_status.setText("Automatic workflow started; existing valid artifacts will be reused.")
            self._update_workflow_controls()
            self.automatic_timer.start()
        except (ValueError, RuntimeError) as exc:
            self.workflow_status.setText(str(exc))

    def stop_automatic_workflow(self):
        if not self.automatic_busy or self.automatic_workflow is None:
            return
        try:
            self.automatic_workflow.cancel()
            self.workflow_status.setText("Cancellation requested; waiting for the current provider operation to stop.")
            self._update_workflow_controls()
        except Exception:
            logging.getLogger("aics.pipeline").exception("[AICS][PIPELINE][AUTO][CANCEL] cancel_error")
            self.workflow_status.setText("Cancellation requested; provider cleanup is still pending.")

    def _automatic_tick(self):
        loop, task = self.automatic_loop, self.automatic_task
        if loop is None or task is None:
            return
        loop.call_soon(loop.stop)
        loop.run_forever()
        if not task.done():
            return
        self.automatic_timer.stop()
        try:
            result = task.result()
            self.workflow_status.setText(
                f"Automatic workflow complete. Timeline ready: {result.timeline_clips} clips.")
            self.workflow_summary.setText(
                f"Sections: {result.sections}\nScenes: {result.scenes}\n"
                f"Timeline clips: {result.timeline_clips}\nDuration: {result.duration}\n"
                f"Reused stages: {result.skipped}"
                + (f"\nScript groups: {result.plan_script_groups_completed}/{result.plan_script_groups_total}"
                   if result.plan_script_groups_total else ""))
            if self.timeline.services:
                self.timeline.refresh()
                self.preview.timeline_changed(self.timeline.edit)
            self._bind_regeneration()
            self.diagnose_pipeline()
        except AutomaticWorkflowBlocked as exc:
            self.workflow_status.setText(f"Automatic workflow stopped at {exc.stage}.")
            suffix = f"\nSection: {exc.section}" if exc.section else ""
            suffix += f"\nScene: {exc.scene}" if exc.scene else ""
            self.workflow_summary.setText(f"{exc.reason}{suffix}\nSee console for diagnostics.")
        except AutomaticWorkflowCanceled:
            self.workflow_status.setText("Automatic workflow canceled. Completed artifacts were retained.")
            self.workflow_summary.setText("Click Run / Resume automatic workflow to continue from retained state.")
        except Exception as exc:
            logging.getLogger("aics.pipeline").exception("[AICS][PIPELINE][AUTO][FAIL] unexpected_error")
            self.workflow_status.setText("Automatic workflow stopped after an unexpected error.")
            self.workflow_summary.setText(f"{type(exc).__name__}: {str(exc)[:240]}\nSee console for diagnostics.")
        finally:
            self.automatic_busy = False
            self._update_workflow_controls()
            loop.close()
            self.automatic_loop = self.automatic_task = None
            self.automatic_workflow = self.automatic_driver = None

    def closeEvent(self, event):
        if self.automatic_busy or self.worker is not None or self.video_plan.busy or self.dirty or self.audio.busy or self.preview.busy or self.regeneration.busy or self.visuals.busy or self.visuals.prompt_dirty or self.visuals.context_dirty:
            self.status.setText("Finish generation and save or discard your draft before closing.")
            event.ignore()
            return
        if self.session:
            self.audio.stop()
            self.preview.stop()
            self.session.close()
            self.session = None
        event.accept()

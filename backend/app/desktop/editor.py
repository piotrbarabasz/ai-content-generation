"""Section editor presentation; persistence and editing rules live in services."""

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget, QDockWidget,
)

from app.application.script_generation import ScriptGenerationService
from app.desktop.audio_panel import AudioPanel


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
    def __init__(self, projects, provider=None, audio_factory=None):
        super().__init__()
        self.projects, self.provider = projects, provider
        self.audio_factory = audio_factory
        self.audio = AudioPanel(self)
        audio_dock = QDockWidget("Section audio", self)
        audio_dock.setWidget(self.audio)
        self.addDockWidget(Qt.BottomDockWidgetArea, audio_dock)
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
        self.sections = QListWidget()
        self.sections.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.sections.currentRowChanged.connect(self._select)
        layout.addWidget(self.sections)
        form = QFormLayout()
        self.title, self.role = QLineEdit(), QLineEdit("body")
        self.text = QPlainTextEdit()
        form.addRow("Section title", self.title)
        form.addRow("Role", self.role)
        form.addRow("Text", self.text)
        layout.addLayout(form)
        for field in (self.title, self.role, self.text):
            field.textChanged.connect(self._dirty)
        actions = QHBoxLayout()
        layout.addLayout(actions)
        for label, action in (("New section", self.new_section), ("Save", self.save),
                              ("Discard draft", self.discard), ("Split at cursor", self.split),
                              ("Merge selected", self.merge), ("Move up", lambda: self.move(-1)),
                              ("Move down", lambda: self.move(1))):
            self._button(actions, label, action)
        self.request = QPlainTextEdit()
        self.request.setPlaceholderText("Describe the script to generate (replaces saved sections).")
        self.request.setMaximumHeight(90)
        layout.addWidget(self.request)
        self.generate_button = QPushButton("Generate replacement script")
        self.generate_button.clicked.connect(self.generate)
        self.generate_button.setEnabled(provider is not None)
        self.generate_button.setToolTip("Generate with the configured structured script provider." if provider else
                                       "Generation is unavailable until a structured script provider is configured.")
        layout.addWidget(self.generate_button)
        self.status = QLabel("Create or open a project." if provider else
                             "Create or open a project. Generation requires a configured provider.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for field in (self.title, self.role, self.text):
            field.setEnabled(False)

    def _button(self, layout, label, callback):
        button = QPushButton(label)
        button.clicked.connect(callback)
        layout.addWidget(button)
        self.buttons[label] = button

    def _dirty(self):
        if not self.loading:
            self.dirty = True
            self.audio.set_draft(True)

    def _ready(self, clean=True):
        if self.worker is not None:
            raise ValueError("Wait for generation to finish.")
        if self.session is None:
            raise ValueError("Create or open a project first.")
        if clean and self.dirty:
            raise ValueError("Save or discard your draft first.")

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
            if self.worker is not None or self.dirty or self.audio.busy:
                raise ValueError("Finish generation and save or discard your draft first.")
            candidate = (self.projects.create(path, name=self.project_name.text(), language=self.language.text())
                         if create else self.projects.open(path))
            try:
                snapshot, project = candidate.active_script, candidate.project
                audio_services = self.audio_factory(candidate) if self.audio_factory else None
            except Exception:
                candidate.close()
                raise
            if self.session:
                self.audio.stop()
                self.session.close()
            self.session, self.snapshot = candidate, snapshot
            self.audio.bind(audio_services, project.language)
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
        self.sections.clear()
        for section in self.snapshot.sections:
            item = QListWidgetItem(section.title)
            item.setData(Qt.UserRole, section.section_id)
            self.sections.addItem(item)
        ids = [s.section_id for s in self.snapshot.sections]
        row = ids.index(selected) if selected in ids else (0 if ids else -1)
        self.sections.setCurrentRow(row)
        self.sections.blockSignals(False)
        self.loading = False
        self.dirty = False
        self._select(row)
        self.status.setText("Saved.")

    def _select(self, row):
        if self.loading:
            return
        if self.dirty or self.worker is not None:
            self.sections.blockSignals(True)
            ids = [s.section_id for s in self.snapshot.sections]
            self.sections.setCurrentRow(ids.index(self.selected_id) if self.selected_id in ids else -1)
            self.sections.blockSignals(False)
            self.status.setText("Save or discard your draft and finish generation before switching sections.")
            return
        section = self.snapshot.sections[row] if self.snapshot and row >= 0 else None
        self.selected_id = section.section_id if section else None
        self.loading = True
        self.title.setText(section.title if section else "")
        self.role.setText(section.role if section else "body")
        self.text.setPlainText(section.text if section else "")
        self.loading = False
        self.audio.select_section(section)

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
        if self.worker is not None or self.dirty or self.audio.busy:
            self.status.setText("Finish generation and save or discard your draft before closing.")
            event.ignore()
            return
        if self.session:
            self.audio.stop()
            self.session.close()
            self.session = None
        event.accept()

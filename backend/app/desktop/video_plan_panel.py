"""Focused editor for the optional Social / Standard Video Plan."""

from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from app.application.video_planning import VideoPlanningService
from app.domain.video_plan import VIDEO_FORMATS, VideoFormat


class VideoPlanPanel(QWidget):
    def __init__(self, provider=None, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.session = self.plans = self.plan = self.context_service = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.format = QComboBox()
        self.format.addItem("Social — 30–60 sec", VideoFormat.SOCIAL.value)
        self.format.addItem("Standard — 9–11 min", VideoFormat.STANDARD.value)
        self.duration = QSpinBox()
        self.topic = QPlainTextEdit()
        self.topic.setMaximumHeight(90)
        self.topic.setPlaceholderText("Describe the video topic or idea")
        form.addRow("Format", self.format)
        form.addRow("Target duration (seconds)", self.duration)
        form.addRow("Topic / idea", self.topic)
        layout.addLayout(form)
        self.format.currentIndexChanged.connect(self._profile_changed)
        buttons = QHBoxLayout()
        self.generate_button = QPushButton("Generate plan")
        self.regenerate_button = QPushButton("Regenerate plan")
        self.apply_button = QPushButton("Apply visual context")
        self.generate_button.clicked.connect(self.generate)
        self.regenerate_button.clicked.connect(self.generate)
        self.apply_button.clicked.connect(self.apply_visual_context)
        for button in (self.generate_button, self.regenerate_button, self.apply_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.details = QLabel("Choose a format and describe a topic.")
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.outline = QPlainTextEdit()
        self.outline.setReadOnly(True)
        layout.addWidget(self.outline, 1)
        self.status = QLabel("Open a project to generate a plan.")
        layout.addWidget(self.status)
        self._profile_changed()

    def _profile_changed(self, *_):
        spec = VIDEO_FORMATS[VideoFormat(self.format.currentData())]
        self.duration.setRange(spec.min_duration_seconds, spec.max_duration_seconds)
        self.duration.setValue(spec.default_duration_seconds)

    def bind(self, session):
        from app.application.visual_prompts import VisualPromptService
        from app.storage.local_store import LocalArtifactStore
        from app.storage.video_plans import ProjectVideoPlans
        self.session = session
        store = LocalArtifactStore.for_project(session.repository)
        self.plans = ProjectVideoPlans(session.repository, store)
        self.context_service = VisualPromptService(
            _ContextPort(session, store), self.provider,
            generation_identity={"provider": "video_plan_apply"})
        self.plan = self.plans.selected()
        self._render()

    def generate(self):
        try:
            if self.session is None:
                raise ValueError("Open a project first.")
            if self.provider is None:
                raise ValueError("Configure a structured plan provider first.")
            active = self.plans.selected()
            service = VideoPlanningService(self.plans, self.provider)
            self.plan = service.generate(project_id=self.session.project.id,
                                         language=self.session.project.language,
                                         video_format=self.format.currentData(),
                                         target_duration_seconds=self.duration.value(),
                                         topic=self.topic.toPlainText(),
                                         parent_revision_id=active.id if active else None)
            self._render()
            self.status.setText("Plan generated and retained.")
        except Exception as exc:
            self.status.setText(str(exc))

    def apply_visual_context(self):
        try:
            if self.plan is None:
                raise ValueError("Generate or open a plan first.")
            prompts = self.context_service.prompts
            for kind, text in (("film_brief", self.plan.film_brief), ("visual_style", self.plan.visual_style)):
                parent = prompts.current_context(kind)
                self.context_service.pin_context(kind, text, parent_revision_id=parent.id if parent else None)
            self.status.setText("Plan visual context was added to prompt context history.")
        except Exception as exc:
            self.status.setText(str(exc))

    def _render(self):
        if self.plan is None:
            self.details.setText("No Video Plan is selected. Existing project workflows remain available.")
            self.outline.clear()
            return
        plan = self.plan
        self.format.setCurrentIndex(self.format.findData(plan.format.value))
        self.duration.setValue(plan.target_duration_seconds)
        self.topic.setPlainText(plan.topic)
        self.details.setText(f"{plan.working_title}\n{plan.format.value.title()} · {plan.target_duration_seconds} sec · "
                             f"~{plan.target_word_count} words · ~{plan.target_scene_count} visuals\n\n"
                             f"Film Brief\n{plan.film_brief}\n\nVisual Style\n{plan.visual_style}")
        lines = []
        for group in plan.groups:
            lines.append(f"{group.kind.title()}: {group.title} ({group.target_duration_seconds}s)")
            for section in group.sections:
                lines.append(f"  • {section.title} — {section.purpose} ({section.target_duration_seconds}s, ~{section.target_word_count} words, ~{section.target_scene_count} visuals)")
        self.outline.setPlainText("\n".join(lines))


class _ContextPort:
    """Narrow adapter to the existing visual prompt context repository."""
    def __init__(self, session, store):
        from app.storage.visual_prompts import ProjectVisualPrompts
        self.prompts = ProjectVisualPrompts(session.repository, store)
        self.project_id = session.project.id
    def context(self, revision_id): return self.prompts.context(revision_id)
    def save_context(self, revision): return self.prompts.save_context(revision)
    def current_context(self, kind): return self.prompts.current_context(kind)

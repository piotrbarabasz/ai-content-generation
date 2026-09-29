"""Focused editor for the optional Social / Standard Video Plan."""

from PySide6.QtCore import QThread, Signal, QTimer
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from app.application.video_planning import VideoPlanningService
from app.domain.video_plan import VIDEO_FORMATS, VideoFormat


class PlanGroupThread(QThread):
    outcome = Signal(object, str)

    def __init__(self, provider, prepared, parent=None):
        super().__init__(parent)
        self.provider, self.prepared = provider, prepared

    def run(self):
        try:
            self.outcome.emit(self.provider.generate_structured(self.prepared["prompt"], self.prepared["schema"]), "")
        except Exception as exc:
            self.outcome.emit(None, str(exc))


class VideoPlanPanel(QWidget):
    plan_changed = Signal(object)

    def __init__(self, provider=None, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.session = self.plans = self.plan = self.context_service = None
        self.store = self.script_service = self.script_artifacts = None
        self.script_worker = None
        self._script_running = False
        self._script_plan = self._script_group = self._prepared_group = None
        self._expected_script_revision_id = None
        self._cancel_script_requested = False
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
        script_actions = QHBoxLayout()
        self.resume_script_button = QPushButton("Resume script generation")
        self.new_script_button = QPushButton("Start script from new plan")
        self.cancel_script_button = QPushButton("Cancel after current group")
        self.resume_script_button.clicked.connect(self.resume_script_generation)
        self.new_script_button.clicked.connect(self.confirm_start_new_script)
        self.cancel_script_button.clicked.connect(self.cancel_script_generation)
        for button in (self.resume_script_button, self.new_script_button, self.cancel_script_button):
            script_actions.addWidget(button)
        layout.addLayout(script_actions)
        self.script_progress = QLabel("Script groups: 0/0")
        layout.addWidget(self.script_progress)
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
        from app.storage.plan_script import ProjectPlanScripts
        from app.storage.video_plans import ProjectVideoPlans
        self.session = session
        self.store = LocalArtifactStore.for_project(session.repository)
        self.plans = ProjectVideoPlans(session.repository, self.store)
        self.script_artifacts = ProjectPlanScripts(session.repository, self.store, self.plans)
        from app.application.planned_script_generation import PlanScriptGenerationService
        self.script_service = PlanScriptGenerationService(session, self.provider, self.plans, self.script_artifacts)
        self.context_service = VisualPromptService(
            _ContextPort(session, self.store), self.provider,
            generation_identity={"provider": "video_plan_apply"})
        self.plan = self.plans.selected()
        self._render()
        self._script_progress()

    @property
    def busy(self):
        return self._script_running

    def _script_ready(self):
        if self.session is None or self.plan is None:
            raise ValueError("Select or generate a Video Plan first.")
        if self.provider is None:
            raise ValueError("Configure a structured script provider first.")
        editor = self.parent()
        if editor is not None and hasattr(editor, "_ready"):
            editor._ready()

    def resume_script_generation(self):
        self._start_script_generation(replace_current=False)

    def confirm_start_new_script(self):
        answer = QMessageBox.question(self, "Replace current script",
            "Start a new script from this plan? The current script remains in project history, and the active script will switch to a new empty revision.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer == QMessageBox.Yes:
            self._start_script_generation(replace_current=True)

    def start_new_script_confirmed(self):
        """Explicit command for controllers/tests that already confirmed replacement."""
        self._start_script_generation(replace_current=True)

    def _start_script_generation(self, *, replace_current):
        try:
            self._script_ready()
            self.script_service.start_or_resume(self.plans.selected(), replace_current=replace_current)
            self.plan = self.plans.selected()
            self._cancel_script_requested = False
            self._script_running = True
            if self.parent() is not None and hasattr(self.parent(), "set_plan_script_busy"):
                self.parent().set_plan_script_busy(True)
            self._script_progress()
            self._run_next_script_group()
        except Exception as exc:
            self.status.setText(str(exc))

    def cancel_script_generation(self):
        self._cancel_script_requested = True
        cancel = getattr(self.provider, "cancel", None)
        if self.script_worker is not None and callable(cancel):
            cancel()
        self.status.setText("Cancellation requested; a validated current group may be cached for resume.")

    def _run_next_script_group(self):
        if self._cancel_script_requested:
            self._script_running = False
            if self.parent() is not None and hasattr(self.parent(), "set_plan_script_busy"):
                self.parent().set_plan_script_busy(False)
            self._script_progress()
            self.status.setText("Script generation canceled between groups. Completed groups were retained.")
            return
        try:
            states = self.script_service.progress(self.plan)
            for group, state in zip(self.plan.groups, states):
                if state == "complete":
                    continue
                if state != "pending":
                    raise ValueError("Active script contains an inconsistent partial plan group.")
                self._script_group = group
                self._expected_script_revision_id = self.session.active_script.id
                self._prepared_group = self.script_service.prepare_group(self.plan, group)
                if self._prepared_group["cached"] is not None:
                    result = self._prepared_group["cached"]
                    self.script_service.commit_group(self.plan, group, result,
                        expected_active_revision_id=self._expected_script_revision_id)
                    self._render()
                    self._script_progress()
                    if self.parent() is not None and hasattr(self.parent(), "_refresh"):
                        self.parent()._refresh()
                    QTimer.singleShot(0, self._run_next_script_group)
                    return
                self.status.setText(f"Generating script group: {group.title}")
                self.script_worker = PlanGroupThread(self.provider, self._prepared_group, self)
                self.script_worker.outcome.connect(self._script_group_ready)
                self.script_worker.start()
                self._update_script_buttons()
                return
            self._script_progress()
            self._script_running = False
            if self.parent() is not None and hasattr(self.parent(), "set_plan_script_busy"):
                self.parent().set_plan_script_busy(False)
            self.status.setText("All plan script groups are complete.")
        except Exception as exc:
            self._script_running = False
            if self.parent() is not None and hasattr(self.parent(), "set_plan_script_busy"):
                self.parent().set_plan_script_busy(False)
            self.status.setText(str(exc))

    def _script_group_ready(self, payload, error):
        worker, self.script_worker = self.script_worker, None
        if worker is not None:
            worker.deleteLater()
        try:
            if error:
                raise ValueError(error)
            result = self.script_service.validate_and_cache(self.plan, self._script_group,
                                                             self._prepared_group, payload)
            if self._cancel_script_requested:
                self._script_running = False
                if self.parent() is not None and hasattr(self.parent(), "set_plan_script_busy"):
                    self.parent().set_plan_script_busy(False)
                self.status.setText("Canceled after the current response; validated output was cached for Resume.")
                self._script_progress()
                self._update_script_buttons()
                return
            self.script_service.commit_group(self.plan, self._script_group, result,
                expected_active_revision_id=self._expected_script_revision_id)
            self._render()
            self._script_progress()
            if self.parent() is not None and hasattr(self.parent(), "_refresh"):
                self.parent()._refresh()
            self._script_progress()
            QTimer.singleShot(0, self._run_next_script_group)
        except Exception as exc:
            self._script_running = False
            if self.parent() is not None and hasattr(self.parent(), "set_plan_script_busy"):
                self.parent().set_plan_script_busy(False)
            self.status.setText(str(exc))
            self._script_progress()
        finally:
            self._update_script_buttons()

    def _script_progress(self):
        if self.plan is None or self.script_service is None:
            self.script_progress.setText("Script groups: 0/0")
            self._update_script_buttons()
            return
        try:
            states = self.script_service.progress(self.plan)
            completed = sum(state == "complete" for state in states)
            self.script_progress.setText(f"Script groups: {completed}/{len(states)} complete")
        except Exception as exc:
            self.script_progress.setText(f"Script progress unavailable: {exc}")
        self._update_script_buttons()

    def _update_script_buttons(self):
        has_plan = self.plan is not None and self.session is not None
        self.resume_script_button.setEnabled(has_plan and not self.busy and self.provider is not None)
        self.new_script_button.setEnabled(has_plan and not self.busy and self.provider is not None)
        self.cancel_script_button.setEnabled(self.busy)
        for button in (self.generate_button, self.regenerate_button, self.apply_button):
            button.setEnabled(not self.busy)

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
            self.plan_changed.emit(None)
            return
        plan = self.plan
        self.format.setCurrentIndex(self.format.findData(plan.format.value))
        self.duration.setValue(plan.target_duration_seconds)
        self.topic.setPlainText(plan.topic)
        self.details.setText(f"{plan.working_title}\n{plan.format.value.title()} · {plan.target_duration_seconds} sec · "
                             f"~{plan.target_word_count} words · ~{plan.target_scene_count} visuals\n\n"
                             f"Film Brief\n{plan.film_brief}\n\nVisual Style\n{plan.visual_style}")
        lines = []
        try:
            states = self.script_service.progress(plan) if self.script_service else ("pending",) * len(plan.groups)
        except Exception:
            states = ("pending",) * len(plan.groups)
        for group, state in zip(plan.groups, states):
            lines.append(f"[{state}] {group.kind.title()}: {group.title} ({group.target_duration_seconds}s)")
            for section in group.sections:
                lines.append(f"  • {section.title} — {section.purpose} ({section.target_duration_seconds}s, ~{section.target_word_count} words, ~{section.target_scene_count} visuals)")
        self.outline.setPlainText("\n".join(lines))
        self.plan_changed.emit(plan)


class _ContextPort:
    """Narrow adapter to the existing visual prompt context repository."""
    def __init__(self, session, store):
        from app.storage.visual_prompts import ProjectVisualPrompts
        self.prompts = ProjectVisualPrompts(session.repository, store)
        self.project_id = session.project.id
    def context(self, revision_id): return self.prompts.context(revision_id)
    def save_context(self, revision): return self.prompts.save_context(revision)
    def current_context(self, kind): return self.prompts.current_context(kind)

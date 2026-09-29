"""Compact project identity, workflow actions and diagnostic stage summary."""

from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QVBoxLayout, QWidget)


class ProjectHeader(QWidget):
    def __init__(self, provider_configured=True, workflow_mode="manual", parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        identity = QHBoxLayout()
        root.addLayout(identity)
        self.project_name = QLineEdit("My project")
        self.project_name.setAccessibleName("Project name")
        self.language = QLineEdit("en")
        self.language.setMaximumWidth(90)
        self.language.setAccessibleName("Project language")
        identity.addWidget(QLabel("Project"))
        identity.addWidget(self.project_name, 1)
        identity.addWidget(QLabel("Language"))
        identity.addWidget(self.language)
        self.workflow_mode = QComboBox()
        self.workflow_mode.addItem("Manual", "manual")
        self.workflow_mode.addItem("Automatic", "automatic")
        self.workflow_mode.setCurrentIndex(max(0, self.workflow_mode.findData(workflow_mode)))
        identity.addWidget(self.workflow_mode)
        self.create_button = QPushButton("Create project")
        self.open_button = QPushButton("Open project")
        identity.addWidget(self.create_button)
        identity.addWidget(self.open_button)
        self.run_button = QPushButton("Run / Resume")
        self.stop_button = QPushButton("Stop")
        self.diagnose_button = QPushButton("Diagnose")
        identity.addWidget(self.run_button)
        identity.addWidget(self.stop_button)
        identity.addWidget(self.diagnose_button)
        initial_status = ("Create or open a project." if provider_configured else
                          "Create or open a project. Script and image generation need configured providers.")
        self.status = QLabel(initial_status)
        self.status.setWordWrap(True)
        identity.addWidget(self.status, 1)
        self.project_summary = QLabel("Project · —   Format · —   Target · —   Actual · —")
        root.addWidget(self.project_summary)
        self.stage_labels = {}
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        root.addLayout(grid)
        for column, name in enumerate(("Plan", "Script", "Voice", "Visuals", "Timeline", "Export")):
            label = QLabel(f"{name}  —")
            label.setAccessibleName(f"{name} readiness")
            grid.addWidget(label, 0, column)
            self.stage_labels[name] = label
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

    def set_status(self, text):
        self.status.setText(text)

    def show_diagnostics(self, report, *, project_name, language, plan=None):
        if plan is None:
            profile, target = "—", "—"
        else:
            profile = plan.format.value.upper()
            target = self._clock(plan.target_duration_seconds)
        actual = "—"
        if report.narration_duration_review:
            text = report.narration_duration_review
            actual = text.split("measured ", 1)[-1].split("s", 1)[0] + "s" if "measured " in text else text
        self.project_summary.setText(
            f"{project_name}   {profile}   Target {target}   Actual {actual}   Language {language.upper()}")
        script = report.script_group_progress if report.script_group_progress != "NOT USED" else (
            "Ready" if report.section_count else "Pending")
        timeline = ("Ready" if report.timeline_expected and not report.timeline_rejected
                    else "Pending" if not report.timeline_expected else "Review")
        export_ready = report.export_ready if report.final_render_ready is None else report.final_render_ready
        export = "Ready" if export_ready else "Pending"
        values = {
            "Plan": ("Unavailable" if report.plan_summary == "UNAVAILABLE" else
                     "Not used" if plan is None else f"{plan.format.value.upper()} · Ready"),
            "Script": script,
            "Voice": f"{report.voice_ready}/{report.section_count}",
            "Visuals": f"{report.visuals_ready}/{report.visual_count}",
            "Timeline": timeline,
            "Export": export,
        }
        for name, value in values.items():
            self.stage_labels[name].setText(f"{name}  {value}")

    @staticmethod
    def _clock(seconds):
        minutes, remainder = divmod(int(seconds), 60)
        return f"{minutes}:{remainder:02d}"

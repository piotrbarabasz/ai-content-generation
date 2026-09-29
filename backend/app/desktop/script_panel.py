"""Narrative text workspace with plan-group progress kept beside the draft."""

from PySide6.QtWidgets import (QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)


class ScriptPanel(QWidget):
    def __init__(self, handlers, *, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        self.plan_progress = QLabel("No plan-driven script progress.")
        root.addWidget(self.plan_progress)
        self.resume_button = QPushButton("Resume missing groups")
        self.resume_button.clicked.connect(handlers["resume"])
        self.resume_button.hide()
        root.addWidget(self.resume_button)
        form = QFormLayout()
        self.title, self.role = QLineEdit(), QLineEdit("body")
        self.text = QPlainTextEdit()
        self.text.setAccessibleName("Narrative section text")
        form.addRow("Section title", self.title)
        form.addRow("Role", self.role)
        form.addRow("Narrative", self.text)
        root.addLayout(form, 1)
        self.buttons = {}
        actions = QHBoxLayout()
        root.addLayout(actions)
        for label, key in (("New section", "new"), ("Save", "save"),
                           ("Discard draft", "discard"), ("Split at cursor", "split"),
                           ("Merge selected", "merge"), ("Move up", "up"),
                           ("Move down", "down")):
            button = QPushButton(label)
            button.clicked.connect(handlers[key])
            actions.addWidget(button)
            self.buttons[label] = button
        self.request = QPlainTextEdit()
        self.request.setMaximumHeight(62)
        self.request.setPlaceholderText("Describe a replacement script")
        root.addWidget(self.request)
        self.generate_button = QPushButton("Generate replacement script")
        self.generate_button.clicked.connect(handlers["generate"])
        root.addWidget(self.generate_button)
        self.title.textChanged.connect(handlers["dirty"])
        self.role.textChanged.connect(handlers["dirty"])
        self.text.textChanged.connect(handlers["dirty"])

    def show_plan_progress(self, plan, states, section):
        if plan is None:
            self.plan_progress.setText("No plan-driven script progress.")
            self.resume_button.hide()
            return
        complete = sum(state == "complete" for state in states)
        self.plan_progress.setText(f"Script generation · {complete}/{len(states)} groups ready")
        self.resume_button.show()
        self.resume_button.setEnabled(complete < len(states))
        if section is None:
            return
        from app.domain.plan_script import planned_section_identity
        group_position = 0
        for group in plan.groups:
            group_position += 1
            for planned in group.sections:
                if planned_section_identity(plan.project_id, planned.id) == section.section_id:
                    actual_words = len(section.text.split())
                    self.plan_progress.setText(
                        f"{group.title} · {planned.title} · target {planned.target_word_count} words / "
                        f"{planned.target_duration_seconds // 60}:{planned.target_duration_seconds % 60:02d} · "
                        f"actual {actual_words} words · {complete}/{len(states)} groups ready")
                    return

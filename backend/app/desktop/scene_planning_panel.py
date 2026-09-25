"""Qt review surface for retained scene-plan proposals and acceptances."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget


class ScenePlanningPanel(QWidget):
    plan_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.services = self.section = self.view = None
        layout = QVBoxLayout(self)
        self.section_label = QLabel("No narrative section selected.")
        self.section_label.setWordWrap(True)
        layout.addWidget(self.section_label)
        self.state = QLabel("Scene planning services are not configured.")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.scenes = QListWidget()
        self.scenes.currentRowChanged.connect(self._show_scene)
        layout.addWidget(self.scenes, 1)
        self.detail = QLabel("No scene proposal exists.")
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.detail)
        actions = QHBoxLayout()
        layout.addLayout(actions)
        self.buttons = {}
        for label, action in (
            ("Generate scene plan", self.generate),
            ("Accept scene plan", self.accept),
            ("Regenerate scene plan", self.regenerate),
            ("Rebuild timing", self.retime),
        ):
            button = QPushButton(label)
            button.clicked.connect(action)
            actions.addWidget(button)
            self.buttons[label] = button
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._enable()

    def bind(self, services):
        self.services, self.section, self.view = services, None, None
        self.scenes.clear()
        self.section_label.setText("No narrative section selected.")
        self.state.setText("Select a section." if services else "Scene planning services are not configured.")
        self.detail.setText("No scene proposal exists.")
        self.status.clear()
        self._enable()

    def select_section(self, section):
        self.section = section
        self.section_label.setText(f"Selected section: {section.title}" if section else
                                   "No narrative section selected.")
        self.refresh()

    def refresh(self):
        self.scenes.clear()
        self.view = None
        if self.services is None or self.section is None:
            self.state.setText("Select a section." if self.services else
                               "Scene planning services are not configured.")
            self.detail.setText("No scene proposal exists.")
            self._enable()
            return
        try:
            self.view = self.services.plan_state(self.section)
            labels = {
                "none": "No proposal exists for this saved section revision.",
                "proposal": "Proposal awaiting review. It has not been accepted.",
                "accepted": "The current saved section revision has an accepted scene plan.",
            }
            self.state.setText(labels[self.view.state])
            for scene in self.view.scenes:
                item = QListWidgetItem(f"Scene {scene.index} · {scene.time_label}")
                item.setData(Qt.UserRole, scene.index - 1)
                item.setToolTip(scene.text)
                self.scenes.addItem(item)
            self.scenes.setCurrentRow(0 if self.view.scenes else -1)
            if not self.view.scenes:
                self.detail.setText("No scene proposal exists.")
        except Exception as exc:
            self.state.setText(str(exc))
        self._enable()

    def _show_scene(self, row):
        if self.view is None or not 0 <= row < len(self.view.scenes):
            self.detail.setText("No scene selected.")
            return
        scene = self.view.scenes[row]
        self.detail.setText(
            f"Scene {scene.index}\n\nSource text:\n{scene.text}\n\n"
            f"Visual description:\n{scene.visual_description}\n\nTiming: {scene.time_label}"
        )

    def _enable(self):
        ready = self.services is not None and self.section is not None
        state = self.view.state if self.view else "none"
        self.buttons["Generate scene plan"].setEnabled(ready and state == "none")
        self.buttons["Accept scene plan"].setEnabled(ready and state == "proposal")
        self.buttons["Regenerate scene plan"].setEnabled(ready and state in ("proposal", "accepted"))
        self.buttons["Rebuild timing"].setEnabled(ready and state == "accepted")

    def _act(self, callback, message):
        try:
            self.view = callback()
            self.status.setText(message)
            self.refresh()
            self.status.setText(message)
            self.plan_changed.emit()
        except Exception as exc:
            self.status.setText(str(exc))
            self._enable()

    def generate(self):
        if self.services and self.section:
            self._act(lambda: self.services.suggest_scene_plan(self.section), "Scene proposal generated for review.")

    def regenerate(self):
        if self.services and self.section:
            self._act(lambda: self.services.suggest_scene_plan(self.section),
                      "New scene proposal generated; previous history was retained.")

    def accept(self):
        if self.services and self.section and self.view and self.view.plan_id:
            plan_id = self.view.plan_id
            self._act(lambda: self.services.accept_scene_plan(self.section, plan_id), "Scene plan accepted.")

    def retime(self):
        if self.services and self.section:
            self._act(lambda: self.services.rebuild_scene_timing(self.section), "Scene timing rebuilt.")


__all__ = ["ScenePlanningPanel"]

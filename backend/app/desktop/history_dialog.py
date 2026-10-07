"""Compact retained-choice browser; restoration is an explicit user action."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QTextEdit, QVBoxLayout)


class HistoryDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Project history")
        self.resize(850, 550)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Restore a retained choice. Newer versions remain available.\n"
                               "Outputs are checked against current inputs; restoring does not bypass freshness."))
        body = QHBoxLayout()
        self.items = QListWidget()
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        body.addWidget(self.items, 1)
        body.addWidget(self.details, 2)
        layout.addLayout(body)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.restore_button = QPushButton("Restore this version")
        self.restore_button.clicked.connect(self.restore_selected)
        layout.addWidget(self.restore_button)
        self.items.currentItemChanged.connect(self.show_choice)
        self.reload()

    def reload(self):
        self.items.clear()
        for choice in reversed(self.service.choices()):
            excerpt = " ".join(choice.description.split())[:60]
            item = QListWidgetItem(f"{'✓ ' if choice.selected else ''}{choice.kind.title()} · {choice.title}\n{excerpt}")
            item.setData(Qt.UserRole, choice)
            self.items.addItem(item)
        self.items.setCurrentRow(0)

    def show_choice(self, item, *_):
        choice = item.data(Qt.UserRole) if item else None
        self.restore_button.setEnabled(bool(choice and not choice.selected))
        self.details.setPlainText((f"{choice.title}\nCreated: {choice.created}\nSource: {choice.source}\n"
                                   f"{'Currently selected' if choice.selected else 'Retained history'}\n\n"
                                   f"{choice.freshness}\n\n"
                                   + choice.description) if choice else "No retained history.")

    def restore_selected(self):
        item = self.items.currentItem()
        if item is None:
            return
        try:
            self.service.restore(item.data(Qt.UserRole))
            self.reload()
            self.status.setText("Restored. Dependent outputs will be rechecked against this selection.")
        except Exception as exc:
            self.status.setText(str(exc))

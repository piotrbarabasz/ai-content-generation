"""Project-local storage report, pin controls, and explicit cleanup preview."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QTextEdit, QVBoxLayout)


def size_text(value):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


class StorageDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Project storage")
        self.resize(800, 580)
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview)
        self.artifacts = QListWidget()
        self.artifacts.setMaximumHeight(120)
        layout.addWidget(self.artifacts)
        pins = QHBoxLayout()
        self.pin_button = QPushButton("Protect selected artifact")
        self.unpin_button = QPushButton("Remove explicit protection")
        self.pin_button.clicked.connect(lambda: self.set_pin(True))
        self.unpin_button.clicked.connect(lambda: self.set_pin(False))
        pins.addWidget(self.pin_button)
        pins.addWidget(self.unpin_button)
        layout.addLayout(pins)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh dry run")
        self.delete_button = QPushButton("Delete previewed files")
        self.resume_button = QPushButton("Resume interrupted cleanup")
        self.refresh_button.clicked.connect(self.refresh)
        self.delete_button.clicked.connect(self.delete_preview)
        self.resume_button.clicked.connect(self.resume)
        for button in (self.refresh_button, self.delete_button, self.resume_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status = QLabel("No files are removed until you explicitly choose deletion or resume.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    def refresh(self):
        try:
            self.report = self.service.analyze()
            self.summary.setText("Project storage: " + size_text(self.report.total_bytes) + "\n" +
                                 "\n".join(f"{name}: {size_text(size)}" for name, size in self.report.categories.items()))
            self.preview.setPlainText("Free up to " + size_text(self.report.reclaimable_bytes) + "\n\nWill remove:\n" +
                ("\n".join(f"• {item.path} ({size_text(item.size_bytes)})" for item in self.report.candidates) or "Nothing eligible.") +
                "\n\nWill keep: active media, retained variants and history, protected items, job inputs, "
                "recent orphans, backups, external caches, models and runtimes.")
            self.artifacts.clear()
            pins = self.service.pins()
            for manifest in self.service.artifacts():
                protected = " · Protected" if manifest.artifact_id in pins else ""
                item = QListWidgetItem(f"{manifest.name} · {manifest.artifact_type} · {size_text(manifest.size_bytes)}{protected}")
                item.setData(Qt.UserRole, manifest.artifact_id)
                self.artifacts.addItem(item)
            self.delete_button.setEnabled(bool(self.report.candidates))
            self.resume_button.setEnabled(bool(self.service.pending_runs()))
        except Exception as exc:
            self.delete_button.setEnabled(False)
            self.resume_button.setEnabled(False)
            self.status.setText(str(exc))

    def set_pin(self, protected):
        item = self.artifacts.currentItem()
        if item is None:
            return
        try:
            (self.service.pin if protected else self.service.unpin)(item.data(Qt.UserRole))
            self.refresh()
        except Exception as exc:
            self.status.setText(str(exc))

    def delete_preview(self):
        try:
            self.service.cleanup(self.report)
            self.refresh()
            self.status.setText("Cleanup complete. Report reflects files currently on disk.")
        except Exception as exc:
            self.status.setText(str(exc))
            self.resume_button.setEnabled(bool(self.service.pending_runs()))

    def resume(self):
        try:
            for run in self.service.pending_runs():
                self.service.resume_cleanup(run)
            self.refresh()
            self.status.setText("Cleanup resumed. Changed or newly protected files were kept.")
        except Exception as exc:
            self.status.setText(str(exc))

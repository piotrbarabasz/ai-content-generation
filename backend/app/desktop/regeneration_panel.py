"""One bounded rebuild operation with explicit outcomes and cancellation."""

import asyncio

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, QLabel


class RegenerationPanel(QWidget):
    busy_changed = Signal(bool)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.services = self.task = self.loop = None
        self.before_start = lambda: None
        layout = QVBoxLayout(self)
        self.outputs = QComboBox()
        layout.addWidget(self.outputs)
        bar = QHBoxLayout()
        layout.addLayout(bar)
        self.rebuild = QPushButton("Rebuild missing / stale")
        self.cancel_button = QPushButton("Cancel rebuild")
        bar.addWidget(self.rebuild)
        bar.addWidget(self.cancel_button)
        self.status = QLabel("Regeneration unavailable.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.rebuild.clicked.connect(self.start)
        self.cancel_button.clicked.connect(self.cancel)
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self.tick)
        self.bind(None)

    @property
    def busy(self):
        return self.task is not None

    def bind(self, services):
        if self.busy:
            raise ValueError("Wait for regeneration cleanup before switching projects.")
        self.services = services
        self.outputs.clear()
        self.outputs.addItem("All configured outputs", None)
        if services is not None:
            for step in services.steps():
                self.outputs.addItem(step.key, step.key)
        self.rebuild.setEnabled(services is not None)
        self.cancel_button.setEnabled(False)

    def start(self):
        if self.busy or self.services is None:
            return
        try:
            self.before_start()
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self.loop = asyncio.new_event_loop()
        self.task = self.loop.create_task(self.services.run(self.outputs.currentData()))
        self.rebuild.setEnabled(False)
        self.outputs.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.busy_changed.emit(True)
        self.timer.start()

    def tick(self):
        self.loop.call_soon(self.loop.stop)
        self.loop.run_forever()
        if not self.task.done():
            self.status.setText(self.services.progress)
            return
        self.timer.stop()
        try:
            outcomes = self.task.result()
            self.status.setText("\n".join(f"{item.key}: {item.status} {item.detail}" for item in outcomes) or "No outputs.")
        except (Exception, asyncio.CancelledError) as exc:
            self.status.setText(str(exc) or "Canceled")
        finally:
            self.task = None
            self.loop.close()
            self.loop = None
            self.rebuild.setEnabled(True)
            self.outputs.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.busy_changed.emit(False)
            self.finished.emit()

    def cancel(self):
        if self.busy:
            if self.services.busy:
                self.services.cancel()
            else:
                self.task.cancel()  # The coroutine has not started on the Qt loop yet.
            self.status.setText("Cancel requested; waiting for cleanup.")

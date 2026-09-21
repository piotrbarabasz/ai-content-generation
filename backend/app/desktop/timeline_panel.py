"""One paired visual/narration layer. All edits are committed through the service."""

from PySide6.QtCore import Signal

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView,
)


class TimelinePanel(QWidget):
    changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.services = self.edit = None
        layout = QVBoxLayout(self)
        self.sources = QComboBox()
        layout.addWidget(self.sources)
        bar = QHBoxLayout()
        layout.addLayout(bar)
        self.buttons = {}
        for label, action in (("Refresh timeline", self.refresh), ("Add selected clip", self.append),
                              ("Move earlier", lambda: self.move(-1)), ("Move later", lambda: self.move(1)),
                              ("Remove clip", self.remove)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, action=action: self.run(action))
            bar.addWidget(button)
            self.buttons[label] = button
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Visual / scene", "Narration artifact", "Offset (s)",
                                             "Duration (s)", "Source samples [start, end)", "Timing"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(lambda: self.run(self.select))
        layout.addWidget(self.table)
        ranges = QHBoxLayout()
        self.start, self.end = QComboBox(), QComboBox()
        for label, widget in (("Source start sample", self.start), ("Source end sample", self.end)):
            ranges.addWidget(QLabel(label))
            ranges.addWidget(widget)
        self.apply = QPushButton("Apply source range")
        self.apply.clicked.connect(lambda: self.run(self.set_range))
        ranges.addWidget(self.apply)
        layout.addLayout(ranges)
        layout.addWidget(QLabel("Moving a clip moves its visual and pinned narration together. "
                                "Offsets follow clip order without gaps. Source cuts use verified sentence boundaries."))
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.bind(None)

    def run(self, action):
        try:
            action()
        except Exception as exc:
            self.status.setText(str(exc))

    def bind(self, services):
        self.services, self.edit = services, None
        self.table.setRowCount(0)
        self.sources.clear()
        for button in self.buttons.values():
            button.setEnabled(services is not None)
        self.apply.setEnabled(False)
        self.status.setText("Timeline service is unavailable." if services is None else "")
        if services is not None:
            self.run(self.refresh)
        else:
            self.changed.emit(None)

    def refresh(self):
        edit = self.services.current()
        candidates = self.services.candidates()
        self.sources.clear()
        used = {c.media.scene_id for c in edit.timeline.clips} if edit else set()
        for label, source in candidates:
            if source.scene_id not in used:
                self.sources.addItem(label, source)
        self.edit = edit
        self.draw()

    def draw(self, row=0):
        clips = self.edit.timeline.clips if self.edit else ()
        self.table.blockSignals(True)
        self.table.setRowCount(len(clips))
        for i, clip in enumerate(clips):
            audio = clip.media.audio
            values = (clip.media.scene_id, f"{audio.artifact_id} ({audio.variant})", str(clip.audio_offset),
                      str(clip.duration), f"{audio.start_sample}, {audio.end_sample}", clip.media.timing_quality)
            for j, value in enumerate(values):
                self.table.setItem(i, j, QTableWidgetItem(value))
        self.table.blockSignals(False)
        if clips:
            self.table.selectRow(row)
        self.status.setText(f"Saved timeline: {len(clips)} clips; "
                            f"duration {self.edit.timeline.duration if self.edit else 0} s.")
        self.select()
        self.changed.emit(self.edit)

    def clip(self):
        row = self.table.currentRow()
        if self.edit is None or row < 0:
            raise ValueError("Select a timeline clip first.")
        return self.edit.timeline.clips[row]

    def select(self):
        self.start.clear()
        self.end.clear()
        self.apply.setEnabled(False)
        if self.edit is None or self.table.currentRow() < 0:
            return
        clip = self.clip()
        boundaries = self.services.media.boundaries(clip.media)
        for widget, value in ((self.start, clip.media.audio.start_sample), (self.end, clip.media.audio.end_sample)):
            for boundary in boundaries:
                widget.addItem(str(boundary), boundary)
            widget.setCurrentIndex(widget.findData(value))
        self.apply.setEnabled(True)

    def append(self):
        source = self.sources.currentData()
        if source is None:
            raise ValueError("No selected media available; accept scenes, time audio and select images first.")
        self.services.append(source, expected=self.edit.id if self.edit else None)
        self.refresh()

    def move(self, delta):
        row = self.table.currentRow() + delta
        edit = self.services.move(self.clip().media.scene_id, row, expected=self.edit.id)
        self.edit = edit
        self.draw(row)

    def set_range(self):
        row = self.table.currentRow()
        self.edit = self.services.set_range(self.clip().media.scene_id, self.start.currentData(),
                                           self.end.currentData(), expected=self.edit.id)
        self.draw(row)

    def remove(self):
        self.services.remove(self.clip().media.scene_id, expected=self.edit.id)
        self.refresh()
